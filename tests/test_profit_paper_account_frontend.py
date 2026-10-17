"""Run cash-account regressions against the exact inline code shipped to Pages."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="Node required for the actual browser script")
def test_paper_account_engine_and_isolated_frontend():
    result = subprocess.run(
        [NODE, "--test", "tests/test_profit_paper_account_engine.cjs",
         "tests/test_profit_paper_account_ui.cjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_paper_account_keeps_current_source_pins_consistent():
    manifest_path = ROOT / "models/decision_model_freeze.json"
    manifest = json.loads(manifest_path.read_bytes())
    assert manifest["pinned_files"]["decision.html"] == hashlib.sha256(
        (ROOT / "decision.html").read_bytes()).hexdigest()
    inventory = json.loads((ROOT / "forward/model_inventory.json").read_bytes())
    entry = next(a for a in inventory["assets"] if a["path"] == "models/decision_model_freeze.json")
    assert entry["sha256"] == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    assert entry["bytes"] == len(manifest_path.read_bytes())
