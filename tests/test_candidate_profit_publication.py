"""Synthetic private-proof boundary; unchanged fixed model and P0 parser.

These tests do not produce an actual observer proof, contact a market provider,
publish a ranking, or claim a natural settlement.
"""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import socket

import pytest

from top10decision.decision import candidate_profit_publication as m
from work.profit_1000_upgrade import candidate_natural_outcomes as outcomes
from work.profit_1000_upgrade import candidate_natural_evidence_publication as issuer
from work.profit_1000_upgrade.candidate_natural_forward_test import setup_case, run as freeze


class SyntheticProof:
    """Monkeypatched unit-test boundary, never available to the production API."""
    def __init__(self, signal, snapshot_sha):
        self.signal_date = signal
        self.snapshot_file_sha256 = snapshot_sha
        self.observer_run_id = 123456
        self.evidence_commit = "1" * 40
        self.evidence_manifest_sha256 = "2" * 64
        self.publication_observation_sha256 = "3" * 64
        self.report = {"status": "INDEPENDENT_OBSERVER_RESEARCH_PUBLICATION_OBSERVED",
                       "research_prospective_publication_observed": True,
                       "injected_client_for_test": False}
        self.calls = 0
        self.after = None

    def assert_unchanged(self):
        self.calls += 1
        if self.after:
            self.after(self.calls)


def make_case(tmp_path, monkeypatch, size=2):
    base = setup_case(tmp_path, monkeypatch, size=size)
    receipt = freeze(base)
    snapshot = json.loads(Path(receipt["snapshot_path"]).read_bytes())
    monkeypatch.setattr(outcomes, "REGISTRATION_SHA", snapshot["registration_sha256"])
    snapshot["clock_mode"] = "HOST_SYSTEM_UTC"  # synthetic envelope, not real source admission
    snapshot.pop("snapshot_sha256")
    snapshot["snapshot_sha256"] = outcomes.natural.scorer.canonical_sha(snapshot)
    raw = m.encoded(snapshot)
    proof = SyntheticProof(snapshot["signal_date"], m.sha(raw))
    monkeypatch.setattr(issuer, "VerifiedResearchPublication", SyntheticProof)
    monkeypatch.setattr(m, "_now", lambda: datetime.fromisoformat("2026-09-14T12:05:00+00:00"))
    p0 = (base["root"] / f"outputs/decision/three_rank_top10_{snapshot['signal_date']}.json").read_bytes()
    return {"snapshot_raw": raw, "current_p0_raw": p0, "publication_proof": proof,
            "activation": m.load_activation(), "source_main_sha": "4" * 40}


@pytest.fixture
def case(tmp_path, monkeypatch):
    return make_case(tmp_path, monkeypatch)


def project(case):
    return m.project_verified_day(**case)


def validate(day, activation=None):
    raw = m.encoded(day)
    return m.validate_public_day(raw, expected_sha256=m.sha(raw), activation=activation or m.load_activation())


def cache(case, day):
    raw = m.encoded(day)
    return m.validate_cached_day(raw, expected_sha256=m.sha(raw),
                                 **{k: v for k, v in case.items() if k != "source_main_sha"})


def reseal_snapshot(case, change):
    snap = json.loads(case["snapshot_raw"])
    change(snap)
    snap.pop("snapshot_sha256")
    snap["snapshot_sha256"] = outcomes.natural.scorer.canonical_sha(snap)
    case["snapshot_raw"] = m.encoded(snap)
    case["publication_proof"].snapshot_file_sha256 = m.sha(case["snapshot_raw"])


def test_enabled_explicit_separate_activation_preserves_research_registration():
    config = m.load_activation()
    assert config == m.EXPECTED_ACTIVATION
    registration = json.loads(outcomes.natural.REGISTRATION_PATH.read_bytes())
    assert registration["production_activation_allowed"] is False
    assert registration["replacement_review_thresholds"] == "NOT_CONFIGURED"


def test_projection_uses_all_frozen_scores_preserves_promotion_and_is_read_only(case, monkeypatch):
    before = {k: v for k, v in case.items() if k != "publication_proof"}
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("NO_NETWORK"))
    monkeypatch.setattr(outcomes.natural.scorer, "predict_forward", lambda *a, **k: pytest.fail("NO_RESCORE"))
    result = project(case)
    frozen = json.loads(case["snapshot_raw"])
    assert result["candidate_slots"] == frozen["candidate_slots"]
    assert result["promotion_slots"] == frozen["promotion_slots"]
    assert [(r["ts_code"], r["candidate_score"], r["promotion_rank"]) for r in result["rows"]] == [
        (r["ts_code"], r["candidate_score"], r["promotion_rank"]) for r in frozen["prediction"]["rows"]]
    assert all(result[k] is False for k in ("profitability_improvement_proven", "actual_execution_claimed", "score_is_probability"))
    assert result["snapshot_file_sha256"] == m.sha(case["snapshot_raw"])
    assert validate(result) == result
    assert cache(case, result) == result
    assert before == {k: v for k, v in case.items() if k != "publication_proof"}


@pytest.mark.parametrize("size", [0, 1, 10, 13])
def test_zero_one_and_full_top10_have_two_explicit_slots(tmp_path, monkeypatch, size):
    case = make_case(tmp_path, monkeypatch, size=size)
    result = project(case)
    assert result["candidate_count"] == min(size, 10)
    assert len(result["candidate_slots"]) == 2
    for slot in result["candidate_slots"][min(size, 2):]:
        assert slot["status"] == "MISSING_CANDIDATE"
        assert slot["ts_code"] is None and slot["net_return"] is None


@pytest.mark.parametrize("value", [True, {"admitted": True}, None])
def test_plain_admission_cannot_create_formal_day(case, value):
    case["publication_proof"] = value
    with pytest.raises(ValueError, match="REAL_PRIVATE"):
        project(case)


def test_subclass_is_not_proof(case):
    class Forged(SyntheticProof):
        pass
    original = case["publication_proof"]
    case["publication_proof"] = Forged(original.signal_date, original.snapshot_file_sha256)
    with pytest.raises(ValueError, match="REAL_PRIVATE"):
        project(case)


@pytest.mark.parametrize("field,value", [("injected_client_for_test", True),
                                         ("research_prospective_publication_observed", False),
                                         ("status", "SYNTHETIC_OBSERVER_CHECK_ONLY")])
def test_test_transport_cannot_activate(case, field, value):
    case["publication_proof"].report[field] = value
    with pytest.raises(ValueError, match="ACTUAL_PROSPECTIVE"):
        project(case)


@pytest.mark.parametrize("stamp", ["2026-09-14T06:59:59+00:00", "2026-09-15T01:20:00+00:00", "2026-09-15T01:25:00+00:00"])
def test_new_day_cannot_publish_before_close_or_after_safety_boundary(case, monkeypatch, stamp):
    monkeypatch.setattr(m, "_now", lambda: datetime.fromisoformat(stamp))
    with pytest.raises(ValueError, match="PRE_T0920"):
        project(case)


def test_old_immutable_day_can_revalidate_late_but_not_reproject(case, monkeypatch):
    day = project(case)
    monkeypatch.setattr(m, "_now", lambda: datetime.fromisoformat("2026-09-17T12:00:00+00:00"))
    assert cache(case, day) == day
    with pytest.raises(ValueError, match="PRE_T0920"):
        project(case)


def test_disable_blocks_new_days_but_retains_existing_day(case):
    day = project(case)
    case["activation"]["enabled"] = False
    with pytest.raises(ValueError, match="ACTIVATION_DISABLED"):
        project(case)
    assert cache(case, day) == day
    result = m.build_public_index([day], activation=case["activation"], source_main_sha="4" * 40,
                                  generated_at_utc="2026-09-14T12:10:00+00:00")
    assert result["status"] == "DISABLED" and len(result["days"]) == 1


@pytest.mark.parametrize("field,value", [("effective_from_signal_date", "20260911"), ("enabled", 1),
                                         ("model_canonical_sha256", "f" * 64), ("negative_score_skip_allowed", True),
                                         ("real_order_execution_allowed", True), ("legacy_statistics_merge_allowed", True)])
def test_activation_scope_cannot_drift(case, field, value):
    case["activation"][field] = value
    with pytest.raises(ValueError):
        project(case)


def test_changed_p0_or_snapshot_bytes_rejected(case):
    case["current_p0_raw"] += b" "
    with pytest.raises(ValueError, match="P0_BYTES"):
        project(case)


def test_snapshot_injected_clock_rejected_even_with_resealed_json(case):
    reseal_snapshot(case, lambda snap: snap.update(clock_mode="INJECTED_TEST_CLOCK_RESEARCH_ONLY"))
    with pytest.raises(ValueError, match="SYNTHETIC_CLOCK"):
        project(case)


def test_resealed_wrong_model_rejected(case):
    reseal_snapshot(case, lambda snap: snap.update(model_canonical_sha256="f" * 64))
    with pytest.raises(ValueError):
        project(case)


def test_wrong_proof_day_is_not_admission(case):
    case["publication_proof"].signal_date = "20260915"
    with pytest.raises(ValueError, match="ACTIVATION_SAME_D"):
        project(case)


def test_empty_index_waits_not_claiming_results():
    value = m.build_public_index([], activation=m.load_activation(), source_main_sha="4" * 40,
                                 generated_at_utc="2026-09-14T06:00:00+00:00")
    assert value["status"] == "ACTIVE_WAITING_FIRST_NATURAL_D"
    assert value["days"] == [] and value["latest_signal_date"] is None
    assert value["activation"] == m.activation_identity(m.load_activation())
    raw = m.encoded(value)
    assert m.validate_public_index(raw, expected_sha256=m.sha(raw), activation=m.load_activation()) == value


def test_index_is_sha_bound_and_no_duplicate_days(case):
    day = project(case)
    value = m.build_public_index([day], activation=m.load_activation(), source_main_sha="4" * 40,
                                 generated_at_utc="2026-09-14T12:10:00+00:00")
    entry = value["days"][0]
    assert entry["sha256"] == m.sha(m.encoded(day))
    assert entry["path"] == "outputs/decision/candidate_profit_v1/day_20260914.json"
    raw = m.encoded(value)
    assert m.validate_public_index(raw, expected_sha256=m.sha(raw), activation=m.load_activation()) == value
    with pytest.raises(ValueError, match="DUPLICATE"):
        m.build_public_index([day, day], activation=m.load_activation(), source_main_sha="4" * 40,
                              generated_at_utc="2026-09-14T12:10:00+00:00")


@pytest.mark.parametrize("kind", ["score", "promotion_rank", "slot", "fill", "model", "p0sha", "date", "late", "realclaim"])
def test_public_structure_and_cached_source_reject_tampering(case, kind):
    day = project(case)
    if kind == "score":
        day["rows"][0]["candidate_score"] += 1
        day["candidate_slots"][0]["candidate_score"] += 1
        for slot in day["promotion_slots"]:
            if slot["ts_code"] == day["rows"][0]["ts_code"]:
                slot["candidate_score"] += 1
    elif kind == "promotion_rank": day["rows"][0]["promotion_rank"] = 99
    elif kind == "slot": day["candidate_slots"][0]["ts_code"] = "999999.SZ"
    elif kind == "fill": day["candidate_slots"][0]["net_return"] = 0
    elif kind == "model": day["model_canonical_sha256"] = "f" * 64
    elif kind == "p0sha":
        day["p0_file_sha256"] = "f" * 64
        day["p0_source"]["sha256"] = "f" * 64
    elif kind == "date": day["signal_date"] = "20260915"
    elif kind == "late": day["projection_generated_at_utc"] = "2026-09-15T01:20:00+00:00"
    else: day["actual_execution_claimed"] = True
    with pytest.raises(ValueError):
        cache(case, day)


def test_external_hash_duplicate_keys_and_nan_fail_closed(case):
    day = project(case); raw = m.encoded(day)
    with pytest.raises(ValueError, match="EXTERNAL_SHA"):
        m.validate_public_day(raw, expected_sha256="f" * 64, activation=m.load_activation())
    for bad in (b'{"a":1,"a":2}', b'{"score":NaN}'):
        with pytest.raises(ValueError):
            m.validate_public_day(bad, expected_sha256=m.sha(bad), activation=m.load_activation())


def test_activation_mutation_during_last_proof_guard_is_rejected(case):
    def mutate(calls):
        if calls == 2:
            case["activation"]["enabled"] = False
    case["publication_proof"].after = mutate
    with pytest.raises(ValueError, match="PROOF_OR_ACTIVATION"):
        project(case)


def test_persisted_day_checks_sources_without_claiming_new_proof(case, monkeypatch):
    day = project(case); raw = m.encoded(day)
    monkeypatch.setattr(m, "_now", lambda: datetime.fromisoformat("2027-01-01T00:00:00+00:00"))
    case["publication_proof"].after = lambda _: pytest.fail("DO_NOT_REISSUE_EXPIRED_PROOF_FOR_OLD_BYTES")
    result = m.validate_persisted_day(raw, expected_sha256=m.sha(raw), snapshot_raw=case["snapshot_raw"],
                                     current_p0_raw=case["current_p0_raw"], activation=case["activation"])
    assert result == day
    assert result["actual_execution_claimed"] is False
    # This pure consistency check cannot promote the old dict to a new proof.
    case["publication_proof"] = result
    with pytest.raises(ValueError, match="REAL_PRIVATE"):
        project(case)


@pytest.mark.parametrize("field", ["snapshot_raw", "current_p0_raw"])
def test_persisted_day_external_source_changes_still_fail(case, field):
    day = project(case); raw = m.encoded(day)
    case[field] += b" "
    with pytest.raises(ValueError):
        m.validate_persisted_day(raw, expected_sha256=m.sha(raw), snapshot_raw=case["snapshot_raw"],
                                current_p0_raw=case["current_p0_raw"], activation=case["activation"])


def test_persisted_consistency_rejects_resealed_score_even_with_valid_public_shape(case):
    day = project(case)
    selected = day["rows"][0]
    selected["candidate_score"] += 1
    for field in ("candidate_slots", "promotion_slots"):
        for slot in day[field]:
            if slot["ts_code"] == selected["ts_code"]:
                slot["candidate_score"] += 1
    raw = m.encoded(day)
    with pytest.raises(ValueError, match="FROZEN_RANK"):
        m.validate_persisted_day(raw, expected_sha256=m.sha(raw), snapshot_raw=case["snapshot_raw"],
                                current_p0_raw=case["current_p0_raw"], activation=case["activation"])
