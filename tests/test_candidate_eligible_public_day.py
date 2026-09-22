"""Synthetic proof boundary for unchanged-model partial eligible publication."""
from datetime import datetime
from types import SimpleNamespace
import json
import re
import shutil
import subprocess
from pathlib import Path
import pytest
from top10decision.decision import candidate_profit_publication as m
from work.profit_1000_upgrade import candidate_natural_forward_test as fixtures
from work.profit_1000_upgrade import candidate_eligible_forward as eligible
from work.profit_1000_upgrade import candidate_natural_outcomes as outcomes
from work.profit_1000_upgrade.test_candidate_d_source_adapter import update_source


class TestProof:
    __test__=False
    def assert_unchanged(self): pass


def make_case(tmp_path,monkeypatch,size=10,missing=True):
    monkeypatch.setattr(fixtures,'m',eligible)
    base=fixtures.setup_case(tmp_path,monkeypatch,size=size,missing_bar=missing)
    p0_path=f"outputs/decision/three_rank_top10_{base['day']}.json"
    p0=json.loads((base['root']/p0_path).read_bytes())
    for stock in p0['rows']:
        stock.update(name='模拟股票'+stock['ts_code'],industry='模拟行业')
    base['expected']['three_rank_json']=update_source(base,p0_path,m.encoded(p0))
    receipt=fixtures.run(base)
    value=eligible._json(eligible._read(receipt['snapshot_path'])[0])
    value['clock_mode']='HOST_SYSTEM_UTC' # explicit synthetic private-proof test
    value['snapshot_sha256']=eligible.scorer.canonical_sha({k:v for k,v in value.items() if k!='snapshot_sha256'})
    raw=m.encoded(value)
    proof=TestProof();proof.signal_date=value['signal_date'];proof.snapshot_file_sha256=m.sha(raw)
    proof.observer_run_id=123;proof.evidence_commit='1'*40;proof.evidence_manifest_sha256='2'*64
    proof.publication_observation_sha256='3'*64
    proof.report={'status':'INDEPENDENT_OBSERVER_RESEARCH_PUBLICATION_OBSERVED','research_prospective_publication_observed':True,'injected_client_for_test':False}
    monkeypatch.setattr(m,'_dependencies',lambda:(outcomes,SimpleNamespace(VerifiedResearchPublication=TestProof)))
    monkeypatch.setattr(m,'_now',lambda:datetime.fromisoformat('2026-09-14T12:05:00+00:00'))
    return dict(snapshot_raw=raw,publication_proof=proof,current_p0_raw=(base['root']/f"outputs/decision/three_rank_top10_{value['signal_date']}.json").read_bytes(),activation=m.load_activation(),source_main_sha='4'*40)


@pytest.mark.parametrize('size,missing',[(0,False),(1,True),(2,True),(10,True),(10,False)])
def test_partial_publication_keeps_original_ranks_and_two_explicit_slots(tmp_path,monkeypatch,size,missing):
    case=make_case(tmp_path,monkeypatch,size,missing)
    result=m.project_verified_day(**case)
    assert result['original_candidate_count']==size
    assert result['candidate_count']==size-(1 if missing and size else 0)
    assert result['schema_version']==m.ELIGIBLE_DAY_SCHEMA
    assert [r['promotion_rank'] for r in result['original_rows']]==list(range(1,size+1))
    assert len(result['candidate_slots'])==len(result['promotion_slots'])==2
    if missing and size:
        assert result['promotion_slots'][0]['ts_code'] not in {r['ts_code'] for r in result['rows']}
    raw=m.encoded(result)
    assert m.validate_persisted_day(raw,expected_sha256=m.sha(raw),**{k:v for k,v in case.items() if k not in ('source_main_sha','publication_proof')})==result


@pytest.mark.parametrize('key,value',[('eligible',True),('reason','IGNORED_BAD_SHA'),('observed_bar_count',21)])
def test_public_eligibility_tampering_rejected(tmp_path,monkeypatch,key,value):
    case=make_case(tmp_path,monkeypatch)
    result=m.project_verified_day(**case)
    result['eligibility'][0][key]=value
    raw=m.encoded(result)
    with pytest.raises(ValueError):m.validate_public_day(raw,expected_sha256=m.sha(raw),activation=case['activation'])


def test_mixed_cohort_reaches_real_frontend_loader(tmp_path,monkeypatch):
    node=shutil.which('node')
    if not node:pytest.skip('Node required')
    case=make_case(tmp_path,monkeypatch)
    day=m.project_verified_day(**case)
    contract=json.loads(case['current_p0_raw'])
    html=(Path(__file__).parents[1]/'decision.html').read_text()
    functions='\n'.join(re.search(r'^    (?:async )?function '+name+r'\(.*?^    }',html,re.M|re.S).group() for name in ('candidatePublicOriginalRows','loadCandidateProfitDay','candidateUnifiedProfitView'))
    script=functions+'\nconst day='+json.dumps(day)+';const contract='+json.dumps(contract)+';const config='+json.dumps(case['activation'])+''';
const assert=require('node:assert/strict');
const executableProfitExpect=(ok,msg)=>assert.ok(ok,msg);
const exactObjectKeys=(o,ks)=>o && JSON.stringify(Object.keys(o).sort())===JSON.stringify([...ks].sort());
const validatedThreeRankContract=w=>w.three_rank;
const candidateProfitApplies=()=>true;
const dateText=d=>d.slice(0,4)+'-'+d.slice(4,6)+'-'+d.slice(6);
const fetchPagesOnlyShaBoundJson=async()=>({payload:day});
const entry={signal_date:day.signal_date,path:'fixture',sha256:'fixture',p0_file_sha256:day.p0_file_sha256,snapshot_file_sha256:day.snapshot_file_sha256};
const state={candidateProfitActivation:{status:'ready',config,index:{days:[entry]}}};
const wrapper={three_rank:contract,three_rank_source_index:{json_path:day.p0_source.path,json_sha256:day.p0_file_sha256}};
(async()=>{
const loaded=await loadCandidateProfitDay(wrapper);state.currentExecutableProfitResearch=loaded;
const view=candidateUnifiedProfitView(contract);
assert.equal(view.ready,true);assert.equal(view.rows.size,9);assert.equal(view.unscorable.size,1);
assert.ok(view.unscorable.has(contract.rows[0].ts_code));
assert.deepEqual([...view.rows.values()].map(r=>r.executable_profit_research_rank),[1,2,3,4,5,6,7,8,9]);
day.eligibility[0].eligible=true;
await assert.rejects(loadCandidateProfitDay(wrapper));
})().catch(e=>{console.error(e);process.exit(1)});
'''
    result=subprocess.run([node,'-'],input=script,text=True,capture_output=True)
    assert result.returncode==0,result.stderr
