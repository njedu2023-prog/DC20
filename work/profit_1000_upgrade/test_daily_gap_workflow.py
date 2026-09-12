"""The supplemental workflow cannot become a model/ledger publisher."""
from pathlib import Path
import re
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/research_daily_gap_sources.yml"


def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def test_only_registered_plan_push_starts_a_single_nonretry_readonly_collection():
    value = workflow()
    assert value.get("on", value.get(True)) == {"push": {"branches": ["main"],
        "paths": ["work/profit_1000_upgrade/DAILY_GAP_COLLECTION.json"]}}
    assert value["permissions"] == {"contents": "read"}
    assert value["concurrency"]["cancel-in-progress"] is False
    assert set(value["jobs"]) == {"daily-sources"}
    job = value["jobs"]["daily-sources"]
    assert "github.run_attempt == 1" in job["if"]
    assert job["timeout-minutes"] == 30


def test_full_tests_and_freeze_precede_data_and_actions_are_sha_pinned():
    steps = workflow()["jobs"]["daily-sources"]["steps"]
    code = [s.get("run", "") for s in steps]
    collection = next(i for i, run in enumerate(code) if "daily_gap_collect.py" in run)
    assert next(i for i, run in enumerate(code) if "-m pytest -q work/profit_1000_upgrade" in run) < collection
    assert next(i for i, run in enumerate(code) if "validate_decision_model_freeze.py" in run) < collection
    assert steps[0]["with"] == {"ref": "${{ github.sha }}", "persist-credentials": False}
    assert all(re.fullmatch(r"[\w/-]+@[0-9a-f]{40}", s["uses"]) for s in steps if "uses" in s)
    assert sum("TUSHARE_TOKEN" in s.get("env", {}) for s in steps) == 1


def test_process_budget_external_receipt_identity_and_unconditional_archive():
    steps = workflow()["jobs"]["daily-sources"]["steps"]
    code = [s.get("run", "") for s in steps]
    collect = next(run for run in code if "daily_gap_collect.py" in run)
    assert "timeout --signal=TERM --kill-after=30s 12m" in collect
    assert 'exit "${collection_exit}"' in collect
    assert '"${collection_exit}" -ne 2' in collect
    assert "daily_gap_receipt.json" in collect
    verify = next(run for run in code if "daily_gap_verify.py" in run)
    for argument in ("--expected-plan-sha256", "--expected-label-report-sha256",
                     "--expected-run-id", "--expected-run-commit"):
        assert argument in verify
    assert '"${GITHUB_RUN_ID}"' in verify and '"${GITHUB_SHA}"' in verify
    uploads = [s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@")]
    assert len(uploads) == 1 and uploads[0]["if"] == "always()"
    assert uploads[0]["with"]["path"] == "${{ runner.temp }}/daily-gap-sources/"
    assert uploads[0]["with"]["if-no-files-found"] == "error"
    assert any(s.get("if") == "always()" and "git status --porcelain=v1" in s.get("run", "") for s in steps)
    for forbidden in ("git push", "create_commit", "candidate_v3", "--evaluate", "run_primary_d_daily"):
        assert forbidden not in "\n".join(code)


def test_every_run_block_has_valid_shell_syntax():
    for step in workflow()["jobs"]["daily-sources"]["steps"]:
        if "run" in step:
            subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True, timeout=5)
