import json,re,subprocess,shutil
import pytest
from pathlib import Path
root=Path(__file__).resolve().parents[1]
def test_d22_display_binding():
 node=shutil.which('node')
 if not node: pytest.skip('Node required')
 html=(root/'decision.html').read_text()
 fun=re.search(r'^    function candidateUnifiedProfitView\(.*?^    }',html,re.M|re.S).group()
 contract={"signal_date":"20260922","exec_date":"20260923","exit_date":"20260924","bundle_sha256":"71217a05ebda432102bd3b9832fc28e1e658fd44cc4a907c74d494edb891eb6a","feature_snapshot_sha256":"b853bb62ef81939a7eb37de0e240cf4912edf07b7edc39ac42eb3a3be25b44b8","top10_members_sha256":"602c5c9b12277556958d02e18848d095e5ae4ed9046099f9d7dd8865696e0ed4","rows":[{"ts_code":"601811.SH","promotion_rank":1},{"ts_code":"600825.SH","promotion_rank":2},{"ts_code":"000607.SZ","promotion_rank":3},{"ts_code":"600743.SH","promotion_rank":4},{"ts_code":"002614.SZ","promotion_rank":5},{"ts_code":"000910.SZ","promotion_rank":6},{"ts_code":"002303.SZ","promotion_rank":7},{"ts_code":"603636.SH","promotion_rank":8},{"ts_code":"001234.SZ","promotion_rank":9},{"ts_code":"002259.SZ","promotion_rank":10}]}
 js='const assert=require("node:assert/strict"); const state={candidateProfitActivation:{status:"ready",config:{model_canonical_sha256:"999666791b147e4d120ba9b7e10d9d1fc846ba171efbba73b2488ca56ce6f589"},index:{days:[]}}}; const c='+json.dumps(contract)+';'+fun+'''
 const before=JSON.stringify(c);const v=candidateUnifiedProfitView(c);
 assert.equal(v.rows.get("000910.SZ").executable_profit_research_rank,1);
 assert.equal(v.rows.get("002303.SZ").executable_profit_research_rank,2);
 assert.equal(v.rows.size,9);assert.ok(v.supplement);assert.ok(v.unscorable.has("600825.SH"));
 assert.equal(JSON.stringify(c),before);assert.ok(v.message.includes("非正式冻结"));
 for(const key of ["signal_date","exec_date","exit_date","bundle_sha256","feature_snapshot_sha256","top10_members_sha256"])
 assert.equal(candidateUnifiedProfitView({...c,[key]:"bad"}).ready,false);
 assert.equal(candidateUnifiedProfitView({...c,rows:c.rows.slice(1)}).ready,false);
 state.candidateProfitActivation.index.days=[{signal_date:"20260922"}];
 assert.equal(candidateUnifiedProfitView(c).ready,false);
 '''
 subprocess.run([node,'-e',js],check=True,capture_output=True,text=True)
