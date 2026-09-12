"""The v2 replay cannot acquire production write credentials or replace v1."""
from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[2]


def workflow():
    return (ROOT / ".github/workflows/research_profit_1000_sources_v2.yml").read_text()


def test_single_versioned_trigger_and_read_only_execution():
    text = workflow()
    assert '"work/profit_1000_upgrade/COLLECTION_V2.json"' in text
    assert '"work/profit_1000_upgrade/COLLECTION.json"' not in text
    assert "workflow_dispatch" not in text and "schedule:" not in text
    assert "contents: read" in text and "actions: read" in text
    assert "contents: write" not in text and "actions: write" not in text
    assert "persist-credentials: false" in text and "github.run_attempt == 1" in text
    assert "timeout-minutes: 115" in text and "cancel-in-progress: false" in text
    assert "git push" not in text and "GH_WRITE" not in text


def test_immutable_versions_and_bounded_collection():
    text = workflow()
    actions = re.findall(r"uses: ([^\n]+)", text)
    assert len(actions) == 3 and all(re.search(r"@[0-9a-f]{40}$", value) for value in actions)
    assert "3.12.13" in text and "--require-hashes -r requirements-dev.lock" in text
    assert "actions/artifacts/10291376913/zip" in text
    assert text.count("--plan-version v2") == 3
    assert '--resume-zip "${RUNNER_TEMP}/history-v1.zip"' in text
    assert text.index("python -m pytest") < text.index("secrets.TUSHARE_TOKEN")
    assert text.index("validate_decision_model_freeze") < text.index("secrets.TUSHARE_TOKEN")
    assert text.count("secrets.TUSHARE_TOKEN") == 1
    request = json.loads((ROOT / "work/profit_1000_upgrade/COLLECTION_V2.json").read_text())
    prior = json.loads((ROOT / "work/profit_1000_upgrade/COLLECTION.json").read_text())
    assert request["budget"] == prior["budget"]


def test_capital_replay_is_independent_and_failed_evidence_is_saved():
    text = workflow()
    assert "capital_report.py" in text
    assert '--expected-source-run-id "${{ github.run_id }}"' in text
    assert '--expected-source-commit "${{ github.sha }}"' in text
    assert text.count("if: ${{ !cancelled() }}") == 2
    assert "include-hidden-files: true" in text and "retention-days: 30" in text
    assert "Preserve full v2 evidence including blocked and negative results" in text
    assert 'name: dc20-profit-1000-sources-v2-${{ github.sha }}-${{ github.run_id }}' in text
