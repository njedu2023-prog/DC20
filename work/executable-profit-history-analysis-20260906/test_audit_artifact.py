"""Stdlib integration tests: real collector, fake transport, independent audit.

No network, token access, production edits, or persistent analysis outputs.
"""
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


HERE = Path(__file__).resolve().parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


audit = module("history_artifact_audit", HERE / "audit_artifact.py")
collector = module("history_source_collector_for_audit_tests", HERE.parent / "executable-profit-history-source-20260906/collect.py")
RUN_ID, RUN_SHA, RUN_ATTEMPT = "1234567", "a" * 40, "1"
CODE, OTHER = "600001.SH", "000001.SZ"
DATES = ("20260903", "20260904")


def row(api, date, code):
    result = {"ts_code": code, "trade_date": date}
    if api == "daily":
        result.update(open=10.0, high=10.5, low=9.5, close=10.1, pre_close=10.0,
                      change=.1, pct_chg=1.0, vol=123.45, amount=12345.6)
    elif api == "stk_limit":
        result.update(pre_close=10.0, up_limit=11.0, down_limit=9.0)
    else:
        result["adj_factor"] = 1.0
    return result


def csv_bytes(fields, rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


class ArtifactAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.bundle, self.repo = self.base / "bundle", self.base / "reference"
        self.bundle.mkdir()
        self.repo.mkdir()
        self.clock = [0.0]

    def fixture(self, *, missing=False, optional=False, transport_failure=False, limited_page=False,
                optional_transport_failure=False, request_budget=None, null_fields=None):
        request = {"as_of_date": audit.ASOF, "tail_sessions": audit.TAIL,
                   "canary": {"trade_date": DATES[0], "ts_code": CODE},
                   "sessions": [{"trade_date": date, "codes": sorted([CODE, OTHER])} for date in DATES],
                   "source_inputs": audit.INPUTS}
        plan_bytes = audit.canonical({"fixture": "not_a_production_plan"}) + b"\n"
        request_bytes = audit.canonical(collector.request_contract(request)) + b"\n"
        contract = {"request": request, "plan_sha256": audit.sha(plan_bytes), "request_sha256": audit.sha(request_bytes),
                    "collector_sha256": audit.sha(Path(collector.__file__).read_bytes()), "source_inputs": audit.INPUTS,
                    "source_repository": "njedu2023-prog/DC20", "source_commit": "b" * 40, "expected_overlap_days": 2}
        provenance = {k: contract[k] for k in ("plan_sha256", "request_sha256", "collector_sha256", "source_inputs", "source_repository", "source_commit")}
        provenance.update(github_run_id=RUN_ID, github_sha=RUN_SHA, github_run_attempt=RUN_ATTEMPT,
                          ledger_candidate_universe_unchanged=True, market_endpoint="https://api.tushare.pro/",
                          global_other_IP_activity_audited=False, soft_deadline_seconds=5400)
        store = collector.Artifacts(self.bundle)
        store.write("PLAN.json", plan_bytes)
        store.write("REQUEST.json", request_bytes)
        store.json("session_plan.json", request["sessions"])

        def rows_for(api, date):
            codes = [CODE, OTHER]
            if missing and api == "daily" and date == DATES[1]:
                codes.remove(OTHER)
            return [row(api, date, code) for code in sorted(codes)]

        def transport(body):
            args = json.loads(body)
            api, params = args["api_name"], args["params"]
            date = params["trade_date"]
            if api == "adj_factor" and not optional:
                return b'{"code":-1,"msg":"not authorized","data":null}'
            if transport_failure and api == "stk_limit" and date == DATES[1]:
                raise collector.CollectionError("HTTPS_TRANSPORT_FAILURE")
            if optional_transport_failure and api == "adj_factor" and "ts_code" not in params:
                raise collector.CollectionError("HTTPS_TRANSPORT_FAILURE")
            rows = rows_for(api, date)
            if "ts_code" in params:
                rows = [r for r in rows if r["ts_code"] == params["ts_code"]]
            else:
                for item in rows:
                    for field in (null_fields or {}).get((date, api, item["ts_code"]), []):
                        item[field] = None
                start = params["offset"]
                rows = rows[start:start + (1 if limited_page else audit.PAGE)]
            return audit.canonical({"code": 0, "msg": None, "data": {"fields": audit.FIELDS[api], "items": [[r[k] for k in audit.FIELDS[api]] for r in rows]}})

        client = collector.Client("FAKE_CREDENTIAL_ONLY_FOR_TEST", store, transport=transport,
                                  clock=lambda: self.clock[0], sleep=lambda seconds: self.clock.__setitem__(0, self.clock[0] + seconds),
                                  environment={"GITHUB_RUN_ID": RUN_ID, "GITHUB_SHA": RUN_SHA, "GITHUB_RUN_ATTEMPT": RUN_ATTEMPT})
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(collector, "MAX_REQUESTS", request_budget or collector.MAX_REQUESTS):
            collector.perform_collection(client, request, store, provenance)
        for date in DATES:
            directory = self.repo / "data/market/raw" / date[:4] / date
            directory.mkdir(parents=True)
            for api in audit.COMPARE:
                (directory / (api + ".csv")).write_bytes(csv_bytes(audit.FIELDS[api], rows_for(api, date)))
        self.contract = contract
        return self.run_audit()

    def run_audit(self, **kwargs):
        return audit.audit_bundle(self.bundle, self.repo, self.contract,
                                  expected_run_id=kwargs.get("run_id", RUN_ID),
                                  expected_run_sha=kwargs.get("run_sha", RUN_SHA),
                                  expected_run_attempt=kwargs.get("attempt", RUN_ATTEMPT),
                                  expected_manifest_sha256=kwargs.get("manifest_sha", audit.sha((self.bundle / "artifact_manifest.json").read_bytes())))

    def rebind(self, path, value, *, json_value=False):
        payload = audit.canonical(value) + b"\n" if json_value else value
        (self.bundle / path).write_bytes(payload)
        manifest_path = self.bundle / "artifact_manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["files"][path] = {"sha256": audit.sha(payload), "bytes": len(payload)}
        manifest_path.write_bytes(audit.canonical(manifest) + b"\n")

    @staticmethod
    def codes(report):
        return {item["code"] for item in report["issues"]}

    def test_complete_source_never_authorizes_training(self):
        report = self.fixture(optional=True)
        self.assertTrue(report["source_ready_for_label_rebuild"], report)
        self.assertFalse(report["training_authorized"])
        self.assertFalse(report["historically_available_at_D"])
        self.assertEqual(report["requested_sessions"], 2)
        self.assertEqual(report["requested_code_date_keys"], 4)
        self.assertEqual(report["verified_required_partitions"], 4)
        self.assertEqual(len(report["overlap"]["reference_files"]), 4)
        self.assertGreater(report["overlap"]["candidate_compared_field_values"], 0)

    def test_missing_candidate_is_unknown_not_a_source_gate_failure(self):
        report = self.fixture(missing=True)
        self.assertTrue(report["source_ready_for_label_rebuild"], report)
        self.assertEqual(report["missing_required_candidate_key_count"], 1)
        self.assertEqual(report["missing_candidate_keys"], [{"trade_date": DATES[1], "ts_code": OTHER, "api": "daily", "status": "UNKNOWN_NOT_ZERO"}])
        self.assertEqual(report["optional_adj_factor"], "OPTIONAL_UNAVAILABLE")

    def test_null_bulk_fields_keep_identity_and_become_explicit_unknowns(self):
        report = self.fixture(null_fields={(DATES[0], "daily", CODE): ["open", "vol"],
                                           (DATES[1], "stk_limit", OTHER): ["pre_close"]})
        self.assertTrue(report["source_ready_for_label_rebuild"], report)
        self.assertEqual(report["missing_required_candidate_key_count"], 0)
        self.assertEqual(report["incomplete_required_candidate_row_count"], 2)
        self.assertEqual(report["incomplete_required_field_count"], 3)
        self.assertEqual(report["incomplete_candidate_fields"][0], {"trade_date": DATES[0], "api": "daily", "ts_code": CODE,
                          "fields": ["open", "vol"], "status": "EXPLICIT_JSON_NULL_UNKNOWN_NOT_ZERO"})
        self.assertEqual(report["collection_status"], "COLLECTED_REQUIRED_SOURCES_WITH_GAPS")
        self.assertEqual(len(report["overlap"]["incomplete_fields"]), 2)
        self.assertEqual(report["overlap"]["conflicts"], [])

    def test_null_raw_value_cannot_be_filled_with_zero_in_csv(self):
        self.fixture(null_fields={(DATES[0], "daily", CODE): ["open"]})
        path = f"candidate_sources/{DATES[0]}/daily.csv"
        fields, rows = audit.csv_rows((self.bundle / path).read_bytes())
        next(r for r in rows if r["ts_code"] == CODE)["open"] = "0"
        self.rebind(path, csv_bytes(fields, rows))
        report = self.run_audit()
        self.assertFalse(report["source_ready_for_label_rebuild"])
        self.assertIn("CSV_NULL_NOT_PRESERVED_AS_EMPTY_FIELD", self.codes(report))

    def test_null_field_list_is_reconstructed_from_raw_not_trusted(self):
        self.fixture(null_fields={(DATES[0], "daily", CODE): ["vol"]})
        status = json.loads((self.bundle / "status.json").read_bytes())
        status["coverage"][0]["apis"]["daily"]["incomplete_candidate_fields"] = []
        status["required_candidate_coverage_complete"] = True
        self.rebind("status.json", status, json_value=True)
        report = self.run_audit()
        self.assertFalse(report["source_ready_for_label_rebuild"])
        self.assertIn("STATUS_COVERAGE_RAW_MISMATCH", self.codes(report))

    def test_pinned_legacy_failure_can_be_diagnosed_but_not_used_as_source(self):
        self.fixture(transport_failure=True)
        old_hash = "e" * 64
        status = json.loads((self.bundle / "status.json").read_bytes())
        status["provenance"]["collector_sha256"] = old_hash
        for item in status["coverage"]:
            for info in item["apis"].values():
                info.pop("incomplete_candidate_fields")
        self.rebind("status.json", status, json_value=True)
        manifest_path = self.bundle / "artifact_manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["provenance"] = status["provenance"]
        manifest_path.write_bytes(audit.canonical(manifest) + b"\n")
        legacy = {audit.sha(manifest_path.read_bytes()): {key: status["provenance"][key] for key in
                  ("collector_sha256", "plan_sha256", "request_sha256", "github_run_id", "github_sha", "github_run_attempt")}}
        with mock.patch.object(audit, "LEGACY_FAILED", legacy):
            report = self.run_audit()
        self.assertFalse(report["source_ready_for_label_rebuild"])
        self.assertTrue(report["legacy_failed_artifact_diagnostic_only"])
        self.assertEqual(report["verified_required_partitions"], 3)
        self.assertEqual(report["source_contract"]["collector_sha256"], old_hash)
        self.assertFalse(report["training_authorized"])

    def test_existing_v2_execution_fields_null_or_empty_stay_unresolved(self):
        labels = module("v2_existing_labels_for_null_audit", HERE.parent / "executable-profit-execution-v2-20260906/build_labels.py")
        for value in (None, ""):
            self.assertIsNone(labels.number(value))
            for api, field in (("daily", "open"), ("daily", "vol"), ("stk_limit", "up_limit"), ("stk_limit", "down_limit")):
                daily, limits = row("daily", DATES[0], CODE), row("stk_limit", DATES[0], CODE)
                (daily if api == "daily" else limits)[field] = value
                state, data = labels.bar_state(daily, limits)
                self.assertEqual(state, "INVALID")
                self.assertIsNone(data)
        frozen = {"signal_date": "20260902", "exec_date": DATES[0], "scheduled_exit_date": DATES[1],
                  "ts_code": CODE, "stage": "2", "promotion_rank": "1", "top10_members_sha256": "f" * 64}
        for missing_date, missing_field, expected_status, expected_fill in (
                (DATES[0], "open", "INVALID_T_TRUTH", None),
                (DATES[1], "down_limit", "INVALID_EXIT_TRUTH", 1)):
            def candidate(date, code):
                daily, limits = row("daily", date, code), row("stk_limit", date, code)
                if date == missing_date:
                    (limits if missing_field == "down_limit" else daily)[missing_field] = None
                return daily, limits
            result = labels.label_row(frozen, ["20260902", *DATES], DATES[1], mock.Mock(candidate=candidate))
            self.assertEqual(result["label_status"], expected_status)
            self.assertEqual(result["proxy_fill"], expected_fill)
            self.assertIsNone(result["slot_net_return"])
            self.assertIsNone(result["label_available_date"])

    def test_short_page_uses_actual_offset_and_does_not_mean_exhaustion(self):
        report = self.fixture(limited_page=True)
        self.assertTrue(report["source_ready_for_label_rebuild"], report)
        status = json.loads((self.bundle / "status.json").read_bytes())
        info = status["coverage"][0]["apis"]["daily"]
        self.assertEqual(info["pages"], 2)
        self.assertFalse(info["whole_market_exhaustion_observed"])

    def test_partial_failure_keeps_coverage_but_cannot_admit_source(self):
        report = self.fixture(transport_failure=True)
        self.assertFalse(report["source_ready_for_label_rebuild"])
        self.assertEqual(report["verified_required_partitions"], 3)
        self.assertIn("REQUIRED_QUERY_FAILED", self.codes(report))
        self.assertEqual(report["missing_required_partitions"], [{"trade_date": DATES[1], "api": "stk_limit"}])

    def test_optional_budget_does_not_revoke_complete_required_evidence(self):
        report = self.fixture(optional=True, limited_page=True, request_budget=12)
        self.assertTrue(report["source_ready_for_label_rebuild"], report)
        self.assertEqual(report["optional_adj_factor"], "OPTIONAL_PARTIAL_BUDGET")
        self.assertEqual(report["verified_optional_partitions"], 0)
        self.assertEqual(len(report["incomplete_optional_partitions"]), 1)
        self.assertFalse(report["training_authorized"])

    def test_optional_protocol_failure_cannot_use_required_flag_to_hide_block(self):
        report = self.fixture(optional=True, optional_transport_failure=True)
        self.assertFalse(report["source_ready_for_label_rebuild"])
        self.assertEqual(report["verified_required_partitions"], 4)
        self.assertIn("COLLECTOR_REPORTED_BLOCK", self.codes(report))

    def test_external_manifest_hash_required(self):
        self.fixture()
        report = self.run_audit(manifest_sha="c" * 64)
        self.assertIn("EXTERNAL_MANIFEST_HASH_MISMATCH", self.codes(report))

    def test_external_run_identity_cannot_be_self_attested(self):
        self.fixture()
        report = self.run_audit(run_sha="d" * 40)
        self.assertIn("MANIFEST_EXTERNAL_IDENTITY_MISMATCH", self.codes(report))
        self.assertFalse(self.run_audit(attempt="2")["source_ready_for_label_rebuild"])

    def test_modified_csv_fails_manifest_hash(self):
        self.fixture()
        path = self.bundle / f"candidate_sources/{DATES[0]}/daily.csv"
        path.write_bytes(path.read_bytes() + b"tamper")
        self.assertIn("MANIFEST_FILE_HASH_OR_SIZE_MISMATCH", self.codes(self.run_audit()))

    def test_rehashed_csv_still_must_equal_raw(self):
        self.fixture()
        path = f"candidate_sources/{DATES[0]}/daily.csv"
        fields, rows = audit.csv_rows((self.bundle / path).read_bytes())
        rows[0]["open"] = "12.34"
        self.rebind(path, csv_bytes(fields, rows))
        report = self.run_audit()
        self.assertIn("CSV_DIFFERS_FROM_RAW_RESPONSE", self.codes(report))
        self.assertEqual(report["verified_required_partitions"], 3)

    def test_recompressed_response_must_match_receipt_hash(self):
        self.fixture()
        path = "responses/000001.json.gz"
        raw = gzip.decompress((self.bundle / path).read_bytes())
        self.rebind(path, gzip.compress(raw + b" ", mtime=0))
        self.assertIn("RAW_RESPONSE_HASH_OR_SIZE_MISMATCH", self.codes(self.run_audit()))

    def test_request_hash_is_bound_independently(self):
        self.fixture()
        path = "receipts/000004.json"
        receipt = json.loads((self.bundle / path).read_bytes())
        receipt["request_without_credential_sha256"] = "0" * 64
        self.rebind(path, receipt, json_value=True)
        self.assertIn("CREDENTIAL_FREE_REQUEST_HASH_MISMATCH", self.codes(self.run_audit()))

    def test_session_plan_is_exact_not_only_matching_count(self):
        self.fixture()
        sessions = copy.deepcopy(self.contract["request"]["sessions"])
        sessions[0]["codes"][0] = "000002.SZ"
        self.rebind("session_plan.json", sessions, json_value=True)
        self.assertIn("ARTIFACT_SESSION_PLAN_MISMATCH", self.codes(self.run_audit()))

    def test_cent_price_format_and_exact_decimal_volume(self):
        self.fixture()
        path = self.repo / f"data/market/raw/2026/{DATES[0]}/daily.csv"
        fields, rows = audit.csv_rows(path.read_bytes())
        rows[0]["open"] = "10.00000000001"
        rows[0]["vol"] = "123.450000"
        path.write_bytes(csv_bytes(fields, rows))
        self.assertTrue(self.run_audit()["source_ready_for_label_rebuild"])
        rows[0]["vol"] = "123.450001"
        path.write_bytes(csv_bytes(fields, rows))
        report = self.run_audit()
        self.assertFalse(report["source_ready_for_label_rebuild"])
        self.assertEqual(report["overlap"]["conflicts"][0]["field"], "vol")

    def test_price_difference_and_missing_field_are_explicit(self):
        self.fixture()
        path = self.repo / f"data/market/raw/2026/{DATES[0]}/stk_limit.csv"
        fields, rows = audit.csv_rows(path.read_bytes())
        rows[0]["down_limit"] = "8.99"
        rows[1]["up_limit"] = ""
        path.write_bytes(csv_bytes(fields, rows))
        report = self.run_audit()
        self.assertFalse(report["source_ready_for_label_rebuild"])
        self.assertEqual(report["overlap"]["conflicts"][0]["field"], "down_limit")
        self.assertEqual(report["overlap"]["unverified"][0]["field"], "up_limit")

    def test_candidate_row_presence_revision_is_not_silently_accepted(self):
        self.fixture()
        path = self.repo / f"data/market/raw/2026/{DATES[0]}/daily.csv"
        fields, rows = audit.csv_rows(path.read_bytes())
        path.write_bytes(csv_bytes(fields, rows[:1]))
        report = self.run_audit()
        self.assertFalse(report["source_ready_for_label_rebuild"])
        self.assertEqual(report["overlap"]["conflicts"][0]["field"], "row_presence")

    def test_unsafe_paths_symlinks_and_unmanifested_files_rejected(self):
        self.fixture()
        (self.bundle / "unexpected.txt").write_text("not allowed")
        self.assertIn("UNMANIFESTED_OR_MISSING_ARTIFACT_FILES", self.codes(self.run_audit()))
        with self.assertRaises(audit.AuditError):
            audit.safe_file(self.bundle, "../secret")
        link = self.base / "linked-bundle"
        link.symlink_to(self.bundle, target_is_directory=True)
        with self.assertRaises(audit.AuditError):
            audit.no_symlinks(link)

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(audit.AuditError):
            audit.decode(b'{"status": "okay", "status": "evil"}')
        with self.assertRaises(audit.AuditError):
            audit.normalized("open", "1e999999")

    def test_fixed_output_never_overwrites_or_follows_links(self):
        safe_here = self.base / "analysis"
        safe_here.mkdir()
        with mock.patch.object(audit, "HERE", safe_here):
            output = audit.write_report({"training_authorized": False})
            self.assertEqual(output, safe_here / "outputs/audit.json")
            with self.assertRaises(FileExistsError):
                audit.write_report({"tamper": True})
            self.assertEqual(json.loads(output.read_text()), {"training_authorized": False})


if __name__ == "__main__":
    unittest.main()
