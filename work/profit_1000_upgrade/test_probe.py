import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("probe", Path(__file__).with_name("probe.py"))
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class ProbeTests(unittest.TestCase):
    def run_probe(self, call, token="opaque-test-credential"):
        with tempfile.TemporaryDirectory() as base:
            path = Path(base).resolve() / "evidence"
            result = probe.probe(path, token=token, call=call)
            for file in path.rglob("*"):
                if file.is_file() and token:
                    self.assertNotIn(token.encode(), file.read_bytes())
            self.assertEqual(json.loads((path / "receipt.json").read_text()), result)
            return result

    def test_missing_credential_no_network(self):
        def fail(*args):
            self.fail("no credential must not send")
        result = self.run_probe(fail, token="")
        self.assertEqual(result["api_calls"], 0)
        self.assertEqual(result["probes"][0]["status"], "CREDENTIAL_ABSENT")

    def test_permission_failure_safe_and_stops(self):
        def denied(*args):
            return {"code": -2002, "msg": "没有权限 opaque-test-credential"}
        result = self.run_probe(denied)
        self.assertEqual(result["api_calls"], 1)
        self.assertEqual(result["probes"][0]["status"], "ENTITLEMENT_DENIED")
        self.assertEqual(result["probes"][1]["status"], "SKIPPED_AFTER_ACCESS_FAILURE")

    def test_partial_minutes_not_accepted(self):
        result = self.run_probe(lambda *a: {"code": 0, "data": {"fields": [], "items": []}})
        self.assertEqual(result["api_calls"], 2)
        self.assertFalse(result["source_files"])
        self.assertEqual(result["probes"][0]["status"], "INCOMPLETE_OR_INVALID_MINUTE_TRUTH")

    def test_network_exception_never_leaks(self):
        def failure(*a):
            raise RuntimeError("opaque-test-credential")
        result = self.run_probe(failure)
        self.assertEqual(result["probes"][0]["status"], "NETWORK_OR_RESPONSE_ERROR")

    def test_complete_source_is_paired_and_bounded(self):
        _, adapter = probe.load_contract()
        def success(params, fields, token, timeout):
            self.assertEqual(timeout, 20)
            day = params["start_date"][:10].replace("-", "")
            rows = [{"ts_code": params["ts_code"], "trade_time": stamp,
                     "open": 10, "high": 10, "low": 10, "close": 10, "vol": 100, "amount": 1000}
                    for stamp in adapter.expected_bar_ends(day)]
            return {"code": 0, "data": {"fields": list(fields),
                                         "items": [[r[k] for k in fields] for r in rows]}}
        result = self.run_probe(success)
        self.assertEqual(result["api_calls"], 2)
        self.assertEqual(len(result["source_files"]), 4)
        self.assertEqual(result["status"], "MINUTE_ACCESS_VERIFIED_NOT_HISTORY_COMPLETE")
        self.assertFalse(result["trained"])

    def test_checkout_output_refused(self):
        with self.assertRaisesRegex(ValueError, "outside checkout"):
            probe.probe(probe.ROOT / "work/profit_1000_upgrade/unsafe-output", token="")

    def test_classification_sanitizes_unknown_errors(self):
        self.assertEqual(probe.classify({"code": -1, "msg": "opaque-test-credential"}), "API_REJECTED")
        self.assertEqual(probe.classify({"code": "0"}), "INVALID_RESPONSE")


if __name__ == "__main__":
    unittest.main()
