"""Late D21 display is separate from original frozen ranks and shadow results."""
import json
import re
import shutil
import subprocess
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]

def test_supplement_boundaries_and_original_evidence_priority():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required')
    html = (ROOT / 'decision.html').read_text()
    function = re.search(r'^    function candidateUnifiedProfitView\(.*?^    }', html, re.M | re.S).group()
    function += '\n' + re.search(r'^    function candidatePublicOriginalRows\(.*?^    }', html, re.M | re.S).group()
    source = '''
const assert = require('node:assert/strict');
const executableProfitExpect=(ok,msg)=>assert.ok(ok,msg);
const state = {candidateProfitActivation:{status:'ready',config:{model_canonical_sha256:'999666791b147e4d120ba9b7e10d9d1fc846ba171efbba73b2488ca56ce6f589'},index:{days:[]}}};
const contract = {signal_date:'20260921',exec_date:'20260922',exit_date:'20260923',bundle_sha256:'7d5c1338bd4223f93d2a4578b000adf7b046fc9107d84efae354ab47a1185a99',feature_snapshot_sha256:'efc03271e9d6df05216060458f77c564b9aa7a258fa72d2610c3831276d166e5',top10_members_sha256:'0dbf050900345292bce1e0cbdd2e36f6084c6ec79f7a18be1c9b0a77479b46f4',rows:['000504.SZ','600630.SH','002453.SZ','000910.SZ','001376.SZ','002589.SZ','600606.SH','000532.SZ','600448.SH','601123.SH'].map((ts_code,i)=>({ts_code,promotion_rank:i+1}))};
const before=JSON.stringify(contract), stateBefore=JSON.stringify(state);
let view=candidateUnifiedProfitView(contract);
assert.equal(view.ready,true); assert.equal(view.supplement,true);
assert.equal(view.rows.size,9); assert.equal(view.rows.get('000910.SZ').executable_profit_research_rank,1);
assert.equal(view.rows.get('002589.SZ').executable_profit_research_rank,2);
assert.ok(view.unscorable.has('601123.SH')); assert.ok(!view.rows.has('601123.SH'));
assert.ok(view.message.includes('补算')); assert.ok(view.message.includes('不计原影子账本'));
assert.equal(JSON.stringify(contract),before); assert.equal(JSON.stringify(state),stateBefore);
for(const key of ['signal_date','exec_date','exit_date','bundle_sha256','feature_snapshot_sha256','top10_members_sha256']) {
 assert.equal(candidateUnifiedProfitView({...contract,[key]:'tampered'}).ready,false);
}
assert.equal(candidateUnifiedProfitView({...contract,rows:contract.rows.slice(1)}).ready,false);
assert.equal(candidateUnifiedProfitView({...contract,rows:contract.rows.map(r=>({...r,promotion_rank:1}))}).ready,false);
state.candidateProfitActivation.status='invalid'; assert.equal(candidateUnifiedProfitView(contract).ready,false);
state.candidateProfitActivation.status='ready';
state.candidateProfitActivation.index.days=[{signal_date:'20260921'}];
assert.equal(candidateUnifiedProfitView(contract).ready,false);
state.currentExecutableProfitResearch={status:'ready',kind:'candidate_fixed_ridge',activation:state.candidateProfitActivation.config,projection:{...contract,schema_version:'dc20_candidate_formal_profit_day_v1',rows:contract.rows.map((r,i)=>({...r,candidate_rank:i+1,candidate_score:1-i}))}};
view=candidateUnifiedProfitView(contract); assert.equal(view.ready,true);assert.ok(!view.supplement);assert.equal(view.rows.size,10);
'''
    subprocess.run([node, '-e', function + '\n' + source], check=True, capture_output=True, text=True)
    assert '暂不可评分' in html
