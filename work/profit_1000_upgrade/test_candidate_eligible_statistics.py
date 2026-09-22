"""Synthetic settlement reports, not independent publication evidence."""
from datetime import datetime, timezone
from copy import deepcopy
import pytest
from work.profit_1000_upgrade import candidate_natural_statistics as m
from work.profit_1000_upgrade import test_candidate_natural_outcomes as fixtures
from work.profit_1000_upgrade import candidate_natural_forward_test as forward
from work.profit_1000_upgrade import candidate_eligible_forward as eligible


def report_case(tmp_path,monkeypatch,v2):
    if v2:
        monkeypatch.setattr(forward,'m',eligible)
        original=fixtures.setup_case
        monkeypatch.setattr(fixtures,'setup_case',lambda *a,**k:original(*a,missing_bar=True,**k))
    case=fixtures.make_case(tmp_path,monkeypatch,size=10)
    report=fixtures.report(fixtures.run(case))
    # Exercise structural report admission only, never issue a publication proof.
    report['clock_mode']=report['frozen_clock_mode']='HOST_SYSTEM_UTC'
    reseal(report)
    dates=fixtures.m.labels.settlement._strict_open_dates(case['base']['root'])
    return case,report,dates


def reseal(report):
    report['report_sha256']=fixtures.m.natural.scorer.canonical_sha({k:v for k,v in report.items() if k!='report_sha256'})


@pytest.mark.parametrize('v2',[False,True])
def test_versioned_statistics_accepts_original_union(tmp_path,monkeypatch,v2):
    case,report,dates=report_case(tmp_path,monkeypatch,v2)
    m._version(report,case['frozen'],case['snapshot_sha'],dates,datetime(2026,9,18,tzinfo=timezone.utc))
    assert len(report['candidate_slots'])==2
    if v2:
        assert case['frozen']['promotion_slots'][0]['ts_code'] in report['native_singleton_reports']
    else:
        report['writer_sha256']=m.LEGACY_OUTCOMES_SHA;reseal(report)
        m._version(report,case['frozen'],case['snapshot_sha'],dates,datetime(2026,9,18,tzinfo=timezone.utc))


def test_v2_cannot_claim_legacy_writer(tmp_path,monkeypatch):
    case,report,dates=report_case(tmp_path,monkeypatch,True)
    report['writer_sha256']=m.LEGACY_OUTCOMES_SHA;reseal(report)
    with pytest.raises(ValueError,match='UNREGISTERED_OUTCOME_WRITER_PROFILE'):
        m._version(report,case['frozen'],case['snapshot_sha'],dates,datetime(2026,9,18,tzinfo=timezone.utc))
