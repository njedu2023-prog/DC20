"""Release preparation is a downstream, non-producing artifact-only branch."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = (
    ("accept_forward_inputs.yml", "-${{ github.run_id }}"),
    ("test_forward_rebuild.yml", ""),
)
CHECKOUT = "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09"
PYTHON = "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1"
DOWNLOAD = "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093"
UPLOAD = "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"


def job(text, name):
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [a-z][a-z-]*:\n|\Z)",
                      text.split("\njobs:\n", 1)[1], re.M | re.S)
    if match is None:
        raise AssertionError(f"missing job {name}")
    return match.group(1)


class ReleaseWorkflowTests(unittest.TestCase):
    def texts(self):
        for filename, suffix in WORKFLOWS:
            yield filename, (ROOT / ".github/workflows" / filename).read_text(encoding="utf-8"), suffix

    def test_candidates_do_not_enter_the_inference_critical_path(self):
        for filename, text, _ in self.texts():
            with self.subTest(workflow=filename):
                p0 = job(text, "promotion-inference")
                p1 = job(text, "profit-inference")
                p0_candidate = job(text, "promotion-release-candidate")
                p1_candidate = job(text, "profit-release-candidate")
                self.assertNotIn("release-candidate", p0)
                self.assertNotIn("release-candidate", p1)
                self.assertIn("needs: [promotion-inference]", p0_candidate)
                self.assertIn("needs: [promotion-inference, profit-inference]", p1_candidate)
                self.assertNotIn("profit-inference", p0_candidate)
                self.assertNotIn("migration-acceptance", p0_candidate)
                self.assertNotRegex(p0_candidate, r"(?m)^\s*if:")
                self.assertNotRegex(p1_candidate, r"(?m)^\s*if:")
                if filename == "accept_forward_inputs.yml":
                    self.assertIn("if: ${{ needs.input-acceptance.outputs.status == 'INPUTS_VALIDATED_NOT_PRODUCTION' }}", p0)
                    self.assertIn("needs: [input-acceptance]", p0)
                else:
                    self.assertNotRegex(p0, r"(?m)^\s*needs:")

    def test_both_model_jobs_export_externally_pinned_receipt_sha(self):
        for filename, text, _ in self.texts():
            for name, command in (("promotion-inference", "promotion"), ("profit-inference", "profit")):
                with self.subTest(workflow=filename, job=name):
                    section = job(text, name)
                    self.assertIn("    outputs:\n      receipt_sha256: ${{ steps.infer.outputs.receipt_sha256 }}", section)
                    self.assertEqual(section.count("        id: infer"), 1)
                    infer = section.split("        id: infer", 1)[1].split("      - ", 1)[0]
                    self.assertIn(f"forward.bundle_rehearsal --root . {command}", infer)

    def test_promotion_candidate_uses_only_p0_from_this_run_and_external_pin(self):
        for filename, text, suffix in self.texts():
            with self.subTest(workflow=filename):
                section = job(text, "promotion-release-candidate")
                self.assertEqual(re.findall(r"uses: ([^\s]+)", section), [CHECKOUT, PYTHON, DOWNLOAD, UPLOAD])
                self.assertIn(f"name: forward-promotion-rehearsal{suffix}", section)
                self.assertIn("path: ${{ runner.temp }}/forward-primary", section)
                self.assertIn("PRIMARY_RECEIPT_SHA256: ${{ needs.promotion-inference.outputs.receipt_sha256 }}", section)
                self.assertIn('python -m forward.release_candidate --root . promotion --primary "$RUNNER_TEMP/forward-primary" --primary-receipt-sha256 "$PRIMARY_RECEIPT_SHA256" --output "$RUNNER_TEMP/forward-promotion-release-candidate"', section)
                self.assertNotIn("PROFIT_RECEIPT_SHA256", section)
                self.assertNotIn("forward-profit-rehearsal", section)
                self.assertIn(f"name: forward-promotion-release-candidate{suffix}", section)
                self.assertIn("path: ${{ runner.temp }}/forward-promotion-release-candidate/", section)

    def test_profit_candidate_pins_both_complete_model_artifacts(self):
        for filename, text, suffix in self.texts():
            with self.subTest(workflow=filename):
                section = job(text, "profit-release-candidate")
                self.assertEqual(re.findall(r"uses: ([^\s]+)", section), [CHECKOUT, PYTHON, DOWNLOAD, DOWNLOAD, UPLOAD])
                for stem in ("promotion", "profit"):
                    self.assertIn(f"name: forward-{stem}-rehearsal{suffix}", section)
                self.assertIn("PRIMARY_RECEIPT_SHA256: ${{ needs.promotion-inference.outputs.receipt_sha256 }}", section)
                self.assertIn("PROFIT_RECEIPT_SHA256: ${{ needs.profit-inference.outputs.receipt_sha256 }}", section)
                self.assertIn('python -m forward.release_candidate --root . profit --primary "$RUNNER_TEMP/forward-primary" --primary-receipt-sha256 "$PRIMARY_RECEIPT_SHA256" --profit "$RUNNER_TEMP/forward-profit" --profit-receipt-sha256 "$PROFIT_RECEIPT_SHA256" --output "$RUNNER_TEMP/forward-profit-release-candidate"', section)
                self.assertIn(f"name: forward-profit-release-candidate{suffix}", section)
                self.assertIn("path: ${{ runner.temp }}/forward-profit-release-candidate/", section)
                self.assertNotIn("promotion-release-candidate", section)

    def test_candidate_jobs_are_stdlib_only_same_revision_and_external_outputs(self):
        for filename, text, _ in self.texts():
            for name in ("promotion-release-candidate", "profit-release-candidate"):
                with self.subTest(workflow=filename, job=name):
                    section = job(text, name)
                    self.assertIn("ref: ${{ github.sha }}", section)
                    self.assertIn("persist-credentials: false", section)
                    self.assertIn("python-version: '3.12.13'", section)
                    self.assertIn("runs-on: ubuntu-24.04", section)
                    self.assertIn("timeout-minutes: 10", section)
                    self.assertIn("if-no-files-found: error", section)
                    self.assertIn("retention-days: 14", section)
                    self.assertIn('test -z "$(git status --porcelain=v1 --untracked-files=no)"', section)
                    self.assertNotRegex(section.lower(), r"\b(pip|uv|conda|npm|apt-get)\s+(install|sync|ci)\b")
                    self.assertNotIn("requirements", section)
                    self.assertNotIn("cache:", section)
                    self.assertNotIn("PYTHONPATH", section)
                    self.assertNotIn("GITHUB_TOKEN", section)
                    self.assertNotIn("secrets.", section)
                    self.assertNotIn("run-id:", section)
                    self.assertNotIn("github-token:", section)
                    self.assertNotIn("repository:", section)
                    self.assertNotRegex(section, r"--now\b|--signal-date\b|--activate\b|--generation-mode\b")
                    self.assertNotRegex(section, r"RECEIPT_SHA256=|sha256sum|shasum")

    def test_no_candidate_can_write_pages_epoch_ledger_or_main(self):
        for filename, text, _ in self.texts():
            with self.subTest(workflow=filename):
                self.assertNotRegex(text, r"(?m)^\s*[a-z-]+:\s*(write|write-all)\s*$")
                self.assertNotRegex(text, r"(?m)^\s*(workflow_dispatch|repository_dispatch|workflow_call|workflow_run):")
                self.assertNotIn("continue-on-error", text)
                for name in ("promotion-release-candidate", "profit-release-candidate"):
                    section = job(text, name)
                    self.assertNotIn("always()", section)
                    self.assertNotIn("permissions:", section)
                    self.assertNotIn("git push", section)
                    self.assertNotIn("gh workflow", section)
                    self.assertNotIn("curl ", section)
                    self.assertNotIn("deploy-pages", section)
                    self.assertNotRegex(section, r"forward\.(daybook|ledger|cli)\b|epoch\.json|latest\.json")
                    self.assertNotIn("github-pages", section)


if __name__ == "__main__":
    unittest.main()
