import copy
from stress import clock,joint_labels,raw_variants,summarize,rate_interval,outcome_diagnostic
from replay import minute


def session(value):
    return dict(components=[value]*4,coverage=1.,gap=.01,first=600.,last=600.,opens=0.)


def test_joint_complete_and_deterministic():
    seq=[session(.57),session(.8)]
    before=copy.deepcopy(seq)
    a=list(joint_labels(seq))
    assert len(a)==256
    assert a==list(joint_labels(seq))
    assert seq==before
    assert len(set(a))>1


def test_clock_roundtrip():
    assert minute(clock(570.5))==570.5


def test_raw_changes_propagate():
    d=dict(open=10,pre_close=10)
    l=dict(up_limit=11,down_limit=9)
    x=dict(first_time='100000',last_time='110000',open_times=2,amount=100,seal_amount=10)
    variants=list(raw_variants(d,l,x))
    assert len(variants)==8
    assert len({s['gap'] for k,s in variants if k=='open_tick'})==2
    assert len({s['opens'] for k,s in variants if k=='open_count'})==2
    assert d['open']==10


def test_invalid_scenarios_not_generated():
    d=dict(open=11,pre_close=10)
    l=dict(up_limit=11,down_limit=9)
    x=dict(first_time='093000',last_time='093000',open_times=0,amount=100,seal_amount=10)
    variants=list(raw_variants(d,l,x))
    assert len([s for k,s in variants if k=='open_tick'])==1
    assert all(s['first']<=s['last'] for k,s in variants)
    assert all(s['opens']>=0 for k,s in variants)


def test_overlap_not_added_as_independent_rows():
    r=dict(stage=2,prior_sensitive=True,joint_flips=10,raw_flip_causes={'open_count':2},candidate='UNCERTAIN')
    out=summarize([r])['2']
    assert out['rows']==1 and out['retained']==0
    assert out['raw_cause_rows']['open_count']==1


def test_no_samples_not_zero_accuracy():
    assert rate_interval(0,0)['rate'] is None
    assert rate_interval(5,10)['wilson95'][0]<.5<rate_interval(5,10)['wilson95'][1]


def test_unmatured_not_failure():
    rows=[dict(signal_date='20260918',ts_code='x',stage=2)]
    result=outcome_diagnostic(rows,{('20260918','daily'):{}})
    assert result['groups']=={}
    assert result['excluded']['FUTURE_DATE_UNAVAILABLE']==1
