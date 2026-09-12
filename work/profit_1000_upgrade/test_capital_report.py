from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from work.profit_1000_upgrade import capital_report as report

D = "20260813"
T = "20260814"
SOURCE_RUN = "34671477608"
SOURCE_COMMIT = "4675fab984050fa32be875fff07e0285e8903ebb"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def case(tmp_path, monkeypatch):
    root = tmp_path / "mirror"
    root.mkdir()
    marker = root / ".dc20-profit-1000-research-root.json"
    write_json(marker, {"schema_version": "dc20_profit_1000_research_mirror_v1", "production_writes": False,
                        "plan_sha256": report.run.sha(report.HERE / "PLAN.json")})
    source = root / "evidence.json"
    source.write_text('{"source":"unit-test-only"}')
    binding = {"path": "evidence.json", "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    frozen = [{"signal_date": D, "ts_code": f"60000{rank}.SH", "promotion_rank": rank} for rank in (1, 2)]
    manifest = {"rows": [dict(row, exec_date=T) for row in frozen], "source_bindings": [binding]}
    labels = {"rows": [dict(row, label_status="SETTLED_1000_LIMIT_HOLD_MINUTE_PROXY") for row in frozen],
              "source_files": [binding], "candidate_manifest_sha256": "m" * 64}
    candidate = {
        "status": "DEVELOPMENT_ONLY_FITTED", "candidate_model": {"model": "test-only"},
        "report": {"training_performed": True, "production_activation_allowed": False},
        "predictions": [dict(row, candidate_rank=3-row["promotion_rank"], candidate_score=-0.05 * row["promotion_rank"])
                        for row in frozen],
    }
    receipt = {"schema_version": "dc20_profit_1000_collection_receipt_v1", "as_of_date": "20260911",
               "auction_attempts_complete": True, "production_writes": False,
               "existing_truth_overwritten": False, "credential_persisted": False,
               "candidate_source_bindings": [binding], "new_source_files": [binding],
               "request_receipts": [{"endpoint": "stk_auction_o", "trade_date": T,
                                      "network_request_performed": True, "status": "AUCTION_ABSENT_FALLBACK_DECLARED"}],
               "status": "RESEARCH_LABEL_COHORTS_COMPLETE"}
    write_json(root / "collection_receipt.json", receipt)
    calls = []
    monkeypatch.setattr(report.run, "prepare_history", lambda actual_root: (manifest, frozen, {"unit": "source-bound"}))
    def build(actual_root, actual_manifest, **kwargs):
        assert actual_root == root and actual_manifest is manifest
        calls.append(("labels", kwargs))
        return copy.deepcopy(labels)
    def train(actual_frozen, actual_labels, **kwargs):
        assert actual_frozen is frozen and actual_labels == labels["rows"]
        calls.append(("candidate", kwargs))
        return copy.deepcopy(candidate)
    def capital(actual_root, actual_rows, actual_labels, **kwargs):
        assert actual_root == root and actual_labels == labels["rows"]
        assert kwargs["candidate_manifest"] is manifest
        calls.append(("capital", {**kwargs, "ranked_rows": copy.deepcopy(actual_rows)}))
        return {"label_verification": "REBUILT_FROM_BOUND_REPOSITORY_PRICE_SOURCES",
                "production_activation_allowed": False, "source_files": [binding],
                "accounts": {"top1": {"equity": 999000}, "top2": {"equity": 998000}}}
    monkeypatch.setattr(report, "build_labels", build)
    monkeypatch.setattr(report, "run_candidate", train)
    monkeypatch.setattr(report, "replay_capital_from_repository", capital)
    return {"root": root, "manifest": manifest, "frozen": frozen, "labels": labels,
            "candidate": candidate, "receipt": receipt, "calls": calls, "binding": binding}


def output(case, **kwargs):
    return report.generate_capital_report(case["root"], **kwargs)


def update_receipt(case):
    write_json(case["root"] / "collection_receipt.json", case["receipt"])


def test_recomputes_labels_candidate_and_two_capital_comparisons(case):
    before = (case["root"] / "evidence.json").read_bytes()
    result = output(case)
    assert result["status"] == "CAPITAL_REPLAY_COMPLETE"
    assert [name for name, _ in case["calls"]] == ["labels", "candidate", "capital", "capital"]
    train = case["calls"][1][1]
    assert train["gates"] == report.DEFAULT_GATES
    assert train["training_cutoff_date"] == "20251111" and train["validation_end_date"] == "20260814"
    assert train["holdout_start_date"] == "20260914"
    capital_calls = [args for name, args in case["calls"] if name == "capital"]
    assert [args["rank_field"] for args in capital_calls] == ["candidate_rank", "promotion_rank"]
    assert all(args["entry_policy_id"] == "research_auction_or_open_no_cap_v1" for args in capital_calls)
    assert all(row["candidate_score"] < 0 for row in capital_calls[0]["ranked_rows"])
    assert result["compared_validation_dates"] == [D] and result["compared_candidate_rows"] == 2
    assert set(result["capital_comparisons"]) == {"candidate", "frozen_promotion"}
    assert all(set(value["accounts"]) == {"top1", "top2"} for value in result["capital_comparisons"].values())
    assert not result["production_activation_allowed"] and not result["data_collected"]
    assert not result["old_saved_labels_consumed"] and not result["forward_holdout_evaluated"]
    assert (case["root"] / "evidence.json").read_bytes() == before
    assert {p.name for p in (case["root"] / "research_capital").iterdir()} == set(report.OUTPUT_NAMES)
    assert json.loads(json.dumps(result, allow_nan=False))["status"] == result["status"]


def test_blocked_candidate_preserves_quality_report_without_nav(case):
    case["candidate"].update(status="BLOCKED_DATA_QUALITY", candidate_model=None, predictions=[],
                             report={"training_performed": False, "production_activation_allowed": False, "failed_quality_gates": ["min_train_dates"]})
    result = output(case)
    saved = json.loads((case["root"] / "research_capital/candidate_replay.json").read_text())
    assert result["status"] == "BLOCKED_CANDIDATE_DATA" and result["capital_comparisons"] is None
    assert not any(name == "capital" for name, _ in case["calls"])
    assert saved["status"] == "BLOCKED_DATA_QUALITY"
    assert saved["candidate_model"] is None and saved["predictions"] == []
    assert saved["report"]["failed_quality_gates"] == ["min_train_dates"]


@pytest.mark.parametrize("change", ["empty_predictions", "no_model", "not_fitted", "partial_cohort", "duplicate", "wrong_rank", "outside_date", "nan_score"])
def test_fake_or_partial_fitted_result_does_not_produce_nav(case, change):
    candidate = case["candidate"]
    if change == "empty_predictions": candidate["predictions"] = []
    if change == "no_model": candidate["candidate_model"] = None
    if change == "not_fitted": candidate["report"]["training_performed"] = False
    if change == "partial_cohort": candidate["predictions"].pop()
    if change == "duplicate": candidate["predictions"][1] = dict(candidate["predictions"][0])
    if change == "wrong_rank": candidate["predictions"][0]["promotion_rank"] = 99
    if change == "outside_date": candidate["predictions"][0]["signal_date"] = "20260914"
    if change == "nan_score": candidate["predictions"][0]["candidate_score"] = float("nan")
    result = output(case)
    assert result["status"] == "BLOCKED_CANDIDATE_DATA"
    assert result["capital_comparisons"] is None
    assert not any(name == "capital" for name, _ in case["calls"])


@pytest.mark.parametrize("change", ["missing", "source_sha", "no_attempt", "missing_t", "duplicate_t", "wrong_inputs", "write_permission"])
def test_collection_evidence_blocks_before_labels_or_training(case, change):
    if change == "missing": (case["root"] / "collection_receipt.json").unlink()
    else:
        if change == "source_sha": case["receipt"]["new_source_files"][0]["sha256"] = "0" * 64
        if change == "no_attempt": case["receipt"]["request_receipts"][0]["network_request_performed"] = False
        if change == "missing_t": case["receipt"]["request_receipts"] = []
        if change == "duplicate_t": case["receipt"]["request_receipts"] *= 2
        if change == "wrong_inputs": case["receipt"]["candidate_source_bindings"] = []
        if change == "write_permission": case["receipt"]["production_writes"] = True
        update_receipt(case)
    result = output(case)
    assert result["status"] == "BLOCKED_COLLECTION_EVIDENCE"
    assert result["capital_comparisons"] is None and case["calls"] == []


def test_expected_upstream_checks_only_metadata_not_old_results(case):
    saved = {"execution_provenance": {"run_id": SOURCE_RUN, "run_commit": SOURCE_COMMIT},
             "candidate_model": "DO_NOT_USE", "predictions": [{"candidate_score": 999}], "labels": "FAKE_LEGACY_LABELS"}
    write_json(case["root"] / "research_results/candidate.json", saved)
    result = output(case, expected_source_run_id=SOURCE_RUN, expected_source_commit=SOURCE_COMMIT)
    assert result["status"] == "CAPITAL_REPLAY_COMPLETE"
    assert not result["upstream_provenance"]["saved_candidate_consumed_as_truth"]
    replayed = json.loads((case["root"] / "research_capital/candidate_replay.json").read_text())
    assert replayed["candidate_model"] == {"model": "test-only"}
    assert all(row["candidate_score"] < 0 for row in replayed["predictions"])


@pytest.mark.parametrize("mode", ["missing", "wrong_run", "wrong_commit", "unpaired"])
def test_bad_upstream_output_is_structured_blocked_without_nav(case, mode):
    if mode != "missing":
        write_json(case["root"] / "research_results/candidate.json", {"execution_provenance": {
            "run_id": "123" if mode == "wrong_run" else SOURCE_RUN,
            "run_commit": "a" * 40 if mode == "wrong_commit" else SOURCE_COMMIT}})
    result = output(case, expected_source_run_id=SOURCE_RUN,
                    expected_source_commit=None if mode == "unpaired" else SOURCE_COMMIT)
    assert result["status"] == "BLOCKED_UPSTREAM_OUTPUT"
    assert result["capital_comparisons"] is None and case["calls"] == []
    saved = json.loads((case["root"] / "research_capital/candidate_replay.json").read_text())
    assert saved["candidate_model"] is None and not saved["report"]["training_performed"]


def test_source_bindings_and_current_code_shas_recorded(case):
    result = output(case)
    expected = {"evidence.json", "collection_receipt.json", ".dc20-profit-1000-research-root.json"}
    assert expected == {value["path"] for value in result["data_source_files"]}
    code = {value["path"]: value["sha256"] for value in result["execution_provenance"]["source_files"]}
    assert code["work/profit_1000_upgrade/capital_report.py"] == report.run.sha(report.HERE / "capital_report.py")
    assert code["work/profit_1000_upgrade/capital.py"] == report.run.sha(report.HERE / "capital.py")
    saved = json.loads((case["root"] / "research_capital/candidate_replay.json").read_text())
    assert result["candidate_replay_sha256"] == report._canonical_sha(saved)


def test_capital_wrapper_must_rebuild_labels_or_result_is_blocked(case, monkeypatch):
    monkeypatch.setattr(report, "replay_capital_from_repository", lambda *args, **kwargs: {
        "label_verification": "TRUST_CALLER", "production_activation_allowed": False, "source_files": []})
    result = output(case)
    assert result["status"] == "BLOCKED_CAPITAL_REPLAY"
    assert result["capital_comparisons"] is None


def test_existing_output_preflight_prevents_partial_second_write(case):
    path = case["root"] / "research_capital/capital.json"
    write_json(path, {"prior": True})
    raw = path.read_bytes()
    with pytest.raises(ValueError, match="immutable"):
        output(case)
    assert path.read_bytes() == raw
    assert not (path.parent / "candidate_replay.json").exists()
    assert case["calls"] == []


def test_symlink_output_parent_never_written(case):
    destination = case["root"] / "outside"
    destination.mkdir()
    (case["root"] / "research_capital").symlink_to(destination, target_is_directory=True)
    with pytest.raises(ValueError, match="immutable"):
        output(case)
    assert not list(destination.iterdir())


def test_plan_gate_drift_blocks_before_candidate_fit(case, monkeypatch):
    plan = report.run.load_plan()
    plan["data_gates"]["min_train_dates"] = 1
    monkeypatch.setattr(report.run, "load_plan", lambda: plan)
    result = output(case)
    assert result["status"] == "BLOCKED_PLAN_DRIFT" and not case["calls"]


def test_cli_paired_upstream_arguments_and_status_exit_code(case, monkeypatch, capsys):
    monkeypatch.setattr(report, "generate_capital_report", lambda root, **kwargs: {
        "status": "BLOCKED_UPSTREAM_OUTPUT", "reason": "missing", "kwargs": kwargs})
    monkeypatch.setattr("sys.argv", ["capital_report.py", "--root", str(case["root"]),
                                   "--expected-source-run-id", SOURCE_RUN, "--expected-source-commit", SOURCE_COMMIT])
    assert report.main() == 2
    assert json.loads(capsys.readouterr().out)["status"] == "BLOCKED_UPSTREAM_OUTPUT"
