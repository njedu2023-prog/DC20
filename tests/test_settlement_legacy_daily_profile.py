from copy import deepcopy
from types import SimpleNamespace
import pytest
from work.profit_1000_upgrade import candidate_natural_settlement_workflow as m

LEGACY = {
    'daily_module_sha256': '88d75a131946be1884da459b8d357942b29f18fd4d83839ea918951291d7e321',
    'dependencies': {
        'work/profit_1000_upgrade/candidate_natural_outcome_collect.py': '1d9addaa1aecaf1082023c28ab26b23b8ee5ab79d5fd7b9fd24f55e1320d9ced',
        'scripts/diagnose_core_supervisor.py': 'b21794ddd38510ce06c1cbe958fe0744ce28547d998fceb6f34a31b66a5d5a99'}}
V1 = {'schema_version': 'dc20_fixed_candidate_natural_research_snapshot_20260913_v1'}
V2 = {'schema_version': 'dc20_fixed_candidate_eligible_research_snapshot_20260922_v2'}

def test_exact_original_daily_writer_only_for_v1():
    modules=m.dependencies()
    assert m.daily_metadata_profile(LEGACY, V1, modules)
    assert not m.daily_metadata_profile(LEGACY, V2, modules)

def test_current_writer_remains_valid():
    modules=m.dependencies()
    current={'daily_module_sha256': m.PINS['candidate_natural_daily'], 'dependencies': modules['daily'].PINS}
    for frozen in (V1,V2): assert m.daily_metadata_profile(current,frozen,modules)

@pytest.mark.parametrize('change',['writer','dependency','extra','mixed'])
def test_no_unknown_or_mixed_writer_profile(change):
    modules=m.dependencies(); bad=deepcopy(LEGACY)
    if change=='writer': bad['daily_module_sha256']='0'*64
    if change=='dependency': bad['dependencies']['scripts/diagnose_core_supervisor.py']='0'*64
    if change=='extra': bad['dependencies']['unknown']='0'*64
    if change=='mixed': bad['dependencies']=modules['daily'].PINS
    assert not m.daily_metadata_profile(bad,V1,modules)
