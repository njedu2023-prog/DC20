"""Synthetic mixed-history cohort through unchanged native settlement kernel."""
import pytest
from work.profit_1000_upgrade import candidate_natural_forward_test as forward_fixtures
from work.profit_1000_upgrade import test_candidate_natural_outcomes as fixtures
from work.profit_1000_upgrade import candidate_eligible_forward as eligible


@pytest.mark.parametrize('kind', ['normal','missing_minutes'])
def test_excluded_promotion_comparator_still_settles_without_blocking_top2(tmp_path,monkeypatch,kind):
    monkeypatch.setattr(forward_fixtures,'m',eligible)
    original=fixtures.setup_case
    monkeypatch.setattr(fixtures,'setup_case',lambda *args,**kwargs:original(*args,missing_bar=True,**kwargs))
    case=fixtures.make_case(tmp_path,monkeypatch,size=10,kind=kind)
    raw=case['snapshot'].read_bytes()
    frozen=case['frozen']
    excluded=frozen['promotion_slots'][0]['ts_code']
    assert excluded not in {r['ts_code'] for r in frozen['prediction']['rows']}
    result=fixtures.report(fixtures.run(case))
    assert excluded in result['native_singleton_reports']
    assert len(result['candidate_slots'])==2
    assert result['registration_sha256']==frozen['registration_sha256']
    assert result['full_frozen_candidate_count']==9
    assert case['snapshot'].read_bytes()==raw
    if kind=='normal':
        assert all(r['status']==fixtures.m.labels.SETTLED for r in result['candidate_slots'])
        assert all(r['slot_net_return']==pytest.approx(-.0245) for r in result['candidate_slots'])
    else:
        assert any(r['rows'][0]['label_status']=='PENDING_EXIT_MISSING_MINUTES' for r in result['native_singleton_reports'].values())
