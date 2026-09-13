"""Historical compatibility must reconstruct raw truth, not copy an old summary."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from top10decision.decision import executable_profit_shadow_settlement as settlement
from top10decision.decision import primary_profit_shadow_statistics as adapter


ROOT = Path(__file__).resolve().parents[1]
ARCHIVES = {
    "20260910": "21af0c5b9b77609622e28153d07c4bf21eaf61ea06dc6af671880744804ca647",
    "20260911": "b76147f4c8b0d10a348c4097192b68717d2bb6f9ae0aac05efbca8ee6e265534",
}


@pytest.mark.parametrize("day", ARCHIVES)
def test_historical_raw_reconstruction_matches_exact_immutable_archive(day):
    digest = ARCHIVES[day]
    archive = ROOT / settlement.STATISTICS_PATH.parent / "snapshots" / f"summary_asof_{day}_sha256_{digest}.json"
    raw = archive.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == digest
    historical = adapter.build_public_statistics(ROOT, as_of_date=day)
    assert settlement._canonical_bytes(historical) == raw
    assert historical["input_files"] == json.loads(raw)["input_files"]


@pytest.mark.parametrize("day", ["20260914", "20260915"])
def test_policy_era_statistics_pass_through_without_mixing_returns(day, monkeypatch):
    original = settlement.build_public_statistics(ROOT, as_of_date=day)
    calls = []
    def validated(root, *, as_of_date):
        calls.append((root, as_of_date))
        return original
    monkeypatch.setattr(settlement, "build_public_statistics", validated)
    monkeypatch.setattr(adapter, "_bound_inputs", lambda *args: pytest.fail("policy-era projection entered historical adapter"))
    assert adapter.build_public_statistics(ROOT, as_of_date=day) is original
    assert calls == [(ROOT, day)]
    cohort = original["cohorts"]["all_selected_slots"]
    assert cohort["return_policy_status"] == "MIXED_EXIT_POLICIES_NOT_COMBINED"
    assert cohort["mean_net_return_after_cost"] is None
    assert cohort["equal_weight_cumulative_return"] is None
    assert settlement.EXIT_POLICY_ID_1000 in cohort["by_exit_policy"]


@pytest.fixture
def bound_inputs_only(tmp_path, monkeypatch):
    summary = settlement.build_public_statistics(ROOT, as_of_date="20260911")
    for binding in summary["input_files"]:
        path = tmp_path / binding["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / binding["path"]).read_bytes())
    monkeypatch.setattr(settlement, "build_public_statistics", lambda *args, **kwargs: copy.deepcopy(summary))
    return tmp_path, summary


def test_adapter_uses_only_bound_inputs_without_any_archive_or_future_truth(bound_inputs_only, monkeypatch):
    root, summary = bound_inputs_only
    future = root / settlement.VERIFICATION_ROOT / "t_verification_20260911.json"
    future.parent.mkdir(parents=True, exist_ok=True)
    future.write_bytes(b"future truth deliberately excluded from the validated input scope")
    read_bytes = Path.read_bytes
    def only_bound(path):
        assert path != future, "adapter read future unbound truth"
        return read_bytes(path)
    monkeypatch.setattr(Path, "read_bytes", only_bound)
    actual = adapter.build_public_statistics(root, as_of_date="20260911")
    assert hashlib.sha256(settlement._canonical_bytes(actual)).hexdigest() == ARCHIVES["20260911"]
    assert actual["input_files"] == summary["input_files"]
    assert not (root / settlement.STATISTICS_PATH.parent).exists()


@pytest.mark.parametrize("stage", ["before", "during"])
@pytest.mark.parametrize("kind", ["selections", "verifications", "settlements"])
def test_input_bytes_cannot_change_after_validation_or_during_rebuild(bound_inputs_only, monkeypatch, kind, stage):
    root, summary = bound_inputs_only
    binding = next(item for item in summary["input_files"] if f"/{kind}/" in item["path"])
    path = root / binding["path"]
    if stage == "before":
        path.write_bytes(path.read_bytes() + b" ")
    else:
        original = settlement._cohort_metrics
        def changed(records):
            path.write_bytes(path.read_bytes() + b" ")
            return original(records)
        monkeypatch.setattr(settlement, "_cohort_metrics", changed)
    with pytest.raises(settlement.ExecutableProfitSettlementError, match="input changed"):
        adapter.build_public_statistics(root, as_of_date="20260911")


def test_original_validation_failure_is_not_adapted_or_hidden(monkeypatch):
    def rejected(*args, **kwargs):
        raise settlement.ExecutableProfitSettlementError("original validation rejected")
    monkeypatch.setattr(settlement, "build_public_statistics", rejected)
    with pytest.raises(settlement.ExecutableProfitSettlementError, match="original validation rejected"):
        adapter.build_public_statistics(ROOT, as_of_date="20260911")
