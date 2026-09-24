import pytest
from scripts.collect_posthoc_20260921 import qualify_entry, run


def entry():
    return ({'open':6.03,'high':6.89,'low':5.94,'close':6.89,'pre_close':6.26,'vol':10000},
        {'up_limit':6.89,'down_limit':5.63},
        {'status':'CANONICAL_PRICE_OBSERVED','price_qualified':True,'price':6.03,'auction_trade_observed':True,'capacity_proxy_verified':False,'amount':None})

def test_qualified_price_is_not_an_execution_claim():
    assert qualify_entry(*entry())=='QUALIFIED_RESEARCH_PROXY'

def test_source_conflict_cannot_settle():
    daily,limits,evidence=entry(); evidence['price']=6.04
    with pytest.raises(ValueError,match='ENTRY_SOURCE_CONFLICT'): qualify_entry(daily,limits,evidence)

def test_limit_up_remains_no_fill():
    daily,limits,evidence=entry(); daily['open']=evidence['price']=limits['up_limit']
    assert qualify_entry(daily,limits,evidence)=='NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED'

def test_missing_qualification_stays_pending():
    daily,limits,evidence=entry(); evidence['price_qualified']=False
    assert qualify_entry(daily,limits,evidence)=='PENDING_CANONICAL_ENTRY_QUALIFICATION'

def test_missing_credential_cannot_create_evidence(tmp_path,monkeypatch):
    monkeypatch.delenv('TUSHARE_TOKEN',raising=False)
    with pytest.raises(ValueError,match='CREDENTIAL_ABSENT'): run(tmp_path/'out')
    assert not (tmp_path/'out').exists()
