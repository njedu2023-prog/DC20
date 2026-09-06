"""Offline stdlib tests. No credential, market call, model fit, or production IO."""
import contextlib
import copy
import csv
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location("dc20_history_source_collector_20260906_tests", Path(__file__).with_name("collect.py"))
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)
TOKEN = "TEST_ONLY_NOT_A_REAL_CREDENTIAL_20260906"
DATE, CODE = "20221114", "603778.SH"


def row(api, code=CODE, date=DATE):
    values = {"ts_code": code, "trade_date": date, "open": 10., "high": 11., "low": 9., "close": 10., "pre_close": 10., "change": 0., "pct_chg": 0., "vol": 100., "amount": 1000., "up_limit": 11., "down_limit": 9., "adj_factor": 1.}
    return {key: values[key] for key in C.FIELDS[api]}


def response(api, rows=None, **extra):
    rows = [row(api)] if rows is None else rows
    value = {"code": 0, "msg": "", "data": {"fields": C.FIELDS[api], "items": [[item[field] for field in C.FIELDS[api]] for item in rows]}}
    value.update(extra)
    return json.dumps(value).encode()


def params(**extra):
    return {"trade_date": DATE, "limit": C.PAGE_SIZE, "offset": 0, **extra}


class Clock:
    def __init__(self):
        self.now, self.sleeps = 0., []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dc20-collector-test-")
        self.root = Path(self.temp.name).resolve()
        self.output = self.root / "artifact"
        self.output.mkdir()
        self.artifacts = C.Artifacts(self.output)
        self.clock = Clock()
        self.transport = mock.Mock(return_value=response("daily"))
        self.client = C.Client(TOKEN, self.artifacts, transport=self.transport, clock=self.clock, sleep=self.clock.sleep, environment={})

    def tearDown(self):
        self.temp.cleanup()

    def fail_code(self, code, fn, *args, **kwargs):
        with self.assertRaises(C.CollectionError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def artifact(self, name):
        return json.loads((self.output / name).read_text())

    def assert_secret_absent(self):
        for path in self.output.rglob("*"):
            if path.is_file():
                data = path.read_bytes()
                if path.suffix == ".gz":
                    data = gzip.decompress(data)
                self.assertNotIn(TOKEN.encode(), data, str(path))


class ContractTests(Base):
    def test_fixed_real_plan_and_source_request(self):
        plan = json.loads(C.HERE.joinpath("PLAN.json").read_text())
        request = C.build_request(C.repository(), plan)
        self.assertEqual(request["canary"], {"trade_date": DATE, "ts_code": CODE})
        self.assertLessEqual(max(s["trade_date"] for s in request["sessions"]), C.ASOF)
        self.assertGreater(len(request["sessions"]), 900)
        for entry in request["sessions"]:
            self.assertEqual(entry["codes"], sorted(set(entry["codes"])))
        compact = C.request_contract(request)
        self.assertNotIn("sessions", compact)
        self.assertEqual(compact["session_plan_sha256"], C.sha_bytes(C.canonical(request["sessions"])))
        self.assertEqual(compact["candidate_date_keys"], sum(len(s["codes"]) for s in request["sessions"]))
        self.assertLess(len(C.canonical(compact)), 2000)
        C.verify_request({**compact, "_activation": {"installation_commit": "a" * 40}}, request)
        self.fail_code("REQUEST_DIFFERS_FROM_FIXED_LEDGER_CALENDAR_WINDOW", C.verify_request, {**compact, "unrecognized": True}, request)
        self.fail_code("REQUEST_DIFFERS_FROM_FIXED_LEDGER_CALENDAR_WINDOW", C.verify_request, {**compact, "session_plan_sha256": "0" * 64}, request)
        self.fail_code("REQUEST_DIFFERS_FROM_FIXED_LEDGER_CALENDAR_WINDOW", C.verify_request, request, request)

    def test_plan_cannot_change_mode_window_pins_budget(self):
        plan = json.loads(C.HERE.joinpath("PLAN.json").read_text())
        for key, value in [("daily_mode", "candidate_batches"), ("as_of_date", "20260907"), ("tail_sessions", 19), ("max_requests", 99999)]:
            self.fail_code("PLAN_FETCH_CONTRACT_MISMATCH", C.validate_plan, {**plan, key: value})
        bad = copy.deepcopy(plan)
        bad["source_inputs"]["ledger"]["sha256"] = "0" * 64
        self.fail_code("PLAN_SOURCE_PINS_MISMATCH", C.validate_plan, bad)
        self.fail_code("BATCH_CODE_MODE_FORBIDDEN", C.validate_plan, {**plan, "daily_batch_size": 100})
        self.fail_code("PLAN_SOURCE_IDENTITY_MISMATCH", C.validate_plan, {**plan, "source_commit": "0" * 40})

    def test_source_pin_and_input_symlink_fail_before_queries(self):
        plan = json.loads(C.HERE.joinpath("PLAN.json").read_text())
        with mock.patch.object(C, "sha", return_value="0" * 64):
            self.fail_code("SOURCE_PIN_MISMATCH", C.build_request, C.repository(), plan)
        (self.root / "input.csv").write_text("test")
        (self.root / "alias.csv").symlink_to(self.root / "input.csv")
        self.fail_code("SYMLINK_PATH_FORBIDDEN", C.safe_input, self.root, "alias.csv")
        self.fail_code("UNSAFE_INPUT_PATH", C.safe_input, self.root, "../secret")
        self.transport.assert_not_called()


class OutputTests(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.root / "repo"
        self.repo.mkdir()

    def test_empty_external_and_fixed_runner_outputs(self):
        target = self.root / "new-external-temp"
        self.assertEqual(C.prepare_output(self.repo, str(target), environment={}), target)
        other = self.root / "already-empty"
        other.mkdir()
        self.assertEqual(C.prepare_output(self.repo, str(other), environment={}), other)
        runner = self.root / "runner"
        runner.mkdir()
        result = C.prepare_output(self.repo, environment={"RUNNER_TEMP": str(runner)})
        self.assertEqual(result, runner / C.OUTPUT_SUBDIR)
        self.fail_code("EXISTING_RUNNER_OUTPUT_FORBIDDEN", C.prepare_output, self.repo, environment={"RUNNER_TEMP": str(runner)})

    def test_reject_repo_nonempty_relative_broad_and_symlink(self):
        self.fail_code("OUTPUT_MUST_BE_OUTSIDE_REPOSITORY", C.prepare_output, self.repo, str(self.repo / "outputs"), environment={})
        # Linux TemporaryDirectory is commonly /tmp/name (three parts), which
        # is correctly rejected by the earlier broad-root check. Use an
        # explicitly deeper ancestor to isolate the repository ancestry guard.
        ancestor = self.root / "ancestor"
        nested_repo = ancestor / "repo"
        nested_repo.mkdir(parents=True)
        self.assertGreaterEqual(len(ancestor.parts), 4)
        self.fail_code("OUTPUT_MUST_BE_OUTSIDE_REPOSITORY", C.prepare_output, nested_repo, str(ancestor), environment={})
        (self.output / "existing").write_text("preserve")
        self.fail_code("EXISTING_NONEMPTY_OUTPUT_FORBIDDEN", C.prepare_output, self.repo, str(self.output), environment={})
        self.assertEqual((self.output / "existing").read_text(), "preserve")
        self.fail_code("EXPLICIT_OUTPUT_MUST_BE_ABSOLUTE", C.prepare_output, self.repo, "relative", environment={})
        self.fail_code("BROAD_OUTPUT_ROOT_FORBIDDEN", C.prepare_output, self.repo, "/", environment={})
        alias = self.root / "alias"
        alias.symlink_to(self.output, target_is_directory=True)
        self.fail_code("SYMLINK_PATH_FORBIDDEN", C.prepare_output, self.repo, str(alias), environment={})

    def test_linux_shallow_tmp_path_is_broad_before_ancestry_or_io(self):
        # Only bypass the platform's /tmp alias check for this lexical-depth
        # unit test: macOS aliases /tmp, Linux normally does not. Production
        # symlink enforcement is unchanged and exercised in separate tests.
        shallow = Path("/tmp/dc20-shallow-output-fixture")
        self.assertEqual(len(shallow.parts), 3)
        with mock.patch.object(C, "no_symlinks"), mock.patch.object(Path, "mkdir") as mkdir:
            self.fail_code("BROAD_OUTPUT_ROOT_FORBIDDEN", C.prepare_output, self.repo, str(shallow), environment={})
        mkdir.assert_not_called()

    def test_actions_output_cannot_be_redirected(self):
        runner = self.root / "runner"
        runner.mkdir()
        env = {"GITHUB_ACTIONS": "true", "RUNNER_TEMP": str(runner)}
        self.fail_code("ACTIONS_OUTPUT_NOT_FIXED_RUNNER_TEMP", C.prepare_output, self.repo, str(self.root / "other"), environment=env)
        self.assertEqual(C.prepare_output(self.repo, str(runner / C.OUTPUT_SUBDIR), environment=env), runner / C.OUTPUT_SUBDIR)

    def test_artifact_paths_reject_escape_symlink_and_overwrite(self):
        self.fail_code("UNSAFE_ARTIFACT_PATH", self.artifacts.write, "../outside", b"bad")
        (self.output / "link").symlink_to(self.repo, target_is_directory=True)
        self.fail_code("SYMLINK_PATH_FORBIDDEN", self.artifacts.write, "link/injected", b"bad")
        self.artifacts.json("fixed.json", {"safe": True})
        with self.assertRaises(FileExistsError):
            self.artifacts.json("fixed.json", {})
        self.fail_code("ONLY_STATUS_CAN_BE_REPLACED", self.artifacts.write, "fixed.json", b"bad", replace_status=True)


class ClientTests(Base):
    def test_success_raw_hash_receipt_and_serial_rate(self):
        actual = self.client.query("daily", params(), {CODE})
        self.assertEqual(actual, [row("daily")])
        raw = gzip.decompress((self.output / "responses/000001.json.gz").read_bytes())
        self.assertEqual(raw, response("daily"))
        receipt = self.artifact("receipts/000001.json")
        self.assertEqual(receipt["response_sha256"], C.sha_bytes(raw))
        self.assertEqual(receipt["request_without_credential_sha256"], C.sha_bytes(C.canonical({"api_name": "daily", "params": params(), "fields": ",".join(C.FIELDS["daily"])})))
        self.client.query("daily", params(), {CODE})
        self.assertEqual(self.clock.sleeps, [.5])
        sent = json.loads(self.transport.call_args.args[0])
        self.assertEqual(sent["token"], TOKEN)
        self.assertNotIn("token", sent["params"])
        self.assert_secret_absent()

    def test_redirect_never_followed(self):
        self.fail_code("HTTPS_REDIRECT_REFUSED", C.NoRedirect().redirect_request, None, None, 302, TOKEN, {}, "https://evil.invalid/" + TOKEN)

    def test_transport_error_never_leaks_exception_or_raw_message(self):
        self.transport.side_effect = RuntimeError("request token=" + TOKEN)
        self.fail_code("HTTPS_TRANSPORT_FAILURE", self.client.query, "daily", params(), {CODE})
        receipt = self.artifact("receipts/000001.json")
        self.assertEqual(receipt["failure_code"], "HTTPS_TRANSPORT_FAILURE")
        self.assertFalse((self.output / "responses").exists())
        self.assert_secret_absent()

    def test_raw_and_unicode_escaped_secret_echo_refused(self):
        self.transport.return_value = json.dumps({"code": -1, "msg": TOKEN}).encode()
        self.fail_code("SECRET_ECHO_RESPONSE_REFUSED", self.client.query, "daily", params(), {CODE})
        self.transport.return_value = ('{"code":-1,"msg":"' + "".join("\\u%04x" % ord(char) for char in TOKEN) + '"}').encode()
        self.fail_code("SECRET_ECHO_RESPONSE_REFUSED", self.client.query, "daily", params(), {CODE})
        self.assertFalse((self.output / "responses").exists())
        self.assert_secret_absent()

    def test_api_error_keeps_only_hash_code_not_message(self):
        message = "PRIVATE_UPSTREAM_FREE_TEXT_DO_NOT_STORE"
        self.transport.return_value = json.dumps({"code": -2002, "msg": message}).encode()
        error = self.fail_code("UPSTREAM_API_REJECTED", self.client.query, "adj_factor", params(), {CODE})
        self.assertTrue(error.optional_api_rejection)
        receipt = self.artifact("receipts/000001.json")
        self.assertEqual(receipt["upstream_code"], -2002)
        self.assertNotIn(message, json.dumps(receipt))
        self.assertFalse((self.output / "responses").exists())

    def test_success_upstream_prose_and_nonfinite_json_refused(self):
        self.transport.return_value = response("daily", msg="UNEXPECTED_PROSE")
        self.fail_code("UPSTREAM_MESSAGE_PAYLOAD_REFUSED", self.client.query, "daily", params(), {CODE})
        invalid = row("daily")
        invalid["open"] = float("nan")
        self.transport.return_value = response("daily", [invalid])
        self.fail_code("RESPONSE_NONFINITE_OR_INVALID_JSON", self.client.query, "daily", params(), {CODE})
        self.assertEqual(self.artifact("receipts/000002.json")["status"], "FAILED")

    def test_invalid_candidate_evidence_dates_fields_duplicates(self):
        cases = []
        malformed = row("daily"); malformed["open"] = "10.0"
        cases.append((response("daily", [malformed]), "CANDIDATE_NUMERIC_EVIDENCE_MISSING"))
        cases.append((response("daily", [row("daily", date="20221115")]), "RESPONSE_DATE_OUT_OF_SCOPE"))
        cases.append((response("daily", [row("daily"), row("daily")]), "RESPONSE_DUPLICATE_CODE"))
        bad = row("daily"); bad["high"] = 5.
        cases.append((response("daily", [bad]), "CANDIDATE_OHLC_INCONSISTENT"))
        for payload, failure in cases:
            self.transport.return_value = payload
            self.fail_code(failure, self.client.query, "daily", params(), {CODE})
        self.transport.return_value = b'{"code":0,"msg":"","data":{"fields":[{}],"items":[]}}'
        self.fail_code("RESPONSE_FIELDS_MISMATCH", self.client.query, "daily", params(), {CODE})

    def test_bulk_explicit_null_is_preserved_but_canary_remains_strict(self):
        for api in C.FIELDS:
            field = C.FIELDS[api][2]
            nullable = row(api)
            nullable[field] = None
            self.transport.return_value = response(api, [nullable])
            values = self.client.query(api, params(), {CODE})
            self.assertIsNone(values[0][field])
            raw = gzip.decompress((self.output / f"responses/{self.client.count:06d}.json.gz").read_bytes())
            self.assertIn(b"null", raw)
            self.fail_code("CANDIDATE_NUMERIC_EVIDENCE_MISSING", self.client.query, api, params(ts_code=CODE), {CODE}, canary=True)

    def test_nullable_does_not_accept_bool_string_or_nonfinite(self):
        for value in [False, True, "0", "", "null", float("inf"), float("nan")]:
            for api in C.FIELDS:
                bad = row(api)
                bad[C.FIELDS[api][2]] = value
                self.fail_code("CANDIDATE_NUMERIC_EVIDENCE_MISSING", C.validate_rows, api, C.FIELDS[api],
                               [[bad[k] for k in C.FIELDS[api]]], DATE, {CODE})

    def test_valid_nonnull_fields_and_available_pairs_remain_validated(self):
        bad = row("daily"); bad["open"] = None; bad["high"] = 5.
        self.fail_code("CANDIDATE_OHLC_INCONSISTENT", C.validate_rows, "daily", C.FIELDS["daily"],
                       [[bad[k] for k in C.FIELDS["daily"]]], DATE, {CODE})
        bad = row("daily"); bad["amount"] = None; bad["vol"] = -1.
        self.fail_code("CANDIDATE_VOLUME_NEGATIVE", C.validate_rows, "daily", C.FIELDS["daily"],
                       [[bad[k] for k in C.FIELDS["daily"]]], DATE, {CODE})
        bad = row("stk_limit"); bad["pre_close"] = None; bad["up_limit"] = 8.
        self.fail_code("CANDIDATE_LIMIT_INVALID", C.validate_rows, "stk_limit", C.FIELDS["stk_limit"],
                       [[bad[k] for k in C.FIELDS["stk_limit"]]], DATE, {CODE})

    def test_non_candidate_fund_can_have_null_numeric_evidence(self):
        fund = {field: None for field in C.FIELDS["daily"]}
        fund.update(ts_code="000001.OF", trade_date=DATE)
        self.transport.return_value = response("daily", [row("daily"), fund])
        self.assertEqual(len(self.client.query("daily", params(), {CODE})), 2)

    def test_canary_must_be_one_exact_candidate(self):
        self.transport.return_value = response("daily", [])
        self.fail_code("CANARY_EMPTY_OR_MULTIPLE_ROWS", self.client.query, "daily", params(ts_code=CODE), {CODE}, canary=True)
        self.transport.return_value = response("daily", [row("daily", code="600000.SH")])
        self.fail_code("CANARY_CODE_OUT_OF_SCOPE", self.client.query, "daily", params(ts_code=CODE), {CODE}, canary=True)

    def test_budget_deadline_future_date_and_batch_fail_before_network(self):
        self.client.count = C.MAX_REQUESTS
        self.fail_code("REQUEST_BUDGET_EXHAUSTED", self.client.query, "daily", params(), {CODE})
        self.client.count = 0
        self.clock.now = C.SOFT_DEADLINE_SECONDS
        self.fail_code("COLLECTION_SOFT_DEADLINE_EXCEEDED", self.client.query, "daily", params(), {CODE})
        self.clock.now = 0
        self.fail_code("REQUEST_DATE_INVALID", self.client.query, "daily", params(trade_date="20260907"), {CODE})
        self.fail_code("REQUEST_PARAMETERS_NOT_FIXED", self.client.query, "daily", params(ts_code=CODE + ",600000.SH"), {CODE})
        self.fail_code("REQUEST_OFFSET_INVALID", self.client.query, "daily", params(offset=-1), {CODE})
        self.fail_code("REQUEST_OFFSET_INVALID", self.client.query, "daily", params(offset=True), {CODE})
        self.transport.assert_not_called()

    def test_deadline_also_checked_after_rate_wait(self):
        self.client.query("daily", params(), {CODE})
        self.clock.now = C.SOFT_DEADLINE_SECONDS - .1
        self.client.last_start = self.clock.now
        self.fail_code("COLLECTION_SOFT_DEADLINE_EXCEEDED", self.client.query, "daily", params(), {CODE})
        self.assertEqual(self.transport.call_count, 1)


class PaginationTests(Base):
    def test_short_nonempty_page_does_not_imply_exhaustion(self):
        pages = [response("adj_factor", [row("adj_factor", code="600000.SH")]), response("adj_factor", [row("adj_factor")])]
        self.transport.side_effect = pages
        selected, info = C.collect_pages(self.client, "adj_factor", DATE, {CODE})
        self.assertEqual(selected, [row("adj_factor")])
        self.assertEqual(info["query_numbers"], [1, 2])
        self.assertTrue(info["candidate_scope_complete"])
        self.assertFalse(info["whole_market_exhaustion_observed"])
        sent_offsets = [json.loads(call.args[0])["params"]["offset"] for call in self.transport.call_args_list]
        self.assertEqual(sent_offsets, [0, 1])

    def test_empty_page_retains_missing_not_zero(self):
        self.transport.side_effect = [response("daily", [row("daily", code="600000.SH")]), response("daily", [])]
        selected, info = C.collect_pages(self.client, "daily", DATE, {CODE})
        self.assertEqual(selected, [])
        self.assertEqual(info["missing_candidate_codes"], [CODE])
        self.assertTrue(info["whole_market_exhaustion_observed"])
        self.assertFalse(info["candidate_scope_complete"])

    def test_duplicates_ignored_offsets_and_page_cap_block(self):
        self.transport.return_value = response("daily", [row("daily", code="600000.SH")])
        self.fail_code("PAGINATION_DUPLICATES_OR_IGNORED_OFFSET", C.collect_pages, self.client, "daily", DATE, {CODE})
        self.transport.side_effect = [response("daily", [row("daily", code="60000%d.SH" % i)]) for i in range(4)]
        self.fail_code("PAGINATION_CAP_REACHED_COMPLETENESS_UNPROVEN", C.collect_pages, self.client, "daily", DATE, {CODE})
        self.assertEqual(self.client.count, 6)


class CollectionTests(Base):
    def request(self):
        return {"canary": {"trade_date": DATE, "ts_code": CODE}, "sessions": [{"trade_date": DATE, "codes": [CODE]}]}

    def execute(self):
        with contextlib.redirect_stdout(io.StringIO()) as logged:
            result, status = C.perform_collection(self.client, self.request(), self.artifacts, {"test_only": True})
        self.assertNotIn(TOKEN, logged.getvalue())
        self.assert_secret_absent()
        manifest = self.artifact("artifact_manifest.json")
        for name, spec in manifest["files"].items():
            self.assertEqual(spec["sha256"], C.sha(self.output / name))
        self.assertIn("artifact_manifest_sha256", logged.getvalue())
        return result, status

    def test_complete_candidate_sources_are_not_new_labels_or_actual_fills(self):
        self.transport.side_effect = lambda body: response(json.loads(body)["api_name"])
        result, status = self.execute()
        self.assertEqual(result, 0)
        self.assertEqual(self.client.count, 6)
        self.assertTrue(status["required_collection_complete"])
        self.assertTrue(status["required_candidate_coverage_complete"])
        for field in ["corporate_actions_resolved", "tail_window_is_forced_exit", "actual_fill_claim", "production_data_writes", "source_files_are_new_labels"]:
            self.assertFalse(status[field])
        self.assertEqual(status["models_trained"], 0)
        self.assertTrue((self.output / ("candidate_sources/" + DATE + "/daily.csv")).is_file())

    def test_optional_api_rejection_allows_required_sources(self):
        def transport(body):
            api = json.loads(body)["api_name"]
            return json.dumps({"code": -2002, "msg": "not retained"}).encode() if api == "adj_factor" else response(api)
        self.transport.side_effect = transport
        result, status = self.execute()
        self.assertEqual(result, 0)
        self.assertEqual(status["optional_adj_factor"], "OPTIONAL_UNAVAILABLE")
        self.assertEqual(self.client.count, 5)
        self.assertFalse((self.output / "canary/adj_factor.csv").exists())

    def test_optional_network_failure_still_stops(self):
        def transport(body):
            api = json.loads(body)["api_name"]
            if api == "adj_factor":
                raise RuntimeError(TOKEN)
            return response(api)
        self.transport.side_effect = transport
        result, status = self.execute()
        self.assertEqual(result, 2)
        self.assertEqual(status["failure_code"], "HTTPS_TRANSPORT_FAILURE")
        self.assertFalse(status["required_collection_complete"])
        self.assertEqual(self.client.count, 3)

    def test_required_canary_failure_stops_before_other_apis(self):
        self.transport.return_value = json.dumps({"code": -1, "msg": "private rejected"}).encode()
        result, status = self.execute()
        self.assertEqual(result, 2)
        self.assertEqual(status["failure_code"], "UPSTREAM_API_REJECTED")
        self.assertEqual(self.client.count, 1)

    def test_missing_bulk_truth_never_becomes_zero(self):
        def transport(body):
            sent = json.loads(body)
            return response(sent["api_name"], [] if "ts_code" not in sent["params"] and sent["api_name"] == "daily" else None)
        self.transport.side_effect = transport
        result, status = self.execute()
        self.assertEqual(result, 0)
        self.assertEqual(status["status"], "COLLECTED_REQUIRED_SOURCES_WITH_GAPS")
        self.assertFalse(status["required_candidate_coverage_complete"])
        self.assertEqual(len((self.output / ("candidate_sources/" + DATE + "/daily.csv")).read_text().splitlines()), 1)

    def test_bulk_null_fields_continue_other_dates_and_mark_coverage_incomplete(self):
        later = "20221115"
        def transport(body):
            sent = json.loads(body)
            api, p = sent["api_name"], sent["params"]
            current = row(api, date=p["trade_date"])
            if "ts_code" not in p and p["trade_date"] == DATE:
                if api == "daily":
                    current["open"], current["vol"] = None, None
                elif api == "stk_limit":
                    current["pre_close"] = None
            return response(api, [current])
        self.transport.side_effect = transport
        request = self.request()
        request["sessions"].append({"trade_date": later, "codes": [CODE]})
        with contextlib.redirect_stdout(io.StringIO()):
            result, status = C.perform_collection(self.client, request, self.artifacts, {})
        self.assertEqual(result, 0)
        self.assertEqual(status["sessions_completed"], 2)
        self.assertTrue(status["required_collection_complete"])
        self.assertFalse(status["required_candidate_coverage_complete"])
        self.assertEqual(status["status"], "COLLECTED_REQUIRED_SOURCES_WITH_GAPS")
        info = status["coverage"][0]["apis"]["daily"]
        self.assertTrue(info["candidate_scope_complete"])
        self.assertEqual(info["missing_candidate_codes"], [])
        self.assertEqual(info["incomplete_candidate_fields"], [{"ts_code": CODE, "fields": ["open", "vol"]}])
        with (self.output / f"candidate_sources/{DATE}/daily.csv").open() as handle:
            saved = list(csv.DictReader(handle))[0]
        self.assertEqual(saved["open"], "")
        self.assertEqual(saved["vol"], "")
        self.assertEqual(saved["high"], "11.0")
        self.assertEqual(status["coverage"][1]["apis"]["daily"]["incomplete_candidate_fields"], [])

    def test_soft_deadline_still_finalizes_manifest(self):
        self.clock.now = C.SOFT_DEADLINE_SECONDS
        result, status = self.execute()
        self.assertEqual(result, 2)
        self.assertEqual(status["failure_code"], "COLLECTION_SOFT_DEADLINE_EXCEEDED")
        self.assertEqual(self.client.count, 0)
        self.transport.assert_not_called()

    def test_required_all_dates_precede_optional_bulk(self):
        seen = []
        def transport(body):
            sent = json.loads(body)
            seen.append((sent["api_name"], sent["params"]["trade_date"], "ts_code" in sent["params"]))
            return response(sent["api_name"], [row(sent["api_name"], date=sent["params"]["trade_date"])])
        self.transport.side_effect = transport
        request = self.request()
        request["sessions"].append({"trade_date": "20221115", "codes": [CODE]})
        with contextlib.redirect_stdout(io.StringIO()):
            result, status = C.perform_collection(self.client, request, self.artifacts, {})
        self.assertEqual(result, 0)
        self.assertEqual([item[0] for item in seen], ["daily", "stk_limit", "adj_factor", "daily", "stk_limit", "daily", "stk_limit", "adj_factor", "adj_factor"])
        self.assertEqual(status["sessions_completed"], 2)
        self.assertEqual(status["optional_sessions_completed"], 2)

    def test_optional_budget_does_not_reclassify_collected_required_data(self):
        self.transport.side_effect = lambda body: response(json.loads(body)["api_name"])
        with mock.patch.object(C, "MAX_REQUESTS", 5):
            result, status = self.execute()
        self.assertEqual(result, 0)
        self.assertTrue(status["required_collection_complete"])
        self.assertEqual(status["optional_adj_factor"], "OPTIONAL_PARTIAL_BUDGET")
        self.assertEqual(status["optional_stop_code"], "REQUEST_BUDGET_EXHAUSTED")
        self.assertEqual(self.client.count, 5)

    def test_optional_bulk_protocol_error_blocks_but_preserves_required_completion(self):
        def transport(body):
            sent = json.loads(body)
            if sent["api_name"] == "adj_factor" and "ts_code" not in sent["params"]:
                raise RuntimeError(TOKEN)
            return response(sent["api_name"])
        self.transport.side_effect = transport
        result, status = self.execute()
        self.assertEqual(result, 2)
        self.assertEqual(status["status"], "BLOCKED_COLLECTION")
        self.assertTrue(status["required_collection_complete"])
        self.assertTrue(status["required_candidate_coverage_complete"])
        self.assertEqual(status["failure_code"], "HTTPS_TRANSPORT_FAILURE")

    def test_main_mismatched_repo_does_not_start_or_read_credentials(self):
        wrong_repo = self.root / "wrong-repo"
        wrong_repo.mkdir()
        with mock.patch("sys.argv", ["collect.py", "--repo", str(wrong_repo), "--output", str(self.output)]), contextlib.redirect_stdout(io.StringIO()) as logged, mock.patch.object(C, "Client") as client:
            self.assertEqual(C.main(), 2)
        self.assertIn("REPOSITORY_ARGUMENT_MISMATCH", logged.getvalue())
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
