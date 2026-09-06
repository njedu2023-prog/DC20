"""Network-free authorization and production-isolation regression tests."""
import copy
import hashlib
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("dc20_history_source_guard_tested", HERE / "guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.install = "a" * 40
        self.head = "b" * 40
        self.env = {"GITHUB_REPOSITORY": "njedu2023-prog/DC20", "GITHUB_EVENT_NAME": "push",
                    "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": self.head, "GITHUB_RUN_ATTEMPT": "1"}
        self.event = {"repository": {"full_name": "njedu2023-prog/DC20"}, "ref": "refs/heads/main",
                      "before": self.install, "after": self.head, "forced": False, "deleted": False}
        self.now = datetime(2026, 9, 6, 9, tzinfo=timezone.utc)
        hashes = {}
        for relative in guard.FILES:
            p = self.root / relative
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(relative)
            hashes[relative] = hashlib.sha256(p.read_bytes()).hexdigest()
        self.request = {"_activation": {"installation_commit": self.install, "source_commit": guard.BASE,
                        "request_nonce": "dc20-profit-history-source-20260906-null-evidence-v3", "files_sha256": hashes,
                        "supersedes_collection_run_id": guard.FAILED_COLLECTION_RUN, "previous_collection_started": True,
                        "previous_manifest_sha256": guard.FAILED_MANIFEST_SHA,
                        "previous_requests_attempted": 83, "previous_sessions_completed": 25,
                        "previous_artifact_reused_for_training": False}}
        self.responses = {
            ("rev-parse", "HEAD"): self.head,
            ("status", "--porcelain=v1"): "",
            ("show", "-s", "--format=%B", self.install): "Install isolated research collector [skip ci]",
            ("rev-list", "--parents", "-n", "1", self.head): self.head + " " + self.install,
            ("rev-list", "--parents", "-n", "1", self.install): self.install + " " + guard.INSTALL_BASE,
            ("diff", "--name-status", self.install, self.head): "M\t" + guard.REQUEST,
            ("diff", "--name-status", guard.INSTALL_BASE, self.install): "\n".join("M\t" + p for p in guard.INSTALL_UPDATES),
        }

    def check(self):
        (self.root / guard.REQUEST).write_text(json.dumps(self.request))
        with patch.object(guard, "git", side_effect=lambda root, *args: self.responses[args]):
            return guard.validate(self.root, self.env, self.event, self.now)

    def test_exact_one_time_request(self):
        self.assertEqual(self.check()["status"], "ONE_TIME_RESEARCH_REQUEST_AUTHORIZED")

    def test_wrong_identity_or_rerun(self):
        cases = [("GITHUB_REPOSITORY", "elsewhere/repo"), ("GITHUB_EVENT_NAME", "workflow_dispatch"),
                 ("GITHUB_REF", "refs/heads/other"), ("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_SHA", "c" * 40)]
        for key, value in cases:
            with self.subTest(key=key), patch.dict(self.env, {key: value}), self.assertRaises(ValueError):
                self.check()

    def test_forced_or_deleted_push(self):
        for key in ("forced", "deleted"):
            with self.subTest(key=key), patch.dict(self.event, {key: True}), self.assertRaises(ValueError):
                self.check()

    def test_wrong_event_before_or_repository(self):
        for data in ({"before": "c" * 40}, {"repository": {"full_name": "wrong/repo"}}, {"ref": "refs/heads/x"}):
            with self.subTest(data=data), patch.dict(self.event, data), self.assertRaises(ValueError):
                self.check()

    def test_outside_sunday_window(self):
        for now in (datetime(2026, 9, 6, 7, 59, tzinfo=timezone.utc), datetime(2026, 9, 6, 12, tzinfo=timezone.utc),
                    datetime(2026, 9, 7, 9, tzinfo=timezone.utc)):
            with self.subTest(now=now), self.assertRaises(ValueError):
                self.now = now
                self.check()

    def test_merge_or_wrong_parent(self):
        for key in (("rev-list", "--parents", "-n", "1", self.head), ("rev-list", "--parents", "-n", "1", self.install)):
            with self.subTest(key=key), patch.dict(self.responses, {key: self.responses[key] + " " + "c" * 40}), self.assertRaises(ValueError):
                self.check()

    def test_dirty_checkout_and_unisolated_installation(self):
        for key, value in ((("status", "--porcelain=v1"), " M models/model.json"),
                           (("show", "-s", "--format=%B", self.install), "ordinary push")):
            with self.subTest(key=key), patch.dict(self.responses, {key: value}), self.assertRaises(ValueError):
                self.check()

    def test_installation_and_file_sha_shapes(self):
        for value in ("--help", "a" * 39, "A" * 40, None):
            with self.subTest(value=value), patch.dict(self.request["_activation"], {"installation_commit": value}), self.assertRaises(ValueError):
                self.check()
        for value in ("x" * 64, "a" * 63, None):
            with self.subTest(value=value), patch.dict(self.request["_activation"]["files_sha256"], {guard.FILES[0]: value}), self.assertRaises(ValueError):
                self.check()

    def test_activation_cannot_change_other_files_or_add_an_unreviewed_request(self):
        key = ("diff", "--name-status", self.install, self.head)
        for output in ("A\t" + guard.REQUEST, "M\t" + guard.REQUEST + "\nM\tdecision.html"):
            with self.subTest(output=output), patch.dict(self.responses, {key: output}), self.assertRaises(ValueError):
                self.check()

    def test_installation_cannot_modify_production(self):
        key = ("diff", "--name-status", guard.INSTALL_BASE, self.install)
        with patch.dict(self.responses, {key: self.responses[key] + "\nM\tmodels/decision_model_freeze.json"}), self.assertRaises(ValueError):
            self.check()

    def test_hash_and_binding_drift(self):
        for name, value in (("source_commit", "c" * 40), ("request_nonce", "another-run"), ("files_sha256", {}),
                            ("supersedes_collection_run_id", 0), ("previous_collection_started", False),
                            ("previous_manifest_sha256", "0" * 64), ("previous_requests_attempted", 0),
                            ("previous_sessions_completed", 0), ("previous_artifact_reused_for_training", True)):
            with self.subTest(name=name), patch.dict(self.request["_activation"], {name: value}), self.assertRaises(ValueError):
                self.check()
        (self.root / guard.FILES[0]).write_text("modified")
        with self.assertRaisesRegex(ValueError, "HASH"):
            self.check()

    def test_symlink_source_forbidden(self):
        target = self.root / guard.FILES[0]
        original = target.with_suffix(".original")
        target.rename(original)
        target.symlink_to(original)
        with self.assertRaisesRegex(ValueError, "UNSAFE"):
            self.check()


class WorkflowIsolationTest(unittest.TestCase):
    def test_workflow_has_no_production_trigger_or_write_authority(self):
        root = HERE.parents[1]
        text = (root / guard.WORKFLOW).read_text()
        self.assertIn('"' + guard.REQUEST + '"', text)
        self.assertIn("contents: read", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("github.run_attempt == 1", text)
        self.assertIn("fetch-depth: 3", text)
        self.assertIn("timeout-minutes: 110", text)
        self.assertEqual(text.count("secrets.TUSHARE_TOKEN"), 1)
        for forbidden in ("contents: write", "actions: write", "id-token: write", "schedule:", "workflow_run:",
                          "workflow_dispatch:", "decision-auction-main-writer", "git push", "gh workflow", "scripts/publish"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
