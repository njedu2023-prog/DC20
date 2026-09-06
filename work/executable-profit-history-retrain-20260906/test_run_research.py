"""Offline admission/overlay/gate tests; no market fetch or real model fit."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("dc20_history_retrain_entrypoint_tests", Path(__file__).with_name("run_research.py"))
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)
REAL_REPO, REAL_HERE = R.HERE.parents[1], R.HERE


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dc20-retrain-test-")
        self.root = Path(self.temp.name).resolve()
        self.artifact = self.root / "artifact"
        self.artifact.mkdir()
        self.audit = self.root / "audit.json"
        self.plan = json.loads(REAL_HERE.joinpath("PLAN.json").read_text())
        self.sessions = [{"trade_date": "20221114", "codes": ["603778.SH"]}]
        controls = {"PLAN.json": b'{"test_source_plan":true}\n', "REQUEST.json": b'{"test_source_request":true}\n'}
        contents = {**controls, "session_plan.json": R.canonical(self.sessions) + b"\n", "candidate_sources/20221114/daily.csv": b"ts_code,trade_date,open\n603778.SH,20221114,\n", "candidate_sources/20221114/stk_limit.csv": b"ts_code,trade_date,up_limit,down_limit\n603778.SH,20221114,11,9\n"}
        for relative, payload in contents.items():
            path = self.artifact / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        contract = self.plan["source_admission"]
        contract.update(requested_sessions=1, requested_code_date_keys=1, expected_required_partitions=2, session_plan_sha256=R.sha_bytes(R.canonical(self.sessions)))
        for name, payload in controls.items():
            contract["source_control_sha256"][name] = R.sha_bytes(payload)
        identity = {"github_run_id": contract["expected_run_id"], "github_sha": contract["expected_run_sha"], "github_run_attempt": "1"}
        self.manifest = {"schema_version": "dc20_isolated_source_artifact_manifest_v1", "status": "COLLECTED_REQUIRED_SOURCES_WITH_GAPS", **identity, "source_data_only": True, "production_writes": False, "files": {name: {"sha256": R.sha_bytes(payload), "bytes": len(payload)} for name, payload in contents.items()}}
        self.manifest_path = self.artifact / "artifact_manifest.json"
        self.manifest_path.write_bytes(R.canonical(self.manifest) + b"\n")
        self.report = {"schema_version": "dc20_profit_history_artifact_audit_v1", "status": "SOURCE_VERIFIED_FOR_SEPARATE_LABEL_REBUILD", "source_ready_for_label_rebuild": True, "issues": [], "expected_run_id": contract["expected_run_id"], "expected_run_sha": contract["expected_run_sha"], "expected_run_attempt": "1", "expected_manifest_sha256": R.sha(self.manifest_path), "artifact_manifest_sha256": R.sha(self.manifest_path), "audit_code_sha256": contract["audit_script"]["sha256"], "source_repository": "njedu2023-prog/DC20", "source_commit": contract["source_commit"], "source_contract": {"plan_sha256": contract["source_control_sha256"]["PLAN.json"], "request_sha256": contract["source_control_sha256"]["REQUEST.json"], "collector_sha256": contract["source_control_sha256"]["collect.py"], "source_inputs": self.plan["source_inputs"]}, "session_plan_sha256": contract["session_plan_sha256"], "requested_sessions": 1, "requested_code_date_keys": 1, "expected_required_partitions": 2, "verified_required_partitions": 2, "missing_required_partitions": [], "missing_candidate_keys": [{"trade_date": "20221114", "ts_code": "603778.SH", "api": "daily", "status": "UNKNOWN_NOT_ZERO"}], "overlap": {"conflicts": [], "unverified": [], "reference_files": []}, "as_of_date": "20260904", "tail_sessions": 20, "training_authorized": False, "production_release_authorized": False, "actual_fill_observed": False, "historically_available_at_D": False, "tail_window_is_forced_exit": False, "collection_status": "COLLECTED_REQUIRED_SOURCES_WITH_GAPS"}
        self.save_report()

    def tearDown(self):
        self.temp.cleanup()

    def save_report(self):
        self.audit.write_bytes(R.canonical(self.report) + b"\n")

    def arguments(self):
        return {"expected_audit_sha256": R.sha(self.audit), "expected_manifest_sha256": R.sha(self.manifest_path), "expected_run_id": self.report["expected_run_id"], "expected_run_sha": self.report["expected_run_sha"], "expected_run_attempt": "1"}

    def admitted(self):
        return R.admit_source(REAL_REPO, self.plan, self.artifact, self.audit, **self.arguments())

    def fail_code(self, code, function, *args, **kwargs):
        with self.assertRaises(R.ResearchError) as error:
            function(*args, **kwargs)
        self.assertEqual(str(error.exception), code)


class SourceAdmissionTests(Base):
    def test_sha_bound_source_with_missing_values_is_not_zero_imputation(self):
        admitted = self.admitted()
        self.assertEqual(admitted["sessions"], self.sessions)
        self.assertTrue(admitted["report"]["source_ready_for_label_rebuild"])
        self.assertTrue(admitted["report"]["missing_candidate_keys"])
        self.assertFalse(admitted["report"]["training_authorized"])

    def test_source_not_ready_blocks_before_any_rebuild_or_fit(self):
        self.report.update(source_ready_for_label_rebuild=False, issues=[{"code": "SOURCE_FAILED"}])
        self.save_report()
        with mock.patch.object(R, "command") as command, mock.patch.object(R, "load_training_module") as training:
            self.fail_code("AUDIT_NOT_READY_OR_HAS_ISSUES", self.admitted)
        command.assert_not_called()
        training.assert_not_called()

    def test_self_asserted_flag_does_not_replace_external_audit_sha(self):
        expected = self.arguments()
        self.audit.write_bytes(self.audit.read_bytes() + b" ")
        self.fail_code("INPUT_SHA_MISMATCH:audit.json", R.admit_source, REAL_REPO, self.plan, self.artifact, self.audit, **expected)

    def test_wrong_external_run_or_manifest_refused(self):
        args = self.arguments()
        args["expected_run_sha"] = "0" * 40
        self.fail_code("EXTERNAL_RUN_DIFFERS_FROM_PREDECLARED_SOURCE", R.admit_source, REAL_REPO, self.plan, self.artifact, self.audit, **args)
        args = self.arguments()
        args["expected_manifest_sha256"] = "0" * 64
        self.fail_code("AUDIT_EXTERNAL_IDENTITY_MISMATCH", R.admit_source, REAL_REPO, self.plan, self.artifact, self.audit, **args)

    def test_overlap_or_incomplete_partition_blocks(self):
        self.report["overlap"]["conflicts"] = [{"field": "open"}]
        self.save_report()
        self.fail_code("OVERLAP_REQUIRES_SEPARATE_REVIEW", self.admitted)
        self.report["overlap"]["conflicts"] = []
        self.report["missing_required_partitions"] = [{"trade_date": "20221114", "api": "daily"}]
        self.save_report()
        self.fail_code("AUDIT_REQUIRED_PARTITIONS_INCOMPLETE", self.admitted)

    def test_changed_artifact_or_unmanifested_file_blocks(self):
        extra = self.artifact / "extra.json"
        extra.write_text("{}")
        self.fail_code("UNMANIFESTED_OR_MISSING_ARTIFACT_FILE", self.admitted)
        extra.unlink()
        daily = self.artifact / "candidate_sources/20221114/daily.csv"
        daily.write_bytes(daily.read_bytes() + b"\n")
        self.fail_code("INPUT_SHA_MISMATCH:candidate_sources/20221114/daily.csv", self.admitted)

    def test_artifact_symlink_and_traversal_refused(self):
        daily = self.artifact / "candidate_sources/20221114/daily.csv"
        original = daily.read_bytes()
        outside = self.root / "outside.csv"
        outside.write_bytes(original)
        daily.unlink()
        daily.symlink_to(outside)
        self.fail_code("SYMLINK_PATH_FORBIDDEN", self.admitted)
        self.fail_code("UNSAFE_RELATIVE_PATH", R.safe_file, self.artifact, "../outside.csv")

    def test_plan_changes_model_parameters_costs_features_or_maturity_rejected(self):
        valid = json.loads(REAL_HERE.joinpath("PLAN.json").read_text())
        legacy = json.loads(REAL_REPO.joinpath(R.LEGACY_PACKAGE, "PLAN.json").read_text())
        for key in ("source_inputs", "label_policy", "training", "hgb_parameters", "future_acceptance", "boundaries"):
            self.assertEqual(valid[key], legacy[key])
        for section, key, value in [("training", "min_train_complete_dates", 251), ("training", "feature_count", 156), ("training", "downside_penalty", .4), ("hgb_parameters", "max_iter", 1000), ("label_policy", "base_all_in_assumed_cost_rate", 0.)]:
            altered = copy.deepcopy(valid)
            altered[section][key] = value
            self.fail_code("FROZEN_V2_POLICY_CHANGED:" + section, R.assert_plan, REAL_REPO, altered)


class IsolationTests(Base):
    def test_overlay_contains_only_admitted_candidate_partitions_and_exact_scripts(self):
        workspace = R.empty_workspace(self.root / "isolated", repo=REAL_REPO, artifact_root=self.artifact)
        old_script_hashes = {name: R.sha(REAL_REPO / R.LEGACY_PACKAGE / name) for name in R.LEGACY_HASHES}
        overlay, study, bindings, mapping = R.stage_overlay(REAL_REPO, self.plan, R.canonical(self.plan), self.artifact, self.admitted(), workspace)
        R.verify_overlay(overlay, bindings)
        self.assertEqual(len(mapping), 2)
        self.assertEqual(len(list((overlay / "data/market/raw").rglob("*.csv"))), 2)
        for name in ("build_labels.py", "train_candidate.py"):
            self.assertEqual(R.sha(study / name), R.LEGACY_HASHES[name])
            self.assertEqual((study / name).stat().st_nlink, 1)
        self.assertIn(b"603778.SH,20221114,\n", (overlay / "data/market/raw/2022/20221114/daily.csv").read_bytes())
        self.assertEqual(old_script_hashes, {name: R.sha(REAL_REPO / R.LEGACY_PACKAGE / name) for name in R.LEGACY_HASHES})
        self.assertFalse((study / "outputs").exists())
        injected = overlay / "data/market/raw/2022/20221115"
        injected.mkdir(parents=True)
        (injected / "daily.csv").write_text("old data must not be mixed")
        self.fail_code("UNBOUND_OR_OLD_MARKET_PARTITION_IN_OVERLAY", R.verify_overlay, overlay, bindings)

    def test_workspace_rejects_repository_artifact_nonempty_and_symlink(self):
        self.fail_code("WORKSPACE_OVERLAPS_INPUTS_OR_REPOSITORY", R.empty_workspace, self.artifact / "overlay", repo=REAL_REPO, artifact_root=self.artifact)
        self.fail_code("WORKSPACE_OVERLAPS_INPUTS_OR_REPOSITORY", R.empty_workspace, REAL_REPO / "overlay", repo=REAL_REPO, artifact_root=self.artifact)
        workspace = self.root / "nonempty"
        workspace.mkdir()
        (workspace / "preserve").write_text("keep")
        self.fail_code("WORKSPACE_NOT_EMPTY_REFUSE_OVERWRITE", R.empty_workspace, workspace, repo=REAL_REPO, artifact_root=self.artifact)
        link = self.root / "alias"
        link.symlink_to(workspace)
        self.fail_code("SYMLINK_PATH_FORBIDDEN", R.empty_workspace, link, repo=REAL_REPO, artifact_root=self.artifact)
        self.assertEqual((workspace / "preserve").read_text(), "keep")

    def test_child_process_has_no_market_or_github_credentials(self):
        with mock.patch.dict(os.environ, {"TUSHARE_TOKEN": "fake-sensitive-market", "GITHUB_TOKEN": "fake-sensitive-github", "GH_TOKEN": "fake-sensitive-cli"}):
            env = R.child_environment()
        self.assertTrue({"TUSHARE_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"}.isdisjoint(env))
        self.assertEqual(env["OPENBLAS_NUM_THREADS"], "2")

    def test_package_outputs_never_overwrite_existing_result(self):
        package = self.root / "package"
        package.mkdir()
        with mock.patch.object(R, "HERE", package):
            output = R.package_output_directory()
            (output / "earlier.json").write_text("preserve")
            self.fail_code("EXISTING_PACKAGE_OUTPUTS_REFUSE_OVERWRITE", R.package_output_directory)
        self.assertEqual((output / "earlier.json").read_text(), "preserve")

    def test_fixed_legacy_cli_arguments_and_failure_logs(self):
        workspace = self.root / "child"
        workspace.mkdir()
        study, overlay = workspace / "study", workspace / "overlay"
        fake = mock.Mock(returncode=2, stdout=b"safe failure", stderr=b"")
        with mock.patch.object(R.subprocess, "run", return_value=fake) as run:
            self.fail_code("LEGACY_ENTRYPOINT_FAILED:build_labels.py", R.command, study, overlay, "build_labels.py", workspace)
        argv = run.call_args.args[0]
        self.assertEqual(argv[1:], ["-B", str(study / "build_labels.py"), "--repo", str(overlay)])
        self.assertFalse(run.call_args.kwargs["check"])
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertEqual((workspace / "logs/build_labels.py.stdout.txt").read_bytes(), b"safe failure")


class ReadinessFlowTests(Base):
    def test_original_builder_keeps_null_required_price_unknown_not_cash_zero(self):
        path = REAL_REPO / R.LEGACY_PACKAGE / "build_labels.py"
        self.assertEqual(R.sha(path), R.LEGACY_HASHES["build_labels.py"])
        spec = importlib.util.spec_from_file_location("dc20_history_retrain_null_label_fixture", path)
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        frozen = {"signal_date": "20221111", "exec_date": "20221114", "scheduled_exit_date": "20221115", "ts_code": "603778.SH", "stage": "2", "promotion_rank": "1", "top10_members_sha256": "f" * 64}
        daily = {"open": "", "high": "11", "low": "9", "close": "10", "pre_close": "10", "vol": "100"}
        limits = {"up_limit": "11", "down_limit": "9"}
        evidence = mock.Mock()
        evidence.candidate.return_value = daily, limits
        result = builder.label_row(frozen, ["20221111", "20221114", "20221115"], "20221115", evidence)
        self.assertEqual(result["label_status"], "INVALID_T_TRUTH")
        self.assertNotIn(result["label_status"], builder.TERMINAL)
        self.assertIsNone(result["slot_net_return"])
        self.assertIsNone(result["slot_net_return_stress"])
        self.assertIsNone(result["proxy_fill"])
        self.assertIsNone(result["label_available_date"])

    def test_readiness_pure_functions_never_fit_or_evaluate(self):
        readiness = {"models_fit": 0, "release_allowed": False, "ready": False}
        module = mock.Mock()
        module.load_inputs.return_value = ({}, object(), {"read_only_market_to_label_replay_verified": True})
        module.assess_readiness.return_value = readiness
        with mock.patch.object(R, "load_training_module", return_value=module):
            result = R.inspect_readiness(self.root / "study", self.root / "overlay")
        self.assertFalse(result["ready"])
        module.fit_heads.assert_not_called()
        module.evaluate_frames.assert_not_called()
        module.write_json.assert_called_once()

    def cli(self, package, workspace, train):
        args = ["run_research.py", "--artifact-root", str(self.artifact), "--audit-report", str(self.audit), "--workspace", str(workspace)]
        for key, value in self.arguments().items():
            args += ["--" + key.replace("_", "-"), value]
        return args + (["--train-after-gates"] if train else [])

    def test_main_source_not_ready_has_zero_fits_and_no_workspace_or_output(self):
        package = self.root / "repo/work/new_study"
        package.mkdir(parents=True)
        (package / "PLAN.json").write_bytes(R.canonical(self.plan))
        self.report["source_ready_for_label_rebuild"] = False
        self.save_report()
        workspace = self.root / "must-not-exist"
        with mock.patch.object(R, "HERE", package), mock.patch.object(R, "assert_plan"), mock.patch.object(R, "command") as command, mock.patch.object(R, "stage_overlay") as stage, mock.patch("sys.argv", self.cli(package, workspace, True)), contextlib.redirect_stdout(io.StringIO()) as logged:
            self.assertEqual(R.main(), 2)
        command.assert_not_called()
        stage.assert_not_called()
        self.assertIn('"models_fit": 0', logged.getvalue())
        self.assertFalse(workspace.exists())
        self.assertFalse((package / "outputs").exists())

    def test_main_training_requires_both_explicit_flag_and_original_gate(self):
        for ready, train, expected_commands, expected_fits in [(False, True, ["build_labels.py"], 0), (True, False, ["build_labels.py"], 0), (True, True, ["build_labels.py", "train_candidate.py"], 10)]:
            with self.subTest(ready=ready, train=train):
                label = f"{ready}-{train}"
                package = self.root / label / "repo/work/new_study"
                package.mkdir(parents=True)
                (package / "PLAN.json").write_bytes(R.canonical(self.plan))
                workspace = self.root / ("workspace-" + label)
                calls = []
                base_readiness = {"ready": ready, "models_fit": 0, "release_allowed": False, "model_weights_saved": False, "result_artifacts_valid": False, "provenance": {"read_only_market_to_label_replay_verified": True}}
                def stage(repo, plan, payload, source, admitted, destination):
                    overlay = destination / "overlay"
                    study = overlay / "study"
                    (study / "outputs").mkdir(parents=True)
                    (study / "PLAN.json").write_bytes(payload)
                    return overlay, study, {}, []
                def command(study, overlay, name, destination):
                    calls.append(name)
                    (destination / "logs").mkdir(exist_ok=True)
                    if name == "train_candidate.py":
                        value = {**base_readiness, "models_fit": 10, "result_artifacts_valid": True}
                        (study / "outputs/training_readiness.json").write_bytes(R.canonical(value))
                def inspect(study, overlay):
                    (study / "outputs/training_readiness.json").write_bytes(R.canonical(base_readiness))
                    return dict(base_readiness)
                with mock.patch.object(R, "HERE", package), mock.patch.object(R, "assert_plan"), mock.patch.object(R, "stage_overlay", side_effect=stage), mock.patch.object(R, "verify_overlay"), mock.patch.object(R, "command", side_effect=command), mock.patch.object(R, "inspect_readiness", side_effect=inspect), mock.patch("sys.argv", self.cli(package, workspace, train)), contextlib.redirect_stdout(io.StringIO()):
                    result = R.main()
                manifest = json.loads((package / "outputs/run_manifest.json").read_text())
                self.assertEqual(calls, expected_commands)
                self.assertEqual(manifest["models_fit"], expected_fits)
                self.assertFalse(manifest["release_allowed"])
                self.assertFalse(manifest["model_weights_saved"])
                self.assertEqual(result, 0 if ready else 2)


if __name__ == "__main__":
    unittest.main()
