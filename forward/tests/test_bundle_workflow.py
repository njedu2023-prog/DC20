"""Static CI/natural input -> P0 -> P1 boundaries; no workflow execution."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
NATURAL = ROOT / ".github/workflows/accept_forward_inputs.yml"
HISTORICAL = ROOT / ".github/workflows/test_forward_rebuild.yml"
PRED_SHA = "1615abe7600d518dd04fcbbf5b159aad8ce95bf7"
MARKET_SHA = "ff7a16b1d37b7f6befca5e4180ade7051fcd1df1"
ACTION_PINS = {
    "actions/checkout": "fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09",
    "actions/setup-python": "ece7cb06caefa5fff74198d8649806c4678c61a1",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "actions/download-artifact": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
}


def job(text, name):
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [a-z][a-z-]*:\n|\Z)",
                      text.split("\njobs:\n", 1)[1], re.M | re.S)
    if match is None:
        raise AssertionError(f"missing job {name}")
    return match.group(1)


class BundleWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.natural = NATURAL.read_text(encoding="utf-8")
        self.historical = HISTORICAL.read_text(encoding="utf-8")

    def test_natural_closed_reader_cannot_launch_ml_jobs(self):
        reader = job(self.natural, "input-acceptance")
        p0 = job(self.natural, "promotion-inference")
        p1 = job(self.natural, "profit-inference")
        self.assertNotIn("needs:", reader)
        self.assertNotIn("pip install", reader)
        self.assertIn("needs: [input-acceptance]", p0)
        self.assertIn("if: ${{ needs.input-acceptance.outputs.status == 'INPUTS_VALIDATED_NOT_PRODUCTION' }}", p0)
        self.assertIn("needs: [promotion-inference]", p1)
        self.assertNotIn("always()", self.natural)
        self.assertNotIn("continue-on-error", self.natural)
        self.assertNotIn("failure()", self.natural)

    def test_prior_runs_slow_p1_cannot_serialize_the_next_natural_p0(self):
        concurrency = self.natural.split("\nconcurrency:\n", 1)[1].split("\njobs:\n", 1)[0]
        self.assertIn("group: dc20-forward-inputs-readonly-${{ github.run_id }}", concurrency)
        self.assertIn("cancel-in-progress: false", concurrency)
        self.assertNotIn("github.ref", concurrency)
        self.assertEqual(len(re.findall(r"(?m)^\s*concurrency:", self.natural)), 1)

    def test_natural_p0_downloads_exact_same_run_and_uses_two_external_pins(self):
        p0 = job(self.natural, "promotion-inference")
        self.assertIn("name: forward-input-acceptance-${{ github.run_id }}", p0)
        self.assertIn("path: ${{ runner.temp }}/forward-input-acceptance", p0)
        self.assertIn("INPUT_MANIFEST_SHA256: ${{ needs.input-acceptance.outputs.bundle_sha256 }}", p0)
        self.assertIn("ACCEPTANCE_RECEIPT_SHA256: ${{ needs.input-acceptance.outputs.receipt_sha256 }}", p0)
        self.assertIn('forward.bundle_rehearsal --root . promotion --bundle "$RUNNER_TEMP/forward-input-acceptance/bundle"', p0)
        self.assertIn('--manifest-sha256 "$INPUT_MANIFEST_SHA256"', p0)
        self.assertIn('--acceptance "$RUNNER_TEMP/forward-input-acceptance"', p0)
        self.assertIn('--acceptance-receipt-sha256 "$ACCEPTANCE_RECEIPT_SHA256"', p0)
        self.assertNotRegex(p0, r"--(signal-date|pred-commit|market-commit)\b")
        self.assertNotIn(" collect ", p0)
        self.assertNotIn("run-id:", p0)
        self.assertNotIn("github-token:", p0)
        self.assertNotIn("repository:", p0)

    def test_natural_p0_is_retained_before_independent_p1(self):
        p0 = job(self.natural, "promotion-inference")
        p1 = job(self.natural, "profit-inference")
        self.assertIn("name: Preserve promotion before any profit work", p0)
        self.assertIn("name: forward-promotion-rehearsal-${{ github.run_id }}", p0)
        self.assertIn("path: ${{ runner.temp }}/forward-primary/*.json", p0)
        self.assertNotIn("forward.bundle_rehearsal --root . profit", p0)
        self.assertIn("name: forward-promotion-rehearsal-${{ github.run_id }}", p1)
        self.assertIn('forward.bundle_rehearsal --root . profit --primary "$RUNNER_TEMP/forward-primary"', p1)
        self.assertIn("name: forward-profit-rehearsal-${{ github.run_id }}", p1)
        self.assertNotIn("input-acceptance", p1)
        self.assertNotIn("--bundle", p1)
        self.assertNotIn(" compare ", p1)

    def test_natural_inference_uses_same_checkout_and_locked_dependencies(self):
        for name in ("promotion-inference", "profit-inference"):
            with self.subTest(job=name):
                section = job(self.natural, name)
                self.assertIn("ref: ${{ github.sha }}", section)
                self.assertIn("persist-credentials: false", section)
                self.assertIn("python-version: '3.12.13'", section)
                self.assertIn("cache-dependency-path: requirements-dev.lock", section)
                self.assertIn("python -m pip install --disable-pip-version-check --only-binary=:all: --require-hashes -r requirements-dev.lock", section)
                self.assertIn('test -z "$(git status --porcelain=v1 --untracked-files=no)"', section)
                self.assertIn("timeout-minutes: 20", section)
                self.assertNotIn("GITHUB_TOKEN", section)
                self.assertNotIn("secrets.", section)

    def test_p0_receipt_hash_is_pinned_outside_the_downloaded_artifact_in_both_workflows(self):
        for text in (self.natural, self.historical):
            p0 = job(text, "promotion-inference")
            p1 = job(text, "profit-inference")
            self.assertIn("    outputs:\n      receipt_sha256: ${{ steps.infer.outputs.receipt_sha256 }}", p0)
            self.assertIn("        id: infer", p0)
            self.assertEqual(p0.count("        id: infer"), 1)
            inference = p0.split("        id: infer", 1)[1].split("      - ", 1)[0]
            self.assertIn("forward.bundle_rehearsal --root . promotion", inference)
            self.assertIn("PRIMARY_RECEIPT_SHA256: ${{ needs.promotion-inference.outputs.receipt_sha256 }}", p1)
            self.assertIn('--primary-receipt-sha256 "$PRIMARY_RECEIPT_SHA256"', p1)
            self.assertNotRegex(p1, r"PRIMARY_RECEIPT_SHA256=|sha256sum .*receipt\.json|shasum .*receipt\.json")

    def test_historical_p0_collects_frozen_upstreams_not_old_repo_inputs(self):
        p0 = job(self.historical, "promotion-inference")
        # Aggregate regressions, truth and P1 remain unable to suppress P0.
        self.assertNotRegex(p0, r"(?m)^\s*needs:")
        self.assertIn("        id: collect", p0)
        self.assertIn(f"forward.bundle_rehearsal --root . collect --signal-date 20260908 --pred-commit {PRED_SHA} --market-commit {MARKET_SHA}", p0)
        self.assertIn('--output "$RUNNER_TEMP/forward-input-bundle"', p0)
        self.assertIn("INPUT_MANIFEST_SHA256: ${{ steps.collect.outputs.manifest_sha256 }}", p0)
        self.assertIn('forward.bundle_rehearsal --root . promotion --bundle "$RUNNER_TEMP/forward-input-bundle" --manifest-sha256 "$INPUT_MANIFEST_SHA256"', p0)
        self.assertLess(p0.index("forward.bundle_rehearsal --root . collect"),
                        p0.index("forward.bundle_rehearsal --root . promotion"))
        self.assertNotIn("--acceptance", p0)
        self.assertNotIn("forward.rehearsal --root . promotion", p0)
        self.assertNotIn("data/market/raw", p0)
        self.assertNotIn("data/pred", p0)

    def test_historical_p1_uploads_before_read_only_legacy_comparison(self):
        p1 = job(self.historical, "profit-inference")
        self.assertIn("needs: [promotion-inference]", p1)
        self.assertIn('forward.bundle_rehearsal --root . profit --primary "$RUNNER_TEMP/forward-primary"', p1)
        self.assertNotIn("forward.rehearsal --root . profit", p1)
        self.assertIn('forward.rehearsal --root . compare --primary "$RUNNER_TEMP/forward-primary" --profit "$RUNNER_TEMP/forward-profit"', p1)
        self.assertLess(p1.index("actions/upload-artifact@"), p1.index("forward.rehearsal --root . compare"))
        migration = job(self.historical, "migration-acceptance")
        self.assertNotRegex(migration, r"(?m)^\s*needs:")
        self.assertIn("python -m pytest forward/tests -q", migration)
        self.assertIn("forward.cli --root . preview", migration)
        self.assertIn('assert not v["days"] and v["activated_at_utc"] is None', migration)

    def test_all_actions_are_exact_reviewed_pins(self):
        for text in (self.natural, self.historical):
            uses = re.findall(r"uses: ([^\s]+)", text)
            self.assertTrue(uses)
            for use in uses:
                name, sha = use.split("@", 1)
                self.assertIn(name, ACTION_PINS)
                self.assertEqual(sha, ACTION_PINS[name])

    def test_no_production_publication_trading_or_manual_dispatch(self):
        for text in (self.natural, self.historical):
            self.assertNotRegex(text, r"(?m)^\s*(workflow_dispatch|repository_dispatch|workflow_call|workflow_run):")
            self.assertNotRegex(text, r"(?m)^\s*[a-z-]+:\s*(write|write-all)\s*$")
            self.assertNotIn("git push", text)
            self.assertNotIn("gh workflow", text)
            self.assertNotIn("deploy-pages", text)
            self.assertNotIn("decision-auction-main-writer", text)
            self.assertNotIn("top10-decision", text)
            self.assertNotRegex(text, r"forward\.(daybook|ledger)\b|--activate\b")
            self.assertNotIn("continue-on-error", text)
            self.assertNotIn("--no-verify", text)


if __name__ == "__main__":
    unittest.main()
