import ast
import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Anchors(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.append(dict(attrs))


class HomeResearchLinkTests(unittest.TestCase):
    def test_new_window_and_safe_destination(self):
        html = (ROOT / "decision.html").read_text()
        links = [a for a in Anchors(html).links if a.get("href") == "/DC20/outputs/decision/profit_research/"]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["target"], "_blank")
        self.assertEqual(set(links[0]["rel"].split()), {"noopener", "noreferrer"})
        nav = html.split('<nav class="workspace-nav"', 1)[1].split("</nav>", 1)[0]
        self.assertIn("盈利模型研究台 ↗", nav)

    def test_source_pin_and_inventory(self):
        raw = (ROOT / "models/decision_model_freeze.json").read_bytes()
        manifest = json.loads(raw)
        self.assertEqual(manifest["pinned_files"]["decision.html"], hashlib.sha256((ROOT / "decision.html").read_bytes()).hexdigest())
        item = next(x for x in json.loads((ROOT / "forward/model_inventory.json").read_bytes())["assets"] if x["path"] == "models/decision_model_freeze.json")
        self.assertEqual(item["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(item["bytes"], len(raw))

    def test_exact_inverse_and_drift_rejection(self):
        source = (ROOT / "tests/test_decision_source_surface_rotation.py").read_text()
        module = ast.parse(source)
        names = {"_candidate_activation_inverse", "_home_research_link_previous"}
        selected = ast.Module(body=[n for n in module.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[])
        ns = dict(ROOT=ROOT, hashlib=hashlib, json=json)
        exec(compile(selected, "<source-review>", "exec"), ns)
        review = json.loads((ROOT / "models/decision_source_surface_review_20260930_research_link.json").read_bytes())
        for item in review["source_changes"]:
            raw = (ROOT / item["path"]).read_bytes()
            restored = ns["_home_research_link_previous"](item["path"], raw)
            self.assertEqual(hashlib.sha256(restored).hexdigest(), item["baseline_sha256"])
            with self.assertRaises(AssertionError):
                ns["_home_research_link_previous"](item["path"], raw + b"\n")
            if item["path"] == "decision.html":
                line = '      <a href="/DC20/outputs/decision/profit_research/" target="_blank" rel="noopener noreferrer" title="在新窗口打开盈利模型研究台">盈利模型研究台 ↗</a>\n'
                self.assertEqual(raw.decode().replace(line, ""), restored.decode())


if __name__ == "__main__":
    unittest.main()
