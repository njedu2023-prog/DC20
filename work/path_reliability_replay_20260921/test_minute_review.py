import pytest
from minute_review import inspect,EXPECTED


def rows():
    return [dict(ts_code='x',trade_time='2026-09-18 '+t,open='10',high='10',low='10',close='10',vol='1') for t in sorted(EXPECTED)]


def test_full_240_not_auction_241():
    result=inspect(rows(),'20260918','x',10)
    assert result['status']=='COMPLETE_MINUTE_PROXY'
    assert result['true_open_count'] is None
    assert result['true_seal_duration'] is None


def test_missing_minute_not_complete():
    assert inspect(rows()[1:],'20260918','x',10)['status']=='INCOMPLETE_OR_INVALID'


def test_zero_volume_not_trade_touch():
    r=rows()
    for x in r:x['vol']='0'
    out=inspect(r,'20260918','x',10)
    assert out['first_active_trade_touch_bar'] is None
    assert out['zero_volume_minutes']==240
    assert out['adjacent_active_close_departures'] is None


def test_empty_not_zero_opens():
    assert inspect([],'20260918','x',10)['adjacent_active_close_departures'] is None


@pytest.mark.parametrize('field,value',[('ts_code','y'),('trade_time','2026-09-17 09:31:00')])
def test_wrong_identity_rejected(field,value):
    r=rows();r[0][field]=value
    with pytest.raises(ValueError):inspect(r,'20260918','x',10)


def test_duplicate_minute_rejected():
    r=rows()
    with pytest.raises(ValueError):inspect(r+[r[0]],'20260918','x',10)


def test_bad_ohlc_not_complete():
    r=rows();r[0]['low']='11'
    assert inspect(r,'20260918','x',10)['status']=='INCOMPLETE_OR_INVALID'


def test_no_limit_not_complete():
    assert inspect(rows(),'20260918','x',None)['status']=='INCOMPLETE_OR_INVALID'


def test_extra_time_not_complete():
    r=rows();r.append(dict(r[0],trade_time='2026-09-18 12:00:00'))
    assert inspect(r,'20260918','x',10)['status']=='INCOMPLETE_OR_INVALID'
