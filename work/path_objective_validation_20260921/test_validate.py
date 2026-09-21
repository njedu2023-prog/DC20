import pandas as pd
import pytest
from work.path_objective_validation_20260921.validate import validate_frames,profit_metrics,paired_blocks


def frame():
    return pd.DataFrame([dict(signal_date='20260101',ts_code='a',rank=1,score=.2,proxy_fill=1.,slot_net_return=.01),
                         dict(signal_date='20260101',ts_code='b',rank=2,score=.1,proxy_fill=0.,slot_net_return=0.)])


def test_no_fill_excluded_from_filled_mean():
    m=profit_metrics(frame())
    assert m['2']['mean_filled_net'] is None
    assert m['2']['mean_slot_net']==0
    assert m['1']['mean_filled_net_90bp']==pytest.approx(.0055)


@pytest.mark.parametrize('field,value',[('slot_net_return',float('nan')),('rank',1),('proxy_fill',.5)])
def test_invalid_data_rejected(field,value):
    a=frame();a.loc[1,field]=value
    with pytest.raises(ValueError):validate_frames({'A':a},'profit')


def test_truth_difference_rejected():
    a=frame();b=frame();b.loc[0,'slot_net_return']=.3
    with pytest.raises(ValueError,match='COHORT_OR_TRUTH'):validate_frames({'A':a,'B':b},'profit')


def test_missing_candidate_rejected():
    with pytest.raises(ValueError,match='COHORT_OR_TRUTH'):validate_frames({'A':frame(),'B':frame().iloc[:1]},'profit')


def test_rank_score_mismatch_rejected():
    a=frame();a['rank']=[2,1]
    with pytest.raises(ValueError,match='RANK_SCORE'):validate_frames({'A':a},'profit')


def test_paired_mismatch_rejected():
    a=pd.DataFrame({'x':[1]},index=['20260101']);b=a.copy();b.index=['20260102']
    with pytest.raises(ValueError):paired_blocks(a,b,'x')


def test_identical_has_zero_gain():
    a=pd.DataFrame({'x':[1,0,1]},index=['1','2','3'])
    assert paired_blocks(a,a,'x')['interval98_75']==[0.,0.]
