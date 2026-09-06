"""No-network fixtures for the nineteen-partition tail contract."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("tail", Path(__file__).with_name("collect_tail.py"))
tail = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tail)


class Clock:
    def __init__(self):
        self.value = 0.
        self.sleeps = []

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


def values(api, date, code):
    if api == "daily":
        return [code, date, 10., 11., 9., 10.5, 10., .5, 5., 100., 1000.]
    return [code, date, 10., 11., 9.]


class TailCollectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = json.loads((tail.HERE / "PLAN.json").read_text())
        cls.request = tail.build_request(tail.REPO, cls.plan)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dc20-tail-fixture.")
        self.output = Path(self.temp.name).resolve()
        self.artifacts = tail.SOURCE.Artifacts(self.output)
        self.clock = Clock()
        self.calls = []
        self.scope = {(p["trade_date"], p["api"]): p["codes"] for p in self.request["partitions"]}

    def tearDown(self):
        self.temp.cleanup()

    def transport(self, body):
        query = json.loads(body)
        self.calls.append(query)
        api, params = query["api_name"], query["params"]
        codes = [params["ts_code"]] if "ts_code" in params else self.scope[(params["trade_date"], api)][params["offset"]:]
        return tail.SOURCE.canonical({"code": 0, "msg": "", "data": {"fields": tail.SOURCE.FIELDS[api],
                    "items": [values(api, params["trade_date"], code) for code in codes]}})

    def client(self, transport=None):
        return tail.TailClient("fixture-token-not-real", self.artifacts, self.request, transport=transport or self.transport,
                               clock=self.clock, sleep=self.clock.sleep,
                               environment={"GITHUB_RUN_ID": "123", "GITHUB_SHA": "a" * 40, "GITHUB_RUN_ATTEMPT": "1"})

    def run_tail(self, transport=None):
        client = self.client(transport)
        with contextlib.redirect_stdout(io.StringIO()):
            result, status = tail.perform_tail(client, self.request, self.artifacts, {"previous_source": tail.PREVIOUS_SOURCE})
        return result, status, client

    def test_exact_scope_recomputed_from_pinned_ledger(self):
        compact = tail.request_contract(self.request)
        self.assertEqual(len(self.request["partitions"]), 19)
        self.assertEqual(compact["candidate_partition_keys"], 1533)
        self.assertEqual(compact["partition_plan_sha256"], "8861ba29a3fbde045302368d30e46b99840e1ebd9dc8f96711664464ec8c4b83")
        self.assertNotIn(("20260824", "daily"), self.scope)
        self.assertEqual(self.request["canaries"], [{"api": "daily", "trade_date": "20260825", "ts_code": "000009.SZ"},
                                                   {"api": "stk_limit", "trade_date": "20260824", "ts_code": "000009.SZ"}])
        tail.verify_request(json.loads((tail.HERE / "REQUEST.json").read_text()), self.request)

    def test_plan_scope_and_previous_evidence_cannot_drift(self):
        for key, replacement in (("as_of_date", "20260905"), ("max_requests", 79), ("optional_adj_factor", True), ("missing_partitions", [])):
            plan = copy.deepcopy(self.plan)
            plan[key] = replacement
            with self.subTest(key=key), self.assertRaises(tail.SOURCE.CollectionError):
                tail.build_request(tail.REPO, plan)
        request = tail.request_contract(self.request)
        request["partition_plan_sha256"] = "0" * 64
        with self.assertRaises(tail.SOURCE.CollectionError):
            tail.verify_request(request, self.request)

    def test_original_collector_bytes_required(self):
        with patch.object(Path, "read_bytes", return_value=b"modified"):
            with self.assertRaisesRegex(ValueError, "SHA_MISMATCH"):
                tail.load_source(tail.REPO)

    def test_complete_tail_is_new_evidence_not_old_run_success(self):
        result, status, client = self.run_tail()
        self.assertEqual(result, 0)
        self.assertEqual((client.count, client.bulk_count, client.canary_count), (21, 19, 2))
        self.assertEqual(status["status"], "COLLECTED_TAIL_REQUIRED_PARTITIONS")
        self.assertTrue(status["required_collection_complete"])
        self.assertTrue(status["required_candidate_coverage_complete"])
        self.assertFalse(status["training_authorized"])
        self.assertFalse(status["previous_status_rewritten"])
        self.assertEqual(status["provenance"]["previous_source"]["collection_status"], "BLOCKED_COLLECTION")
        self.assertEqual(len(list((self.output / "candidate_sources").glob("*/*.csv"))), 19)
        self.assertFalse((self.output / "candidate_sources/20260824/daily.csv").exists())
        manifest = json.loads((self.output / "artifact_manifest.json").read_text())
        self.assertEqual(manifest["artifact_role"], "required_partition_tail")
        self.assertEqual(manifest["github_run_id"], "123")
        self.assertNotIn("artifact_manifest.json", manifest["files"])
        for name, info in manifest["files"].items():
            self.assertEqual(tail.SOURCE.sha(self.output / name), info["sha256"])
            self.assertEqual((self.output / name).stat().st_size, info["bytes"])
        self.assertTrue(all(s >= .5 for s in self.clock.sleeps))
        self.assertNotIn(b"fixture-token-not-real", b"".join(p.read_bytes() for p in self.output.rglob("*") if p.is_file()))

    def test_scope_blocks_old_date_completed_api_optional_and_changed_codes(self):
        client = self.client()
        for api, date, wanted in (("daily", "20260824", {"000009.SZ"}), ("daily", "20221114", {"000009.SZ"}),
                                  ("adj_factor", "20260825", {"000009.SZ"}), ("daily", "20260825", {"000009.SZ"})):
            with self.subTest(api=api, date=date), self.assertRaises(tail.SOURCE.CollectionError):
                client.query(api, {"trade_date": date, "offset": 0, "limit": 5000}, wanted)
        self.assertEqual(self.calls, [])

    def test_non_null_canary_required_and_no_retry(self):
        def null_canary(body):
            response = json.loads(self.transport(body))
            response["data"]["items"][0][2] = None
            return tail.SOURCE.canonical(response)
        result, status, client = self.run_tail(null_canary)
        self.assertEqual(result, 2)
        self.assertEqual(client.count, 1)
        self.assertEqual(status["status"], "BLOCKED_TAIL_COLLECTION")
        self.assertEqual(status["completed_partitions"], 0)
        self.assertEqual(status["failure_code"], "CANDIDATE_NUMERIC_EVIDENCE_MISSING")

    def test_bulk_json_null_stays_unknown_empty_csv(self):
        def null_bulk(body):
            query = json.loads(body)
            response = json.loads(self.transport(body))
            if "ts_code" not in query["params"]:
                response["data"]["items"][0][2] = None
            return tail.SOURCE.canonical(response)
        result, status, _ = self.run_tail(null_bulk)
        self.assertEqual(result, 0)
        self.assertEqual(status["status"], "COLLECTED_TAIL_REQUIRED_PARTITIONS_WITH_GAPS")
        self.assertFalse(status["required_candidate_coverage_complete"])
        self.assertTrue(status["required_collection_complete"])
        self.assertEqual(len(status["coverage"][0]["info"]["incomplete_candidate_fields"]), 1)
        self.assertIn("000009.SZ,20260824,,", (self.output / "candidate_sources/20260824/stk_limit.csv").read_text())

    def test_missing_row_requires_empty_page_not_short_page_assumption(self):
        def missing(body):
            query = json.loads(body)
            response = json.loads(self.transport(body))
            if "ts_code" not in query["params"]:
                response["data"]["items"] = response["data"]["items"][1:]
            return tail.SOURCE.canonical(response)
        result, status, client = self.run_tail(missing)
        self.assertEqual(result, 0)
        self.assertEqual(client.bulk_count, 38)
        self.assertTrue(all(c["info"]["pagination_termination"] == "EMPTY_PAGE_EXHAUSTED_WITH_MISSING_CANDIDATES" for c in status["coverage"]))
        self.assertFalse(status["required_candidate_coverage_complete"])

    def test_malformed_bulk_field_stops_remaining_dates(self):
        def malformed(body):
            query = json.loads(body)
            response = json.loads(self.transport(body))
            if "ts_code" not in query["params"]:
                response["data"]["items"][0][2] = True
            return tail.SOURCE.canonical(response)
        result, status, client = self.run_tail(malformed)
        self.assertEqual((result, client.count), (2, 3))
        self.assertFalse(status["required_collection_complete"])

    def test_network_rejection_is_one_attempt_and_preserves_manifest(self):
        def failed(body):
            self.calls.append(json.loads(body))
            raise tail.SOURCE.CollectionError("HTTPS_TRANSPORT_FAILURE")
        result, status, client = self.run_tail(failed)
        self.assertEqual((result, client.count, len(self.calls)), (2, 1, 1))
        self.assertEqual(status["failure_code"], "HTTPS_TRANSPORT_FAILURE")
        self.assertTrue((self.output / "artifact_manifest.json").is_file())

    def test_total_and_bulk_request_hard_caps_before_network(self):
        for attr, limit in (("count", 78), ("bulk_count", 76)):
            client = self.client()
            setattr(client, attr, limit)
            partition = self.request["partitions"][0]
            with self.subTest(attr=attr), self.assertRaises(tail.SOURCE.CollectionError):
                client.query(partition["api"], {"trade_date": partition["trade_date"], "offset": 0, "limit": 5000}, partition["codes"])
        self.assertEqual(self.calls, [])

    def test_deadline_checked_after_pacing_sleep(self):
        client = self.client()
        self.clock.value = 599.9
        client.last_start = 599.8
        partition = self.request["partitions"][0]
        with self.assertRaisesRegex(tail.SOURCE.CollectionError, "TAIL_SOFT_DEADLINE_EXCEEDED"):
            client.query(partition["api"], {"trade_date": partition["trade_date"], "offset": 0, "limit": 5000}, partition["codes"])
        self.assertEqual(self.calls, [])
        self.assertEqual(json.loads((self.output / "receipts/000001.json").read_text())["status"], "FAILED")

    def test_canary_cannot_be_repeated(self):
        client = self.client()
        c = self.request["canaries"][0]
        params = {"trade_date": c["trade_date"], "ts_code": c["ts_code"], "offset": 0, "limit": 5000}
        client.query(c["api"], params, {c["ts_code"]}, canary=True)
        with self.assertRaises(tail.SOURCE.CollectionError):
            client.query(c["api"], params, {c["ts_code"]}, canary=True)

    def test_output_must_be_new_fixed_runner_temp_and_local_main_refused(self):
        env = {"RUNNER_TEMP": str(self.output)}
        target = self.output / tail.OUTPUT_SUBDIR
        self.assertEqual(tail.prepare_output(str(target), env), target)
        with self.assertRaises(tail.SOURCE.CollectionError):
            tail.prepare_output(str(target), env)
        with self.assertRaises(tail.SOURCE.CollectionError):
            tail.prepare_output(str(self.output / "elsewhere"), env)
        with patch.dict(os.environ, {}, clear=True), patch.object(sys := tail.sys, "argv", ["collect_tail.py", "--output", str(target)]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tail.main(), 2)


if __name__ == "__main__":
    unittest.main()
