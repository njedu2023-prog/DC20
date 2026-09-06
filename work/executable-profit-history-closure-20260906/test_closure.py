"""Offline synthetic two-run closure tests; no market call, label fit, or release."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


C = module(HERE / "audit_closure.py", "dc20_closure_test_auditor")
RUN = module(HERE / "run_research.py", "dc20_closure_test_runner")
A = module(REPO / "work/executable-profit-history-analysis-20260906/audit_artifact.py", "dc20_closure_test_source_auditor")
T = module(REPO / "work/executable-profit-history-tail-20260906/collect_tail.py", "dc20_closure_test_tail_collector")
R = module(REPO / "work/executable-profit-history-retrain-20260906/run_research.py", "dc20_closure_test_legacy_helpers")


class Clock:
    def __init__(self):
        self.value = 0.
    def __call__(self):
        return self.value
    def sleep(self, seconds):
        self.value += seconds


class TailFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dc20-closure-fixture-")
        self.root = Path(self.temp.name).resolve()
        self.artifact = self.root / "tail"
        self.artifact.mkdir()
        self.plan = json.loads((HERE / "PLAN.json").read_text())
        self.tail_plan_path = REPO / "work/executable-profit-history-tail-20260906/PLAN.json"
        self.tail_plan = json.loads(self.tail_plan_path.read_text())
        self.expected = T.build_request(REPO, self.tail_plan)
        self.full_request = A.load_contract(REPO)["request"]
        self.identity = {"run_id": "999999", "run_sha": "a" * 40, "run_attempt": "1", "manifest_sha256": ""}
        self.null = False
        self.missing = False

    def tearDown(self):
        self.temp.cleanup()

    def fail_code(self, code, fn, *args, **kwargs):
        with self.assertRaises((C.ClosureError, A.AuditError, T.SOURCE.CollectionError)) as error:
            fn(*args, **kwargs)
        self.assertEqual(str(error.exception), code)

    def make_artifact(self):
        artifacts = T.SOURCE.Artifacts(self.artifact)
        request = {**T.request_contract(self.expected), "_activation": {"state": "ACTIVATED"}}
        artifacts.write("PLAN.json", self.tail_plan_path.read_bytes())
        artifacts.json("REQUEST.json", request)
        artifacts.json("partition_plan.json", self.expected["partitions"])
        cfg = self.plan["composite_source"]["tail"]
        cfg.update(run_id=self.identity["run_id"], run_sha=self.identity["run_sha"], run_attempt="1", plan_sha256=C.sha((self.artifact / "PLAN.json").read_bytes()), request_sha256=C.sha((self.artifact / "REQUEST.json").read_bytes()), collector_sha256=C.sha((REPO / "work/executable-profit-history-tail-20260906/collect_tail.py").read_bytes()), partition_plan_sha256=C.sha(C.canonical(self.expected["partitions"])))
        provenance = {"previous_source": T.PREVIOUS_SOURCE, "source_collector_sha256": T.SOURCE_COLLECTOR_SHA, "full_session_plan_sha256": T.FULL_SESSION_SHA, "partition_plan_sha256": cfg["partition_plan_sha256"], "tail_plan_sha256": cfg["plan_sha256"], "tail_request_sha256": cfg["request_sha256"], "tail_collector_sha256": cfg["collector_sha256"], "source_inputs": T.SOURCE.INPUTS, "old_failure_status_preserved": True, "cross_run_pagination": False}
        keys = {(p["trade_date"], p["api"]): p["codes"] for p in self.expected["partitions"]}
        def transport(body):
            sent = json.loads(body)
            api, p = sent["api_name"], sent["params"]
            canary = "ts_code" in p
            codes = [p["ts_code"]] if canary else list(keys[(p["trade_date"], api)])
            if not canary and p["offset"]:
                codes = []
            if self.missing and not canary and api == "daily":
                codes = codes[1:]
            rows = []
            for i, code in enumerate(codes):
                values = {"ts_code": code, "trade_date": p["trade_date"], "open": 10., "high": 11., "low": 9., "close": 10., "pre_close": 10., "change": 0., "pct_chg": 0., "vol": 100., "amount": 1000., "up_limit": 11., "down_limit": 9.}
                if self.null and not canary and api == "daily" and i == 0:
                    values["open"] = None
                rows.append([values[f] for f in T.SOURCE.FIELDS[api]])
            return json.dumps({"code": 0, "msg": "", "data": {"fields": T.SOURCE.FIELDS[api], "items": rows}}).encode()
        clock = Clock()
        client = T.TailClient("OFFLINE_TEST_ONLY_NOT_REAL_CREDENTIAL", artifacts, self.expected, transport=transport, clock=clock, sleep=clock.sleep, environment={"GITHUB_RUN_ID": self.identity["run_id"], "GITHUB_SHA": self.identity["run_sha"], "GITHUB_RUN_ATTEMPT": "1"})
        with contextlib.redirect_stdout(io.StringIO()):
            result, self.status = T.perform_tail(client, self.expected, artifacts, provenance)
        self.assertEqual(result, 0)
        self.identity["manifest_sha256"] = C.sha((self.artifact / "artifact_manifest.json").read_bytes())
        cfg["manifest_sha256"] = self.identity["manifest_sha256"]

    def verify(self):
        return C.verify_tail(A, T, REPO, self.artifact, self.plan, self.identity, self.full_request)

    def rewrite(self, relative, transform):
        path = self.artifact / relative
        payload = transform(path.read_bytes())
        path.write_bytes(payload)
        manifest_path = self.artifact / "artifact_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"][relative] = {"sha256": C.sha(payload), "bytes": len(payload)}
        manifest_path.write_bytes(C.canonical(manifest) + b"\n")
        digest = C.sha(manifest_path.read_bytes())
        self.identity["manifest_sha256"] = digest
        self.plan["composite_source"]["tail"]["manifest_sha256"] = digest

    def test_tail_exact_nineteen_partitions_raw_csv_and_pagination_replay(self):
        self.make_artifact()
        manifest, tables, full, missing, nulls = self.verify()
        self.assertEqual(len(tables), 19)
        self.assertEqual(set(tables), {(p["trade_date"], p["api"]) for p in self.expected["partitions"]})
        self.assertNotIn(("20260824", "daily"), tables)
        self.assertEqual(sum(map(len, tables.values())), 1533)
        self.assertEqual((missing, nulls), ([], []))
        self.assertEqual(manifest["requests_attempted"], 21)

    def test_bulk_null_and_absent_candidate_stay_unknown(self):
        self.null = self.missing = True
        self.make_artifact()
        _, _, _, missing, nulls = self.verify()
        self.assertEqual(len(missing), 9)
        self.assertEqual(len(nulls), 9)
        self.assertTrue(all(item["status"] == "UNKNOWN_NOT_ZERO" for item in missing))
        self.assertTrue(all(item["fields"] == ["open"] for item in nulls))
        self.assertFalse(self.status["required_candidate_coverage_complete"])

    def test_missing_field_cannot_be_silently_filled_with_zero(self):
        self.null = True
        self.make_artifact()
        relative = "candidate_sources/20260825/daily.csv"
        self.rewrite(relative, lambda data: data.replace(b",20260825,,", b",20260825,0,", 1))
        self.fail_code("TAIL_CSV_DOES_NOT_REPLAY_RAW", self.verify)

    def test_cross_run_offset_cannot_start_in_the_middle(self):
        self.make_artifact()
        def change(data):
            receipt = json.loads(data)
            receipt["params"]["offset"] = 5000
            receipt["request_without_credential_sha256"] = C.sha(C.canonical({"api_name": receipt["api_name"], "params": receipt["params"], "fields": ",".join(receipt["fields"])}))
            return C.canonical(receipt) + b"\n"
        self.rewrite("receipts/000003.json", change)
        self.fail_code("TAIL_PARTIAL_OR_CONTINUED_PAGINATION", self.verify)

    def test_csv_must_match_raw_even_with_recomputed_artifact_hash(self):
        self.make_artifact()
        self.rewrite("candidate_sources/20260825/daily.csv", lambda data: data.replace(b",10.0,11.0,", b",9.5,11.0,", 1))
        self.fail_code("TAIL_CSV_DOES_NOT_REPLAY_RAW", self.verify)

    def test_tail_cannot_add_original_completed_partition(self):
        self.make_artifact()
        def extra(data):
            value = json.loads(data)
            value.append({"trade_date": "20260824", "api": "daily", "codes": ["000009.SZ"]})
            return C.canonical(value) + b"\n"
        self.rewrite("partition_plan.json", extra)
        self.fail_code("TAIL_PARTITION_PLAN_CHANGED", self.verify)

    def test_wrong_run_and_mutated_payload_fail(self):
        self.make_artifact()
        wrong = {**self.identity, "run_sha": "b" * 40}
        self.fail_code("EXTERNAL_SOURCE_EXECUTION_MISMATCH:run_sha", C.verify_tail, A, T, REPO, self.artifact, self.plan, wrong, self.full_request)
        raw = self.artifact / "responses/000001.json.gz"
        raw.write_bytes(raw.read_bytes() + b"bad")
        self.fail_code("PIN_MISMATCH:responses/000001.json.gz", self.verify)

    def test_pending_tail_identity_blocks_before_any_audit_or_model(self):
        pending = json.loads((HERE / "PLAN.json").read_text())["composite_source"]["tail"]
        pending["run_id"] = None
        self.fail_code("SOURCE_EXECUTION_NOT_YET_BOUND:run_id", C.external_identity, pending, self.identity)


class ContractAndCompositionTests(unittest.TestCase):
    def test_gap_union_is_exact_and_disjoint(self):
        old, tail = {("20260824", "daily")}, {("20260824", "stk_limit")}
        expected = old | tail
        self.assertEqual(C.compose_partition_sources(expected, old, tail), {("20260824", "daily"): "original", ("20260824", "stk_limit"): "tail"})
        with self.assertRaisesRegex(C.ClosureError, "CROSS_RUN_OVERWRITE"):
            C.compose_partition_sources(expected, old, expected)
        with self.assertRaisesRegex(C.ClosureError, "NOT_EXACT_COMPLETE_SCOPE"):
            C.compose_partition_sources(expected, old, set())

    def test_all_training_and_label_rules_equal_frozen_original(self):
        plan = json.loads((HERE / "PLAN.json").read_text())
        RUN.validate_training_contract(REPO, plan, R)
        for section, key, value in [("hgb_parameters", "max_iter", 400), ("training", "min_train_complete_dates", 251), ("training", "evaluation_complete_dates", 179), ("training", "feature_count", 156), ("training", "downside_penalty", .4), ("label_policy", "stress_all_in_assumed_cost_rate", .0045)]:
            changed = copy.deepcopy(plan)
            changed[section][key] = value
            with self.assertRaisesRegex(RUN.C.ClosureError, "FROZEN_TRAINING_OR_LABEL_POLICY_CHANGED"):
                RUN.validate_training_contract(REPO, changed, R)

    def test_original_failure_and_three_issues_must_remain_exact(self):
        plan = json.loads((HERE / "PLAN.json").read_text())
        audit_path = REPO / "work/executable-profit-history-analysis-20260906/outputs/audit.json"
        stored = json.loads(audit_path.read_text())
        cfg = plan["composite_source"]["original"]
        replay = {k: v for k, v in stored.items() if k != "audited_at_utc"}
        self.assertEqual(replay["audit_code_sha256"], cfg["audit_script_sha256"])
        mocked = mock.Mock(wraps=A)
        mocked.load_contract.return_value = {"read_only_fixture": True}
        mocked.audit_bundle.return_value = copy.deepcopy(replay)
        identity = {k: cfg[k] for k in ("run_id", "run_sha", "run_attempt", "manifest_sha256")}
        result, _ = C.validated_original(mocked, REPO, Path("/unused/offline-fixture"), audit_path, plan, identity, cfg["audit_sha256"])
        self.assertFalse(result["source_ready_for_label_rebuild"])
        self.assertEqual(result["issues"], C.EXPECTED_ORIGINAL_ISSUES)
        self.assertEqual(result["collection_status"], "BLOCKED_COLLECTION")
        mocked.audit_bundle.return_value["source_ready_for_label_rebuild"] = True
        with self.assertRaisesRegex(C.ClosureError, "ORIGINAL_READONLY_AUDIT_REPLAY_DIFFERS"):
            C.validated_original(mocked, REPO, Path("/unused/offline-fixture"), audit_path, plan, identity, cfg["audit_sha256"])
        self.assertEqual(json.loads(audit_path.read_text()), stored)

    def test_workspace_overlap_rejected_before_any_input_directory_creation(self):
        with tempfile.TemporaryDirectory(prefix="dc20-closure-workspace-test-") as directory:
            base = Path(directory).resolve()
            roots = {key: base / key for key in ("repo", "original_root", "tail_root")}
            for root in roots.values():
                root.mkdir()
            for key, source in roots.items():
                with self.subTest(source=key):
                    target = source / "new-workspace"
                    with mock.patch.object(R, "empty_workspace", wraps=R.empty_workspace) as creator:
                        with self.assertRaisesRegex(RUN.C.ClosureError, "WORKSPACE_OVERLAPS_"):
                            RUN.prepare_workspace(target, **roots, R=R)
                        creator.assert_not_called()
                    self.assertFalse(target.exists())
                    self.assertEqual(list(source.iterdir()), [])
            external = base / "valid-external-workspace"
            self.assertEqual(RUN.prepare_workspace(external, **roots, R=R), external)
            self.assertTrue(external.is_dir())

    def test_workspace_dot_segments_cannot_bypass_source_boundary(self):
        with tempfile.TemporaryDirectory(prefix="dc20-closure-dotsegments-test-") as directory:
            base = Path(directory).resolve()
            roots = {key: base / key for key in ("repo", "original_root", "tail_root")}
            for root in roots.values():
                root.mkdir()
            outside = base / "outside"
            outside.mkdir()
            for invalid in (str(outside) + "/../repo/new-workspace", str(roots["tail_root"]) + "/./new-workspace", "relative-workspace"):
                with self.subTest(path=invalid), mock.patch.object(R, "empty_workspace", wraps=R.empty_workspace) as creator:
                    with self.assertRaisesRegex(RUN.C.ClosureError, "WORKSPACE_PATH_MUST_BE_NORMALIZED_ABSOLUTE"):
                        RUN.prepare_workspace(invalid, **roots, R=R)
                    creator.assert_not_called()
            self.assertFalse((roots["repo"] / "new-workspace").exists())
            self.assertFalse((roots["tail_root"] / "new-workspace").exists())

    def test_unadmitted_composite_cannot_create_overlay(self):
        with tempfile.TemporaryDirectory(prefix="dc20-closure-stage-test-") as directory:
            workspace = Path(directory).resolve()
            with self.assertRaisesRegex(RUN.C.ClosureError, "COMPOSITE_SOURCE_NOT_ADMITTED"):
                RUN.stage_composite(REPO, {}, b"{}", {"source_ready_for_label_rebuild": False}, {}, workspace, R)
            self.assertFalse((workspace / "overlay").exists())

    def test_distinct_audit_and_research_outputs_never_overwrite(self):
        with tempfile.TemporaryDirectory(prefix="dc20-closure-output-test-") as directory:
            package = Path(directory).resolve()
            with mock.patch.object(RUN, "HERE", package):
                audit = RUN.output_directory("audit")
                (audit / "existing.json").write_text("keep")
                research = RUN.output_directory("research")
                self.assertNotEqual(audit, research)
                with self.assertRaisesRegex(RUN.C.ClosureError, "EXISTING_CLOSURE_OUTPUT"):
                    RUN.output_directory("audit")
            self.assertEqual((audit / "existing.json").read_text(), "keep")

    def test_pending_source_cli_performs_zero_fits_and_creates_no_outputs(self):
        with tempfile.TemporaryDirectory(prefix="dc20-closure-pending-test-") as directory:
            package = Path(directory).resolve()
            plan = json.loads((HERE / "PLAN.json").read_text())
            plan["composite_source"]["tail"]["run_id"] = None
            (package / "PLAN.json").write_text(json.dumps(plan))
            argv = ["run_research.py", "--original-root", "/unused/original", "--original-audit", "/unused/audit.json", "--original-audit-sha256", "0" * 64, "--tail-root", "/unused/tail", "--original-run-id", "34023469106", "--original-run-sha", "d5f3df57c78b0458d1329034c94ec324827aa390", "--original-manifest-sha256", "0" * 64, "--tail-run-id", "9999", "--tail-run-sha", "a" * 40, "--tail-manifest-sha256", "0" * 64, "--train-after-gates"]
            with mock.patch.object(RUN, "HERE", package), mock.patch("sys.argv", argv), mock.patch.object(RUN.C, "audit_closure") as audit, mock.patch.object(RUN, "stage_composite") as stage, contextlib.redirect_stdout(io.StringIO()) as stdout:
                result = RUN.main()
            self.assertEqual(result, 2)
            self.assertIn('"models_fit": 0', stdout.getvalue())
            self.assertIn('"source_ready_for_label_rebuild": false', stdout.getvalue())
            self.assertFalse((package / "outputs").exists())
            audit.assert_not_called()
            stage.assert_not_called()


if __name__ == "__main__":
    unittest.main()
