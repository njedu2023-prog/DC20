import copy
import pytest
from work.path_reliability_replay_20260921.replay import minute, snapshot, classify, assess
from work.path_reliability_replay_20260921.score_review import score


def session(score):
    return dict(components=[score]*4,coverage=1.,gap=.01,first=600.,last=600.,opens=0.)


@pytest.mark.parametrize('value,expected', [('93000',570.),('93000.0',570.),('150000',900.),('',None),('126000',None),('160000',None)])
def test_clock(value,expected):
    assert minute(value)==expected


def test_missing_never_strong():
    a,b=session(.3),session(.9)
    a['components'][0]=None
    a['coverage']=.75
    assert assess([a,b])['candidate']=='INSUFFICIENT'


def test_boundary_abstains():
    a,b=session(.579),session(.8)
    assert assess([a,b])['candidate']=='UNCERTAIN'


def test_robust_direction():
    a,b=session(.3),session(.9)
    assert classify([a,b])=='WEAK_TO_STRONG'
    assert assess([a,b])['candidate']=='WEAK_TO_STRONG'


def test_invalid_time_abstains():
    a,b=session(.3),session(.9)
    b['last']=590.
    assert assess([a,b])['reason']=='INVALID_TIME_ORDER'


def test_no_mutation():
    seq=[session(.3),session(.9)]
    before=copy.deepcopy(seq)
    assess(seq)
    assert seq==before


def test_bad_count_is_missing():
    s=snapshot({'open':11,'pre_close':10},{'up_limit':11},
               {'open_times':-1,'first_time':93000,'last_time':93000,'amount':100,'fd_amount':10})
    assert s['components'][2] is None
    assert s['coverage']==.8


def test_seal_span_not_invented():
    s=snapshot({'open':11,'pre_close':10},{'up_limit':11},
               {'open_times':2,'first_time':93000,'last_time':140000,'amount':100,'fd_amount':10})
    assert 'open_duration' not in s
    assert 'seal_duration' not in s


def test_no_gold_no_accuracy():
    assert score([],[])['candidate']['selective_accuracy'] is None


def test_abstention_not_free_accuracy():
    record=dict(signal_date='20260801',ts_code='x',stage=2,baseline='WEAK_TO_STRONG',candidate='UNCERTAIN')
    ref=dict(record,reviewer_a='WEAK_TO_STRONG',reviewer_b='WEAK_TO_STRONG',
             adjudicated_label='WEAK_TO_STRONG',evidence_notes='independent review',independence_attested=True)
    result=score([record],[ref])
    assert result['candidate']['coverage']==0
    assert result['candidate']['per_label']['WEAK_TO_STRONG']['recall_including_abstentions']==0
    assert result['baseline']['selective_accuracy']==1


def test_unattested_reference_rejected():
    record=dict(signal_date='20260801',ts_code='x',stage=2,baseline='MIXED',candidate='MIXED')
    ref=dict(record,reviewer_a='MIXED',reviewer_b='MIXED',adjudicated_label='MIXED')
    with pytest.raises(ValueError,match='REFERENCE_EVIDENCE_REQUIRED'):
        score([record],[ref])
