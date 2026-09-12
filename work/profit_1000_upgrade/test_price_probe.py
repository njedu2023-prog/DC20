import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("price_probe", Path(__file__).with_name("price_probe.py"))
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
TOKEN = "opaque-price-test-credential-do-not-persist"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def response(fields, row=None, **envelope):
    return json.dumps({"code": 0, "data": {"fields": list(fields), "items": [[row[k] for k in fields]] if row else []}, **envelope}).encode()


def source(endpoint, params):
    return {**params, "open": 6.98, "high": 7.46, "low": 6.55,
            "close": 6.81 if endpoint == "stk_auction_o" else 7.18,
            "price": 6.98, "pre_close": 6.78, "vol": 6478400, "amount": 44992445.44, "vwap": 6.95}


def successful(endpoint, params, fields, *args):
    return response(fields, source(endpoint, params))


class PriceProbeTests(unittest.TestCase):
    def run_probe(self, call=successful, *, token=TOKEN, clock=None, inspect=None):
        timer = clock or Clock()
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            output = base / "fresh"
            result = probe.probe(output, token=token, runner_temp=base, call=call,
                                 clock=timer, sleep=timer.sleep)
            self.assertLessEqual(result["api_calls"], 12)
            self.assertEqual(json.loads((output / "receipt.json").read_text()), result)
            for flag in ("entry_price_selected", "settlement_performed", "release_allowed", "production_writes"):
                self.assertFalse(result[flag])
            for binding in result["source_files"]:
                raw = (output / binding["path"]).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), binding["sha256"])
                self.assertEqual(len(raw), binding["bytes"])
                if token:
                    self.assertNotIn(token.encode(), raw)
            if inspect:
                inspect(result, output)
            return result

    def test_exact_twelve_requests_no_extra_endpoint_or_dates(self):
        observed, timer = [], Clock()
        def call(endpoint, params, fields, token, timeout):
            observed.append((endpoint, params, fields, timer()))
            self.assertEqual(timeout, 20)
            return successful(endpoint, params, fields)
        result = self.run_probe(call, clock=timer)
        self.assertEqual(result["api_calls"], 12)
        self.assertEqual([(e, p["ts_code"], p["trade_date"]) for e, p, f, t in observed],
                         [(e, code, date) for code, date in probe.CASES for e in probe.FIELDS])
        self.assertEqual([t for e, p, f, t in observed], list(range(12)))
        self.assertEqual(result["status"], "DIAGNOSTIC_COMPLETE_NOT_SETTLEMENT")

    def test_conflicting_close_reported_not_selected(self):
        result = self.run_probe()
        for case in result["cases"]:
            self.assertTrue(case["auction_o_open_matches_daily_open_at_cent"])
            self.assertFalse(case["auction_o_close_matches_daily_open_at_cent"])
            self.assertTrue(case["auction_price_matches_daily_open_at_cent"])
            self.assertFalse(case["entry_price_selected"])

    def test_missing_older_auction_is_absent_not_reconstructed(self):
        def call(endpoint, params, fields, *args):
            return response(fields) if endpoint == "stk_auction" and params["trade_date"] < "20250101" else successful(endpoint, params, fields)
        result = self.run_probe(call)
        self.assertIsNone(result["cases"][0]["auction_price"])
        self.assertIsNone(result["cases"][0]["auction_price_matches_daily_open_at_cent"])

    def test_out_of_range_decimal_comparison_remains_unknown(self):
        def call(endpoint, params, fields, *args):
            row = source(endpoint, params)
            if endpoint == "stk_auction":
                row["price"] = 1e30
            return response(fields, row)
        result = self.run_probe(call)
        self.assertTrue(all(c["auction_price_matches_daily_open_at_cent"] is None for c in result["cases"]))

    def test_original_values_kept_but_messages_not_saved(self):
        originals = []
        def call(endpoint, params, fields, *args):
            row = source(endpoint, params)
            row["open"] = "6.980"
            raw = response(fields, row, msg=TOKEN, request_id=TOKEN)
            originals.append(raw)
            return raw
        def inspect(result, output):
            for receipt, raw in zip(result["requests"], originals):
                self.assertEqual(receipt["http_response_sha256"], hashlib.sha256(raw).hexdigest())
                data = json.loads((output / receipt["source_files"][0]["path"]).read_bytes())
                self.assertEqual(data, json.loads(raw)["data"])
        self.run_probe(call, inspect=inspect)

    def test_no_credentials_no_calls(self):
        self.assertEqual(self.run_probe(lambda *args: self.fail("unexpected call"), token="")["api_calls"], 0)

    def test_endpoint_denied_stops_only_endpoint(self):
        def call(endpoint, params, fields, *args):
            return json.dumps({"code": -1, "msg": "没有权限 " + TOKEN}).encode() if endpoint == "stk_auction" else successful(endpoint, params, fields)
        result = self.run_probe(call)
        self.assertEqual(result["api_calls"], 9)
        self.assertEqual(result["status_counts"]["SKIPPED_AFTER_ENDPOINT_ENTITLEMENT_FAILURE"], 3)

    def test_rejected_credential_stops_all(self):
        result = self.run_probe(lambda *args: json.dumps({"code": -1, "msg": "token无效"}).encode())
        self.assertEqual(result["api_calls"], 1)

    def test_time_budget_never_exceeded(self):
        timer = Clock()
        def call(*args):
            timer.sleep(295)
            return successful(*args)
        result = self.run_probe(call, clock=timer)
        self.assertEqual(result["api_calls"], 1)

    def test_invalid_response_never_becomes_observation(self):
        for mutation in (lambda r: r.update(ts_code="600000.SH"),
                         lambda r: r.update(trade_date="20260912"),
                         lambda r: r.update(vol=float("nan")),
                         lambda r: r.update(amount=True),
                         lambda r: r.update(amount=TOKEN)):
            with self.subTest(mutation=mutation):
                def call(endpoint, params, fields, *args):
                    row = source(endpoint, params)
                    mutation(row)
                    return response(fields, row)
                result = self.run_probe(call)
                self.assertEqual(result["status_counts"], {"INVALID_RESPONSE_NOT_INTERPRETED": 12})
                self.assertTrue(all(c["daily_open"] is None for c in result["cases"]))

    def test_truncated_or_duplicate_rows_rejected(self):
        for change in (lambda d: d.update(has_more=True), lambda d: d.update(count=2),
                       lambda d: d["items"].extend(d["items"]),
                       lambda d: d["fields"].append(d["fields"][0])):
            with self.subTest(change=change):
                def call(endpoint, params, fields, *args):
                    payload = json.loads(successful(endpoint, params, fields))
                    change(payload["data"])
                    return json.dumps(payload).encode()
                self.assertEqual(self.run_probe(call)["status_counts"], {"INVALID_RESPONSE_NOT_INTERPRETED": 12})

    def test_cannot_overwrite_existing_or_aliased_output(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            existing = base / "existing"
            existing.mkdir()
            alias = base / "alias"
            alias.symlink_to(existing, target_is_directory=True)
            for path in (existing, alias / "fresh", probe.HERE / "forbidden"):
                with self.subTest(path=path), self.assertRaises((ValueError, FileExistsError)):
                    probe.probe(path, token=TOKEN, runner_temp=base, call=successful)

    def test_http_errors_and_oversize_are_not_price_evidence(self):
        def bad(*args):
            raise probe.error.HTTPError("https://api.tushare.pro", 503, TOKEN, None, None)
        result = self.run_probe(bad)
        self.assertEqual(result["status_counts"], {"HTTP_ERROR": 12})
        with mock.patch.object(probe, "MAX_RESPONSE_BYTES", 5):
            self.assertEqual(self.run_probe()["status_counts"], {"INVALID_RESPONSE_NOT_INTERPRETED": 12})


if __name__ == "__main__":
    unittest.main()
