"""Orchestration tests isolate trust/routing from separately tested model math."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from forward import bundle_rehearsal as cli, promotion, profit
from forward.storage import encoded
from forward.trigger import bind_schedule

REV = "a" * 40
DATES = ["20260904", "20260907", "20260908", "20260909", "20260910"]
NOW = "2026-09-07T13:20:00Z"


@pytest.fixture
def case(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "forward").mkdir(parents=True)
    (root / "forward/config.json").write_bytes(encoded({
        "production_enabled": False, "activated_at_utc": None, "start_signal_date": None,
        "legacy_statistics_import_allowed": False, "formal_trade_actions_allowed": False,
        "phase": "MIGRATION_ACCEPTANCE", "calendar_path": "calendar.csv", "calendar_sha256": "c" * 64}))
    acceptance = tmp_path / "inputs"
    bundle = acceptance / "bundle"
    bundle.mkdir(parents=True)
    manifest = {"signal_date": "20260907", "exec_date": "20260908", "exit_date": "20260909",
                "source_commit": REV, "pred_commit": "b" * 40, "market_commit": "c" * 40,
                "collected_at_utc": "2026-09-07T13:18:00Z"}
    raw = encoded(manifest)
    digest = hashlib.sha256(raw).hexdigest()
    (bundle / "manifest.json").write_bytes(raw)
    event_path = tmp_path / "event.json"
    event_path.write_bytes(encoded({"schedule": "15 13 * * 1"}))
    env = {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "schedule",
           "GITHUB_REPOSITORY": cli.REPOSITORY, "GITHUB_REF": "refs/heads/main",
           "GITHUB_WORKFLOW_REF": f"{cli.REPOSITORY}/{cli.WORKFLOW}@refs/heads/main",
           "GITHUB_SHA": REV, "GITHUB_WORKFLOW_SHA": REV,
           "GITHUB_RUN_ID": "101", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_EVENT_PATH": str(event_path)}
    identity = {"source_revision": REV, "run_id": 101, "run_attempt": 1,
                "event_name": "schedule", "workflow_path": cli.WORKFLOW,
                "schedule": "15 13 * * 1", "run_created_at_utc": "2026-09-07T13:16:00Z"}
    receipt = {"schema_version": "dc20_forward_input_acceptance_v1", "status": "INPUTS_VALIDATED_NOT_PRODUCTION",
               "read_only": True, "production_activated": False, "inference_performed": False,
               "ledger_written": False, "published_list": False, "identity": identity,
               "gate": bind_schedule("schedule", identity["schedule"], identity["run_created_at_utc"], NOW, DATES),
               "bundle_sha256": digest, "started_at_utc": "2026-09-07T13:17:00Z", "finished_at_utc": NOW,
               "sources": {cli.UPSTREAMS[0]: manifest["pred_commit"], cli.UPSTREAMS[1]: manifest["market_commit"]}}
    receipt_raw = encoded(receipt)
    receipt_sha = hashlib.sha256(receipt_raw).hexdigest()
    (acceptance / "receipt.json").write_bytes(receipt_raw)
    monkeypatch.setattr(cli, "_head", lambda root: REV)
    monkeypatch.setattr(cli, "_state", lambda root: (REV, "", {}))
    monkeypatch.setattr(cli, "read_calendar", lambda *args: DATES)
    monkeypatch.setattr(cli, "_utc", lambda: NOW)
    def primary(*args, **kwargs):
        return {"day": {"signal_date": "20260907", "exec_date": "20260908", "exit_date": "20260909", "rows": [],
                        "source": {"input_manifest_sha256": digest,
                                   "candidate": {"resolved_commit": "b" * 40},
                                   "market": {"resolved_commit": "c" * 40}}}, "runtime_rows": []}
    monkeypatch.setattr(promotion, "compute_promotion_from_inputs", primary, raising=False)
    monkeypatch.setattr(profit, "infer_profit", lambda *a: {"rows": []})
    return dict(root=root, acceptance=acceptance, bundle=bundle, manifest=manifest,
                digest=digest, receipt=receipt, receipt_sha=receipt_sha, env=env,
                output=tmp_path / "p0", profit_output=tmp_path / "p1")


def compute(c, natural=False):
    return cli.compute_promotion(c["root"], c["bundle"], c["digest"], c["output"],
        acceptance=c["acceptance"] if natural else None,
        receipt_sha=c["receipt_sha"] if natural else None, env=c["env"] if natural else {})


def test_independent_p0_then_p1_receipts_remain_replay(case):
    receipt = compute(case)
    assert receipt["input_evidence"]["origin"] == "HISTORICAL_PINNED_INPUT_REPLAY"
    assert receipt["generation_mode"] == "REPLAY" and not receipt["forward_ledger_eligible"]
    before = (case["output"] / "receipt.json").read_bytes()
    p1 = cli.compute_profit(case["root"], case["output"], case["profit_output"], primary_receipt_sha=hashlib.sha256(before).hexdigest(), env={})
    assert p1["input_evidence"] == receipt["input_evidence"]
    assert (case["output"] / "receipt.json").read_bytes() == before
    assert p1["formal_trade_count"] == 0 and not p1["production_enabled"]


def test_natural_origin_is_same_run_staging_not_forward(case):
    p0 = compute(case, natural=True)
    assert p0["input_evidence"]["origin"] == "NATURAL_SCHEDULE_STAGING"
    assert p0["input_evidence"]["inference_gate"]["signal_date"] == "20260907"
    assert p0["generation_mode"] == "REPLAY"
    cli.compute_profit(case["root"], case["output"], case["profit_output"], primary_receipt_sha=hashlib.sha256(encoded(p0)).hexdigest(), env=case["env"])


@pytest.mark.parametrize("key,value", [
    ("GITHUB_SHA", "d" * 40), ("GITHUB_WORKFLOW_SHA", "d" * 40),
    ("GITHUB_RUN_ID", "102"), ("GITHUB_RUN_ATTEMPT", "2"),
    ("GITHUB_REPOSITORY", "wrong/repo"), ("GITHUB_EVENT_NAME", "workflow_dispatch"),
    ("GITHUB_ACTIONS", "false"), ("GITHUB_REF", "refs/heads/other"),
    ("GITHUB_WORKFLOW_REF", "other"),
])
def test_natural_identity_mismatch_no_receipt(case, key, value):
    case["env"][key] = value
    with pytest.raises(ValueError):
        compute(case, natural=True)
    assert not case["output"].exists()


def test_expiry_during_inference_blocks_receipt(case, monkeypatch):
    def model(*args, **kwargs):
        monkeypatch.setattr(cli, "_utc", lambda: "2026-09-08T01:20:00Z")
        return {"day": {"signal_date": "20260907", "rows": [], "source": {"input_manifest_sha256": case["digest"]}}}
    monkeypatch.setattr(promotion, "compute_promotion_from_inputs", model)
    with pytest.raises(ValueError):
        compute(case, natural=True)
    assert not (case["output"] / "receipt.json").exists()


def test_changed_checkout_or_wrong_model_input_binding_blocks(case, monkeypatch):
    monkeypatch.setattr(cli, "_unchanged", lambda *a: (_ for _ in ()).throw(ValueError("mutation")))
    with pytest.raises(ValueError):
        compute(case)
    assert not case["output"].exists()
    monkeypatch.setattr(cli, "_unchanged", lambda *a: None)
    monkeypatch.setattr(promotion, "compute_promotion_from_inputs", lambda *a, **k:
                        {"day": {"signal_date": "20260907", "source": {"input_manifest_sha256": "wrong"}}})
    with pytest.raises(ValueError):
        compute(case)
    assert not case["output"].exists()


def test_profit_failure_does_not_remove_p0(case, monkeypatch):
    compute(case)
    before = (case["output"] / "receipt.json").read_bytes()
    monkeypatch.setattr(profit, "infer_profit", lambda *a: (_ for _ in ()).throw(ValueError("bad P1")))
    with pytest.raises(ValueError):
        cli.compute_profit(case["root"], case["output"], case["profit_output"], primary_receipt_sha=hashlib.sha256(before).hexdigest(), env={})
    assert (case["output"] / "receipt.json").read_bytes() == before
    assert not case["profit_output"].exists()


def test_no_natural_downgrade_to_historical(case):
    with pytest.raises(ValueError):
        cli.compute_promotion(case["root"], case["bundle"], case["digest"], case["output"], env=case["env"])
    p0 = compute(case)
    with pytest.raises(ValueError):
        cli.compute_profit(case["root"], case["output"], case["profit_output"], primary_receipt_sha=hashlib.sha256(encoded(p0)).hexdigest(), env=case["env"])


def test_manifest_and_acceptance_pins_are_required(case):
    with pytest.raises(ValueError):
        cli.compute_promotion(case["root"], case["bundle"], "f" * 64, case["output"], env={})
    case["receipt_sha"] = "f" * 64
    with pytest.raises(ValueError):
        compute(case, natural=True)


def test_job_outputs_are_bounded_and_no_symlink(tmp_path):
    target = tmp_path / "out"
    target.touch()
    env = {"GITHUB_ACTIONS": "true", "GITHUB_OUTPUT": str(target)}
    cli.emit_job_outputs({"status": "CLOSED", "bundle_sha256": ""}, env)
    assert target.read_text() == "status=CLOSED\nbundle_sha256=\n"
    for values in ({"status": "ok\nevil=x"}, {"unknown": "x"}, {"status": "../path"}):
        with pytest.raises(ValueError):
            cli.emit_job_outputs(values, env)
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(OSError):
        cli.emit_job_outputs({"status": "CLOSED"}, {**env, "GITHUB_OUTPUT": str(link)})
