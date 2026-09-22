"""Mixed-history v2 collection; synthetic transport only, never real quotes."""
from work.profit_1000_upgrade import candidate_natural_forward_test as forward
from work.profit_1000_upgrade import test_candidate_natural_outcomes as frozen_fixtures
from work.profit_1000_upgrade import test_candidate_natural_outcome_collect as fixture
from work.profit_1000_upgrade import candidate_eligible_forward as eligible


def test_v2_collects_original_promotion_comparator_and_candidate_top2(tmp_path, monkeypatch):
    monkeypatch.setattr(forward, "m", eligible)
    original = frozen_fixtures.setup_case
    monkeypatch.setattr(frozen_fixtures, "setup_case", lambda *a, **kw: original(*a, missing_bar=True, **kw))
    case = fixture.setup(tmp_path, monkeypatch, size=10)
    raw = case["snapshot"].read_bytes()
    excluded = case["frozen"]["promotion_slots"][0]["ts_code"]
    assert excluded not in {r["ts_code"] for r in case["frozen"]["prediction"]["rows"]}
    first, _ = fixture.collect(case)
    assert first["full_frozen_candidate_count"] == 10
    assert excluded in first["slot_union_codes"]
    prior = fixture.observe(case, first)
    second, _ = fixture.collect(case, previous=first, prior_outcome=prior, asof=case["t1"])
    result = fixture.observe(case, second, previous=prior)
    assert result["ledger_file_sha256"]
    assert case["snapshot"].read_bytes() == raw


def test_legacy_writer_is_not_accepted_for_v2():
    c = fixture.c
    v2 = {"schema_version": eligible.SCHEMA}
    assert c.accepted_outcome_writers(v2) == {c.OUTCOMES_SHA}
    assert c.LEGACY_OUTCOMES_SHA in c.accepted_outcome_writers({"schema_version": c.natural.SCHEMA})
