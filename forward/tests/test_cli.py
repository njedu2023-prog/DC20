import json
import tempfile
import unittest
from pathlib import Path

from forward.cli import export_site

ROOT = Path(__file__).resolve().parents[2]


class PreviewTests(unittest.TestCase):
    def test_export_keeps_replay_out_of_empty_ledger_and_escapes_script(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory).resolve()
            dashboard = {"schema_version": "dc20_forward_dashboard_v1", "production_enabled": False,
                         "source_revision": "a" * 40, "latest": None, "untrusted": "</script><script>alert(1)</script>"}
            ledger = {"days": []}
            export_site(output, dashboard, ledger, ROOT / "forward/web")
            self.assertEqual(json.loads((output / "ledger.json").read_text()), ledger)
            html = (output / "index.html").read_text()
            self.assertNotIn("</script><script>alert(1)", html)
            self.assertIn("\\u003c/script>", html)
            revision = json.loads((output / "revision.json").read_text())
            self.assertFalse(revision["production_enabled"])
            self.assertEqual(len(revision["files"]), 7)
            self.assertNotIn("旧二筛", html)
            for filename in ("promotion.csv", "profit.csv"):
                self.assertNotIn(b"\r", (output / filename).read_bytes())

    def test_no_production_enable_flag_or_writer_in_cli(self):
        from forward.cli import main
        with self.assertRaises(SystemExit):
            main(["activate"])

    def test_ci_is_read_only_and_pip_colons_are_block_quoted(self):
        workflow = (ROOT / ".github/workflows/test_forward_rebuild.yml").read_text()
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertIn("run: |\n          python -m pip install", workflow)
        self.assertNotIn("run: python -m pip install", workflow)


if __name__ == "__main__":
    unittest.main()
