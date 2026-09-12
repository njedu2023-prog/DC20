import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib import error

SPEC = importlib.util.spec_from_file_location("minute_gap_probe", Path(__file__).with_name("minute_gap_probe.py"))
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
TOKEN = "opaque-minute-gap-test-credential-must-not-persist"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def rows_for(case):
    return [{"ts_code": case["ts_code"], "trade_time": stamp, "open": 10, "high": 10,
             "low": 10, "close": 10, "vol": 100, "amount": 1000}
            for stamp in probe.minute.expected_bar_ends(case["trade_date"])]


def table(rows, fields=None):
    fields = list(fields or probe.minute.FIELDS)
    return {"fields": fields, "items": [[row[field] for field in fields] for row in rows]}


def response(data, **envelope):
    return json.dumps({"code": 0, "data": data, **envelope}).encode()


class MinuteGapProbeTests(unittest.TestCase):
    def run_probe(self, call, *, token=TOKEN, clock=None, inspect=None):
        timer = clock or Clock()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            out = base / "new-output"
            result = probe.probe(out, token=token, runner_temp=base, call=call,
                                 clock=timer, sleep=timer.sleep)
            self.assertLessEqual(result["api_calls"], 6)
            self.assertEqual(json.loads((out / "receipt.json").read_text()), result)
            for flag in ("production_writes", "purchase_permission", "credential_persisted",
                         "minute_grid_relaxed", "minute_truth_imported", "training_performed",
                         "settlement_performed", "release_allowed", "source_values_modified"):
                self.assertFalse(result[flag])
            self.assertFalse((out / "data").exists())
            for binding in result["source_files"]:
                raw = (out / binding["path"]).read_bytes()
                self.assertEqual(len(raw), binding["bytes"])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), binding["sha256"])
            for path in out.rglob("*"):
                if path.is_file() and token:
                    self.assertNotIn(token.encode(), path.read_bytes())
            if inspect:
                inspect(result, out)
            return result

    def test_exact_six_cases_dates_fields_rate_and_bounded_contract(self):
        times, observed = [], []
        timer = Clock()
        def call(endpoint, params, fields, token, timeout):
            times.append(timer())
            observed.append((params["start_date"][:10].replace("-", ""), params["ts_code"]))
            self.assertEqual(endpoint, "stk_mins")
            self.assertEqual(fields, probe.minute.FIELDS)
            self.assertEqual((token, timeout), (TOKEN, 20))
            self.assertEqual(params["freq"], "1min")
            self.assertTrue(params["start_date"].endswith(" 09:30:00"))
            self.assertTrue(params["end_date"].endswith(" 15:00:00"))
            case = {"trade_date": observed[-1][0], "ts_code": params["ts_code"]}
            return response(table(rows_for(case)))
        result = self.run_probe(call, clock=timer)
        self.assertEqual(tuple(observed), probe.CASES)
        self.assertEqual(times, list(range(6)))
        self.assertEqual(result["api_calls"], 6)
        self.assertEqual(result["status"], "DIAGNOSTIC_COMPLETE_NOT_SETTLEMENT")
        self.assertTrue(all(r["status"] == "DIAGNOSED_STRICT_ACCEPTED_NOT_IMPORTED" for r in result["requests"]))
        self.assertEqual(probe.contract(), probe._expected_contract())

    def test_table_original_order_values_and_complete_body_sha_preserved(self):
        raw_bodies = []
        def call(endpoint, params, fields, *args):
            case = {"trade_date": params["start_date"][:10].replace("-", ""), "ts_code": params["ts_code"]}
            rows = rows_for(case)[::-1]
            rows[0]["open"] = "10.000"
            raw = response(table(rows, tuple(reversed(fields))), msg=TOKEN, arbitrary_envelope=TOKEN)
            raw_bodies.append(raw)
            return raw
        def inspect(result, out):
            for receipt, raw in zip(result["requests"], raw_bodies):
                self.assertEqual(receipt["http_response_sha256"], hashlib.sha256(raw).hexdigest())
                actual = json.loads((out / receipt["source_files"][0]["path"]).read_bytes())
                self.assertEqual(actual, json.loads(raw)["data"])
                self.assertFalse(receipt["diagnosis"]["source_values_modified"])
        self.run_probe(call, inspect=inspect)

    def diagnose(self, mutate, case=None):
        case = case or probe.contract()["cases"][0]
        rows = rows_for(case)
        mutate(rows)
        original = copy.deepcopy(rows)
        result = probe.diagnose(table(rows), case)
        self.assertEqual(rows, original)
        self.assertFalse(result["source_import_allowed"])
        self.assertFalse(result["settlement_allowed"])
        return result

    def test_240_grid_missing_closing_auction_minutes_not_filled(self):
        result = self.diagnose(lambda rows: rows.__delitem__(slice(-3, None)))
        self.assertEqual(result["row_count"], 237)
        self.assertEqual(result["continuous_unique_minutes"], 237)
        self.assertEqual(result["strict_adapter_error_category"], "INCOMPLETE_240_BAR_SESSION")
        self.assertEqual([s[11:] for s in result["missing_bar_ends"]], ["14:58:00", "14:59:00", "15:00:00"])
        self.assertFalse(result["strict_adapter_accepts"])

    def test_empty_table_remains_missing_not_zero(self):
        result = self.diagnose(lambda rows: rows.clear())
        self.assertEqual(result["row_count"], 0)
        self.assertEqual(len(result["missing_bar_ends"]), 240)
        self.assertIn("EMPTY_TABLE", result["issues"])
        self.assertFalse(result["strict_adapter_accepts"])

    def test_unambiguous_0930_retained_and_excluded_by_strict_adapter(self):
        def mutate(rows):
            auction = dict(rows[0], trade_time=rows[0]["trade_time"].replace("09:31:00", "09:30:00"))
            rows.insert(0, auction)
        result = self.diagnose(mutate)
        self.assertEqual(result["row_count"], 241)
        self.assertEqual(result["auction_0930_rows"], 1)
        self.assertEqual(result["continuous_unique_minutes"], 240)
        self.assertTrue(result["strict_adapter_accepts"])

    def test_0930_ohlc_not_flat_is_not_relabelled_as_0931(self):
        def mutate(rows):
            rows.insert(0, dict(rows[0], trade_time=rows[0]["trade_time"].replace("09:31:00", "09:30:00"), high=11))
        result = self.diagnose(mutate)
        self.assertEqual(result["strict_adapter_error_category"], "AMBIGUOUS_0930_POINT")
        self.assertEqual(result["ambiguous_0930_rows"], 1)
        self.assertIn("AMBIGUOUS_0930_POINT", result["issues"])

    def test_lunch_1300_timestamp_is_not_shifted(self):
        def mutate(rows):
            rows[120]["trade_time"] = rows[120]["trade_time"].replace("13:01:00", "13:00:00")
        result = self.diagnose(mutate)
        self.assertEqual(result["strict_adapter_error_category"], "TIMESTAMP_OUTSIDE_GRID")
        self.assertEqual(result["lunch_timestamps"][0][11:], "13:00:00")
        self.assertEqual(result["missing_bar_ends"][0][11:], "13:01:00")

    def test_duplicate_bar_is_not_deduplicated(self):
        result = self.diagnose(lambda rows: rows.append(dict(rows[1])))
        self.assertEqual(result["strict_adapter_error_category"], "CODE_OR_DUPLICATE_TIMESTAMP")
        self.assertEqual(len(result["duplicate_timestamps"]), 1)
        self.assertEqual(result["row_count"], 241)

    def test_zero_volume_ohlc_or_amount_conflict(self):
        result = self.diagnose(lambda rows: rows[1].update(vol=0, high=11))
        self.assertEqual(result["strict_adapter_error_category"], "ZERO_VOLUME_PRICE_AMOUNT_CONFLICT")
        self.assertEqual(result["zero_volume_conflict_rows"], 1)

    def test_zero_volume_price_changed_is_not_carried_forward(self):
        result = self.diagnose(lambda rows: rows[1].update(open=11, high=11, low=11, close=11, vol=0, amount=0))
        self.assertEqual(result["strict_adapter_error_category"], "ZERO_VOLUME_PRICE_CHANGED")
        self.assertEqual(result["zero_volume_price_changed_rows"], 1)

    def test_invalid_price_ohlc_boolean_and_nonnumeric(self):
        for update, category in (({"close": 0}, "INVALID_PRICE_OR_VOLUME"),
                                 ({"close": 12}, "INCONSISTENT_OHLC"),
                                 ({"close": True}, "BOOLEAN_NUMERIC"),
                                 ({"close": "invalid-price"}, "NONNUMERIC_VALUE")):
            with self.subTest(update=update):
                result = self.diagnose(lambda rows: rows[1].update(update))
                self.assertFalse(result["strict_adapter_accepts"])
                self.assertEqual(result["strict_adapter_error_category"], category)

    def test_wrong_identity_and_date_are_not_rebound(self):
        result = self.diagnose(lambda rows: rows[0].update(ts_code="000001.SZ"))
        self.assertEqual(result["wrong_code_rows"], 1)
        self.assertIn("WRONG_CODE", result["issues"])
        result = self.diagnose(lambda rows: rows[0].update(trade_time="2022-11-23 09:31:00"))
        self.assertEqual(result["wrong_date_timestamps"], 1)
        self.assertIn("WRONG_DATE", result["issues"])

    def test_malformed_fields_and_short_rows_retained_as_diagnostics(self):
        case = probe.contract()["cases"][0]
        data = table(rows_for(case))
        data["fields"][0] = "other_code"
        result = probe.diagnose(data, case)
        self.assertEqual(result["strict_adapter_error_category"], "FIELD_SCHEMA")
        data = table(rows_for(case))
        data["items"][0].pop()
        result = probe.diagnose(data, case)
        self.assertEqual(result["strict_adapter_error_category"], "ROW_SHAPE")
        self.assertFalse(result["strict_adapter_accepts"])

    def test_pagination_and_count_mismatch_are_not_accepted_source_contracts(self):
        for extra in ({"has_more": True}, {"count": 241}):
            with self.subTest(extra=extra):
                def call(endpoint, params, fields, *args):
                    case = {"trade_date": params["start_date"][:10].replace("-", ""), "ts_code": params["ts_code"]}
                    return response({**table(rows_for(case)), **extra})
                result = self.run_probe(call)
                for receipt in result["requests"]:
                    self.assertEqual(receipt["status"], "DIAGNOSED_STRICT_REJECTED_NOT_IMPUTED")
                    self.assertTrue(receipt["diagnosis"]["strict_adapter_accepts"])
                    self.assertFalse(receipt["diagnosis"]["table_contract_valid"])
                    self.assertFalse(receipt["diagnosis"]["source_contract_accepts"])
                    self.assertIn("PAGINATION_OR_COUNT_MISMATCH", receipt["diagnosis"]["issues"])

    def test_unknown_exception_text_never_persisted(self):
        with mock.patch.object(probe.minute, "normalize_source_rows", side_effect=ValueError(TOKEN)):
            result = self.diagnose(lambda rows: None)
        self.assertEqual(result["strict_adapter_error_category"], "UNCLASSIFIED_ADAPTER_REJECTION")
        self.assertIsNone(result["strict_adapter_error"])
        self.assertNotIn(TOKEN, json.dumps(result))

    def test_rejected_original_table_is_saved_without_import(self):
        result = self.run_probe(lambda endpoint, params, fields, *args: response({"fields": list(fields), "items": []}))
        self.assertTrue(all(r["status"] == "DIAGNOSED_STRICT_REJECTED_NOT_IMPUTED" for r in result["requests"]))
        self.assertTrue(all(len(r["source_files"]) == 2 for r in result["requests"]))

    def test_decoded_credential_echo_in_table_is_not_persisted(self):
        def call(endpoint, params, fields, *args):
            data = {"fields": list(fields), "items": [[TOKEN, "invalid-time", 10, 10, 10, 10, 1, 10]]}
            return response(data).replace(TOKEN.encode(), b"\\u006f" + TOKEN[1:].encode())
        result = self.run_probe(call)
        self.assertTrue(all(r["status"] == "CREDENTIAL_LIKE_DATA_NOT_PERSISTED" for r in result["requests"]))
        self.assertTrue(all(r["source_files"] == [] for r in result["requests"]))

    def test_credential_absent_sends_nothing(self):
        def fail(*args):
            self.fail("no credential must not send")
        result = self.run_probe(fail, token="")
        self.assertEqual(result["api_calls"], 0)

    def test_denied_credential_or_rate_limit_stops_remaining_calls(self):
        for message, category in (("没有权限 " + TOKEN, "ENTITLEMENT_DENIED"),
                                   ("token rejected " + TOKEN, "CREDENTIAL_REJECTED"),
                                   ("每分钟频率限制 " + TOKEN, "RATE_LIMITED")):
            with self.subTest(category=category):
                result = self.run_probe(lambda *args: json.dumps({"code": -1, "msg": message}).encode())
                self.assertEqual(result["api_calls"], 1)
                self.assertEqual(result["requests"][0]["status"], category)
                self.assertEqual(result["requests"][1]["status"], "SKIPPED_AFTER_" + category)

    def test_network_http_and_malformed_responses_do_not_leak_or_retry(self):
        def network(*args):
            raise RuntimeError(TOKEN)
        def http(*args):
            raise error.HTTPError("https://api.tushare.pro", 503, TOKEN, {}, None)
        for call, status in ((network, "NETWORK_OR_RESPONSE_ERROR"), (http, "HTTP_ERROR"),
                             (lambda *args: b"bad json " + TOKEN.encode(), "INVALID_RESPONSE_NOT_INTERPRETED")):
            with self.subTest(status=status):
                result = self.run_probe(call)
                self.assertEqual(result["api_calls"], 6)
                self.assertTrue(all(r["status"] == status for r in result["requests"]))

    def test_wall_budget_oversleep_and_slow_calls_stop_safely(self):
        timer = Clock()
        def call(endpoint, params, fields, *args):
            timer.now += 100
            return response({"fields": list(fields), "items": []})
        result = self.run_probe(call, clock=timer)
        self.assertEqual(result["api_calls"], 2)
        self.assertEqual(result["requests"][2]["status"], "SKIPPED_BUDGET_EXHAUSTED")

    def test_unsafe_data_structures_and_large_responses_are_not_retained(self):
        for data in ({"fields": ["msg"], "items": [[{"nested": TOKEN}]]},
                     {"fields": ["msg"], "items": [["x" * 129]]},
                     {"fields": [], "items": [], "server_message": TOKEN}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                probe._safe_data({"data": data})
        result = self.run_probe(lambda *args: b"x" * (probe.safe.MAX_RESPONSE_BYTES + 1))
        self.assertTrue(all(r["status"] == "INVALID_RESPONSE_NOT_INTERPRETED" for r in result["requests"]))

    def test_output_requires_fresh_runner_temp_and_no_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            probe.safe._fresh_output(base / "fresh", base)
            with self.assertRaises(FileExistsError):
                probe.probe(base / "fresh", token=TOKEN, runner_temp=base, call=lambda *args: self.fail("network"))
            with self.assertRaises(ValueError):
                probe.safe._fresh_output(base.parent / "outside", base)
            link = base / "alias"
            link.symlink_to(base / "fresh", target_is_directory=True)
            with self.assertRaises(ValueError):
                probe.safe._fresh_output(link / "new", base)
            with self.assertRaises(ValueError):
                probe.safe._fresh_output(probe.ROOT / "not-allowed", probe.ROOT)

    def test_request_and_adapter_sha_tampering_block_before_network(self):
        original = Path.read_bytes
        for target in (probe.HERE / "MINUTE_GAP_REQUEST.json", probe.ADAPTER_PATH):
            def modified(path, *, target=target):
                raw = original(path)
                if path != target:
                    return raw
                if path == probe.ADAPTER_PATH:
                    return raw + b"# changed"
                value = json.loads(raw)
                value["max_api_calls"] = 7
                return json.dumps(value).encode()
            with self.subTest(target=target), mock.patch.object(Path, "read_bytes", modified), self.assertRaises(ValueError):
                probe.contract()

    def test_workflow_is_single_request_trigger_read_only_and_pinned(self):
        workflow = (probe.ROOT / ".github/workflows/research_profit_1000_minute_gap.yml").read_text()
        self.assertIn('"work/profit_1000_upgrade/MINUTE_GAP_REQUEST.json"', workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("github.run_attempt == 1", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("--output \"${RUNNER_TEMP}/dc20-profit-1000-minute-gap\"", workflow)
        self.assertIn("test_minute_gap_probe.py", workflow)
        self.assertEqual(workflow.count("secrets.TUSHARE_TOKEN"), 1)
        for forbidden in ("contents: write", "actions: write", "workflow_dispatch", "git push", "pip install", "download-artifact"):
            self.assertNotIn(forbidden, workflow)
        for line in workflow.splitlines():
            if "uses:" in line:
                self.assertRegex(line, r"@[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
