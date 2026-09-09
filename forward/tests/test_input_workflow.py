"""Static stdlib-only checks for the natural reader's isolated first job."""
from pathlib import Path
import re
import unittest

from forward.trigger import SCHEDULES


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/accept_forward_inputs.yml"
ACCEPTANCE = ROOT / ".github/workflows/test_forward_rebuild.yml"


def top_block(text, key):
    match = re.search(rf"^{re.escape(key)}:\n(.*?)(?=^[A-Za-z][^\n]*:|\Z)", text, re.M | re.S)
    if match is None:
        raise AssertionError(f"missing {key} block")
    return match.group(1)


def job_block(text, key):
    jobs = top_block(text, "jobs")
    match = re.search(rf"^  {re.escape(key)}:\n(.*?)(?=^  [a-z][a-z-]*:\n|\Z)", jobs, re.M | re.S)
    if match is None:
        raise AssertionError(f"missing job {key}")
    return match.group(1)


class InputWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.reader = job_block(self.text, "input-acceptance")

    def test_only_ten_independent_natural_schedule_slots(self):
        events = top_block(self.text, "on")
        self.assertEqual(re.findall(r"^  ([a-z_]+):", events, re.M), ["schedule"])
        crons = re.findall(r"^    - cron: '([^']+)'$", events, re.M)
        self.assertEqual(len(crons), 10)
        self.assertEqual(set(crons), set(SCHEDULES))
        self.assertNotRegex(self.text, r"(?m)^\s*(workflow_dispatch|repository_dispatch|workflow_call|workflow_run):")

    def test_permissions_are_exactly_read_only(self):
        permissions = top_block(self.text, "permissions")
        values = dict(re.findall(r"^  ([a-z-]+): ([a-z]+)$", permissions, re.M))
        self.assertEqual(values, {"contents": "read", "actions": "read"})
        self.assertEqual(len(re.findall(r"(?m)^\s*permissions:", self.text)), 1)
        self.assertNotRegex(self.text, r"(?m)^\s*[a-z-]+:\s*(write|write-all)\s*$")

    def test_reader_has_own_non_cancelling_concurrency(self):
        concurrency = top_block(self.text, "concurrency")
        self.assertIn("group: dc20-forward-inputs-readonly-${{ github.run_id }}", concurrency)
        self.assertNotIn("github.ref", concurrency)
        self.assertIn("cancel-in-progress: false", concurrency)
        self.assertNotIn("decision-auction-main-writer", self.text)
        self.assertNotRegex(self.reader, r"(?m)^\s*needs:")

    def test_pinned_actions_and_checkout_event_sha_without_credentials(self):
        uses = re.findall(r"uses: ([^\s]+)", self.reader)
        self.assertEqual(uses, [
            "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09",
            "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1",
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
        ])
        self.assertIn("ref: ${{ github.sha }}", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn("python-version: '3.12.13'", self.text)

    def test_input_runtime_is_one_stdlib_reader_not_model_or_production_job(self):
        commands = re.findall(r"^        run: \|\n((?:          [^\n]*\n)+)", self.reader, re.M)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].strip(),
                         'python -m forward.input_acceptance --root . --output "$RUNNER_TEMP/forward-input-acceptance"')
        self.assertNotRegex(self.reader.lower(), r"\b(pip|conda|uv|npm|apt-get)\s+(install|sync|ci)\b")
        self.assertNotIn("requirements-dev.lock", self.reader)
        self.assertNotRegex(self.reader, r"forward\.(rehearsal|bundle_rehearsal|promotion|profit|daybook|ledger|cli)\b")
        self.assertNotIn("git push", self.text)
        self.assertNotIn("gh workflow", self.text)
        self.assertNotIn("deploy-pages", self.text)
        self.assertNotIn("top10-decision", self.text)

    def test_only_reader_step_receives_token_and_schedule(self):
        reader = self.text.split("      - name: Verify original natural slot", 1)[1].split("      - name:", 1)[0]
        self.assertIn("          GITHUB_TOKEN: ${{ github.token }}", reader)
        self.assertIn("          FORWARD_SCHEDULE: ${{ github.event.schedule }}", reader)
        self.assertEqual(self.text.count("GITHUB_TOKEN:"), 1)
        self.assertEqual(self.text.count("FORWARD_SCHEDULE:"), 1)
        self.assertNotIn("secrets.", self.text)
        before_steps = self.reader.split("    steps:", 1)[0]
        self.assertNotIn("github.token", before_steps)
        self.assertIn("PYTHONDONTWRITEBYTECODE: '1'", top_block(self.text, "env"))

    def test_reader_exports_only_hash_and_status_pins(self):
        self.assertIn("        id: collect", self.reader)
        self.assertIn("      status: ${{ steps.collect.outputs.status }}", self.reader)
        self.assertIn("      bundle_sha256: ${{ steps.collect.outputs.bundle_sha256 }}", self.reader)
        self.assertIn("      receipt_sha256: ${{ steps.collect.outputs.receipt_sha256 }}", self.reader)
        output = self.reader.split("    outputs:\n", 1)[1].split("    steps:\n", 1)[0]
        self.assertEqual(re.findall(r"^      ([a-z_0-9]+):", output, re.M),
                         ["status", "bundle_sha256", "receipt_sha256"])

    def test_upload_only_successful_whole_external_directory(self):
        upload = self.reader.split("      - name: Preserve successful read-only acceptance evidence", 1)[1]
        self.assertIn("if: ${{ success() }}", upload)
        self.assertIn("name: forward-input-acceptance-${{ github.run_id }}", upload)
        self.assertIn("path: ${{ runner.temp }}/forward-input-acceptance/", upload)
        self.assertIn("if-no-files-found: error", upload)
        self.assertNotIn("always()", upload)
        self.assertNotIn("continue-on-error", self.text)
        self.assertNotIn("github.workspace", upload)
        self.assertNotIn("*.json", upload)

    def test_natural_input_job_is_first_and_has_own_time_limit(self):
        jobs = top_block(self.text, "jobs")
        self.assertEqual(re.findall(r"^  ([a-z-]+):$", jobs, re.M),
                         ["input-acceptance", "promotion-inference", "profit-inference"])
        self.assertIn("if: ${{ github.event_name == 'schedule' }}", self.reader)
        self.assertIn("timeout-minutes: 20", self.reader)
        self.assertIn("runs-on: ubuntu-24.04", self.reader)

    def test_both_push_and_pr_acceptance_watch_new_workflow(self):
        acceptance = ACCEPTANCE.read_text(encoding="utf-8")
        events = top_block(acceptance, "on")
        needle = "      - '.github/workflows/accept_forward_inputs.yml'"
        self.assertEqual(events.count(needle), 2)
        push, pull = events.split("  pull_request:")
        self.assertIn(needle, push)
        self.assertIn(needle, pull)


if __name__ == "__main__":
    unittest.main()
