"""Unit-test actual observer snapshot function without issuing GitHub authority."""
import ast
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_candidate_eligible_public_day import make_case
from work.profit_1000_upgrade import candidate_natural_forward as natural


def validator():
    root=Path(__file__).parents[1]
    source=(root/'work/profit_1000_upgrade/candidate_natural_publication.py').read_text()
    tree=ast.parse(source)
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_snapshot')
    ns={'ROOT':root,'Path':Path,'natural':natural,
        'gh':SimpleNamespace(sha256=natural._sha,read=natural._read),'require':natural.require}
    exec(compile(ast.Module(body=[node],type_ignores=[]),'observer_snapshot_unit','exec'),ns)
    return ns['_snapshot']


def test_observer_partial_snapshot_is_validated_not_marked_complete(tmp_path,monkeypatch):
    case=make_case(tmp_path,monkeypatch)
    result=validator()(case['snapshot_raw'],'20260914')
    assert result['prediction']['eligible_candidate_count']==9
    assert result['D_source_evidence']['projection']['projection_window_complete'] is False
    assert result['production_activation_allowed'] is False


@pytest.mark.parametrize('mutation',['date','clock','completeness'])
def test_observer_rejects_wrong_day_test_clock_or_forged_completeness(tmp_path,monkeypatch,mutation):
    case=make_case(tmp_path,monkeypatch)
    value=natural._json(case['snapshot_raw'])
    if mutation=='clock':value['clock_mode']='INJECTED_TEST_CLOCK_RESEARCH_ONLY'
    if mutation=='completeness':value['D_source_evidence']['projection']['projection_window_complete']=True
    value['snapshot_sha256']=natural.scorer.canonical_sha({k:v for k,v in value.items() if k!='snapshot_sha256'})
    with pytest.raises(ValueError):
        validator()(natural.storage.encoded(value),'20260915' if mutation=='date' else '20260914')
