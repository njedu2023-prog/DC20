"""Test the reviewed D1124 deployment-only transition independently of successor run IDs."""
from copy import deepcopy

import pytest

from tests.top1_paper_source_delta import before_pages, before_readiness, before_rank_ledger
from work.profit_1000_upgrade import candidate_natural_p0_continuation as m
from work.profit_1000_upgrade import candidate_natural_p0_github as gh
from work.profit_1000_upgrade.test_candidate_natural_p0_continuation import call, fixture, pages_zip

DEPLOY = '.github/workflows/deploy_dc20_pages.yml'
ORIGIN = dict(signal_date='20261124', original_run_id='45384357456',
              original_head='754181fa30ba4da37fe2c9a4f514112746395df7',
              original_cas='bce6a4231a3d0406274be45ed805d6d684ed3606')
# The successor in this fixture is synthetic. Real deployments are verified via verify_pair.
SUCCESSOR = dict(reuse_run_id='22222', deployed_head='1' * 40)


def evidence(prior=False):
    def tree(profile):
        return {path:dict(type='blob', mode='100644', sha=blob) for path,blob in profile.items()}
    original=tree(m.LIST_DETAIL_UI_P0_CODE_PROFILE)
    deployed=tree(m.PAGES_TRANSPORT_P0_CODE_PROFILE if prior else m.PAGES_READINESS_P0_CODE_PROFILE)
    blobs={}
    for path in (DEPLOY,m.MANIFEST_PATH):
        new=(gh.CODE_ROOT / path).read_bytes()
        old=before_pages(path,new)
        if prior:
            new=before_readiness(path,new)
        else:
            new=before_rank_ledger(path,new)
        for target,raw in ((original,old),(deployed,new)):
            sha=gh.git_blob(raw)
            if path in target:
                assert target[path]['sha']==sha
            target[path]=dict(type='blob',mode='100644',sha=sha)
            blobs[sha]=raw
    class Reads:
        def blob(self, tree, path):
            return blobs[tree[path]['sha']]
    return [original,deepcopy(original),deployed],Reads(),blobs


@pytest.mark.parametrize('prior', [False, True])
def test_original_cas_profile_survives_pages_transport_change(prior):
    trees,reads,_=evidence(prior)
    identity={**ORIGIN,**SUCCESSOR}
    profile,proof=m.validate_code_profiles(gh,reads,*trees,identity)
    assert profile==m.LIST_DETAIL_UI_P0_CODE_PROFILE
    assert {k:proof[k] for k in identity}==identity
    assert proof['original_deploy_workflow_sha256']==gh.sha256(reads.blob(trees[0],DEPLOY))
    assert proof['deployed_deploy_workflow_sha256']==gh.sha256(reads.blob(trees[2],DEPLOY))
    # Prior same-version replays are unaffected and have no migration field.
    assert m.validate_code_profiles(gh,reads,trees[0],trees[1],trees[0],identity)==(profile,None)


@pytest.mark.parametrize('field',sorted(ORIGIN))
def test_transport_transition_limited_to_original_d_run_and_cas(field):
    trees,reads,_=evidence()
    other='20261125' if field=='signal_date' else '0'*len(ORIGIN[field])
    changed={**ORIGIN,**SUCCESSOR,field:other}
    with pytest.raises(ValueError,match='EXACT_ORIGINAL'):
        m.validate_code_profiles(gh,reads,*trees,changed)


@pytest.mark.parametrize('tree_index',[0,1,2])
@pytest.mark.parametrize('path',[DEPLOY,m.MANIFEST_PATH])
def test_transition_rejects_mutated_manifest_or_transport_bytes(tree_index,path):
    trees,reads,blobs=evidence()
    blobs[trees[tree_index][path]['sha']]+=b'\n'
    with pytest.raises(ValueError,match='EXACT_MANIFEST_PIN_CHANGE|WORKFLOW_BYTES_CHANGED'):
        m.validate_code_profiles(gh,reads,*trees,{**ORIGIN,**SUCCESSOR})


@pytest.mark.parametrize('tree_index',[0,1,2])
def test_transport_transition_rejects_other_model_code_or_workflow_change(tree_index):
    trees,reads,_=evidence()
    for path in sorted(set(m.LIST_DETAIL_UI_P0_CODE_PROFILE)-{m.MANIFEST_PATH}):
        changed=deepcopy(trees)
        changed[tree_index][path]['sha']='0'*40
        with pytest.raises(ValueError,match='REVIEWED_CODE_PROFILE_REQUIRED'):
            m.validate_code_profiles(gh,reads,*changed,{**ORIGIN,**SUCCESSOR})
    for key,value in [('type','tree'),('mode','120000')]:
        changed=deepcopy(trees)
        changed[tree_index][DEPLOY][key]=value
        with pytest.raises(ValueError,match='REGULAR_BLOB_REQUIRED'):
            m.validate_code_profiles(gh,reads,*changed,{**ORIGIN,**SUCCESSOR})


@pytest.mark.parametrize('scenario', ['natural', 'manual', 'failed_deploy'])
def test_verified_pair_requires_real_success_and_natural_successor(tmp_path,monkeypatch,scenario):
    case=fixture(tmp_path,monkeypatch,current_profile=True)
    # The previous tests bind real, reviewed code trees. Isolate event/job validation
    # here using the existing synthetic pair and all its original artifact/ancestry checks.
    validate=m.validate_code_profiles
    def marked(*args):
        profile,_=validate(*args)
        return profile,{'kind':'D20261124_PAGES_TRANSPORT_ONLY'}
    monkeypatch.setattr(m,'validate_code_profiles',marked)
    if scenario=='manual':
        day=case['revision']['primary_d_signal_date']
        title='DC20 controlled daily NATURAL | D='+day
        case['r1'].update(event='workflow_dispatch',name=title,display_title=title)
        case['revision']['event_name']='workflow_dispatch'
        archive=pages_zip(case['revision'])
        case['client'].archives[case['art1']['id']]=archive
        case['art1'].update(size_in_bytes=len(archive),digest='sha256:'+gh.sha256(archive))
        with pytest.raises(ValueError,match='NATURAL_REUSE_REQUIRED'):
            call(case)
    elif scenario=='failed_deploy':
        case['jobs1'][2]['conclusion']='failure'
        with pytest.raises(ValueError,match='EXACT_JOB_OUTCOMES_REQUIRED'):
            call(case)
    else:
        proof,_,_=call(case)
        assert proof['original_generation_run']['conclusion']=='failure'
        assert proof['successful_reuse_run']['conclusion']=='success'
        assert proof['successful_reuse_run']['event'] in ('schedule','workflow_run')
