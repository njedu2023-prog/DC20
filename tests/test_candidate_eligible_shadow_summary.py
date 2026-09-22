"""Actual v2 frozen projection binding, synthetic proof boundary only."""
from copy import deepcopy
import json
import pytest
from top10decision.decision import candidate_formal_shadow_summary as m
from test_candidate_eligible_public_day import make_case


def test_summary_accepts_full_original_universe_with_partial_profit_rows(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch)
    day = m.publication.project_verified_day(**case)
    item = {"snapshot_raw": case["snapshot_raw"], "expected_snapshot_sha256": m.sha(case["snapshot_raw"])}
    identity = m._activation(case["activation"])
    result = m._projections([day], {day["signal_date"]: item}, identity)
    assert len(result[day["signal_date"]][0]["rows"]) == 9
    assert result[day["signal_date"]][0]["original_candidate_count"] == 10


@pytest.mark.parametrize("field", ["eligibility", "original_rows", "original_candidate_count", "schema_version"])
def test_summary_rejects_changed_v2_evidence(tmp_path, monkeypatch, field):
    case = make_case(tmp_path, monkeypatch)
    day = m.publication.project_verified_day(**case)
    frozen = json.loads(case["snapshot_raw"])
    changed = deepcopy(day)
    if field == "eligibility": changed[field][0]["eligible"] = True
    elif field == "original_rows": changed[field].pop()
    elif field == "original_candidate_count": changed[field] = 9
    else: changed[field] = m.DAY_SCHEMA
    with pytest.raises(ValueError): m._projection_profile(changed, frozen)
