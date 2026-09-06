"""Hermetic git-history/identity fixtures; never edits repository controls."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

spec = importlib.util.spec_from_file_location("tail_guard", Path(__file__).with_name("guard.py"))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
REPO = Path(__file__).resolve().parents[2]


class TailGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dc20-tail-guard.")
        self.root = Path(self.temp.name).resolve()
        self.head, self.install = "a" * 40, "b" * 40
        self.env = {"GITHUB_REPOSITORY": "njedu2023-prog/DC20", "GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main",
                    "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": self.head}
        self.event = {"repository": {"full_name": self.env["GITHUB_REPOSITORY"]}, "ref": self.env["GITHUB_REF"],
                      "after": self.head, "before": self.install, "forced": False, "deleted": False}
        self.now = datetime(2026, 9, 6, 11, tzinfo=timezone.utc)
        pins = {}
        for name in guard.FILES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = (REPO / name).read_bytes()
            path.write_bytes(payload)
            pins[name] = hashlib.sha256(payload).hexdigest()
        self.request = {"_activation": {"state": "ACTIVATED", "request_nonce": guard.NONCE, "installation_commit": self.install,
                         "installation_base": guard.INSTALL_BASE, "previous_run_id": guard.PREVIOUS_RUN,
                         "previous_manifest_sha256": guard.PREVIOUS_MANIFEST_SHA, "old_failure_status_preserved": True,
                         "files_sha256": pins}}
        self.save_request()
        self.responses = {
            ("rev-parse", "HEAD"): self.head, ("status", "--porcelain=v1"): "",
            ("rev-list", "--parents", "-n", "1", self.head): self.head + " " + self.install,
            ("rev-list", "--parents", "-n", "1", self.install): self.install + " " + guard.INSTALL_BASE,
            ("show", "-s", "--format=%B", self.install): "Install tail research only [skip ci]",
            ("diff", "--name-status", self.install, self.head): "M\t" + guard.REQUEST,
            ("diff", "--name-status", guard.INSTALL_BASE, self.install): "\n".join("A\t" + p for p in guard.INSTALL_FILES),
        }

    def tearDown(self):
        self.temp.cleanup()

    def save_request(self):
        (self.root / guard.REQUEST).write_text(json.dumps(self.request))

    def validate(self):
        with patch.object(guard, "git", side_effect=lambda root, *args: self.responses[args]):
            return guard.validate(self.root, self.env, self.event, self.now)

    def test_valid_additive_install_and_request_only_child(self):
        result = self.validate()
        self.assertEqual(result["only_missing_required_partitions"], 19)
        self.assertFalse(result["production_writer_invoked"])

    def test_placeholder_not_activated(self):
        self.request["_activation"]["state"] = "NOT_ACTIVATED"
        self.save_request()
        with self.assertRaises(ValueError):
            self.validate()

    def test_no_rerun_dispatch_other_branch_or_repository(self):
        for key, value in (("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_EVENT_NAME", "workflow_dispatch"),
                           ("GITHUB_REF", "refs/heads/other"), ("GITHUB_REPOSITORY", "other/repo")):
            with self.subTest(key=key), patch.dict(self.env, {key: value}), self.assertRaises(ValueError):
                self.validate()

    def test_time_window_both_edges(self):
        for hour in (9, 14):
            self.now = datetime(2026, 9, 6, hour, tzinfo=timezone.utc)
            with self.assertRaises(ValueError):
                self.validate()

    def test_forced_or_deleted_event(self):
        for key in ("forced", "deleted"):
            with patch.dict(self.event, {key: True}), self.assertRaises(ValueError):
                self.validate()

    def test_wrong_event_before_or_after(self):
        for key in ("before", "after"):
            with patch.dict(self.event, {key: "c" * 40}), self.assertRaises(ValueError):
                self.validate()

    def test_old_failure_identity_preserved(self):
        for key, value in (("previous_run_id", "1"), ("previous_manifest_sha256", "0" * 64), ("old_failure_status_preserved", False)):
            original = self.request["_activation"][key]
            self.request["_activation"][key] = value
            self.save_request()
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate()
            self.request["_activation"][key] = original

    def test_installation_cannot_modify_original_or_add_extra_path(self):
        key = ("diff", "--name-status", guard.INSTALL_BASE, self.install)
        self.responses[key] += "\nM\twork/executable-profit-history-source-20260906/PLAN.json"
        with self.assertRaises(ValueError):
            self.validate()

    def test_activation_cannot_modify_code(self):
        key = ("diff", "--name-status", self.install, self.head)
        self.responses[key] += "\nM\t" + guard.PREFIX + "collect_tail.py"
        with self.assertRaises(ValueError):
            self.validate()

    def test_hash_pin_mismatch(self):
        (self.root / guard.PREFIX / "README.md").write_text("changed")
        with self.assertRaises(ValueError):
            self.validate()

    def test_dirty_checkout(self):
        self.responses[("status", "--porcelain=v1")] = "M old-production.json"
        with self.assertRaises(ValueError):
            self.validate()

    def test_merge_or_wrong_install_base(self):
        key = ("rev-list", "--parents", "-n", "1", self.install)
        self.responses[key] += " " + "c" * 40
        with self.assertRaises(ValueError):
            self.validate()

    def test_installation_skip_ci_required(self):
        self.responses[("show", "-s", "--format=%B", self.install)] = "Install"
        with self.assertRaises(ValueError):
            self.validate()


if __name__ == "__main__":
    unittest.main()
