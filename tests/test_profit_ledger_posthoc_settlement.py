"""Supplemented research returns never enter the formal monthly denominator."""
import json
import re
import shutil
import subprocess
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]


def test_verified_posthoc_display_and_export_preserve_formal_boundary():
    node=shutil.which('node')
    if not node: pytest.skip('Node required')
    html=(ROOT/'decision.html').read_text()
    functions='\n'.join(re.search(r'^    function '+name+r'\(.*?^    }',html,re.M|re.S).group() for name in
        ('ledgerStatus','ledgerPosthocReference','ledgerPosthocSettlement','ledgerPerformance','ledgerCsv'))
    fixture=json.loads((ROOT/'outputs/decision/posthoc_settlement/20260921/settlement.json').read_text())
    script=r'''
const assert=require('node:assert/strict');
const executableProfitExpect=(ok,msg)=>assert.ok(ok,msg);
const originals=[['000910.SZ',1],['002589.SZ',2]].map(([ts_code,slot])=>({ts_code,slot,signal_date:'20260921',status:'POSTHOC_SUPPLEMENT_NOT_FORMAL',entry_price:null,exit_price:null,slot_net_return:null}));
const before=JSON.stringify(originals);
const rows=ledgerPosthocSettlement(originals.map(ledgerPosthocReference),evidence);
assert.equal(JSON.stringify(originals),before);
assert.equal(rows.length,2);
for(const r of rows){assert.equal(r.posthoc_settled,true);assert.equal(ledgerStatus(r).kind,'supplement');assert.equal(r.status,'POSTHOC_SUPPLEMENT_NOT_FORMAL');assert.ok(Number.isFinite(r.slot_net_return));}
assert.equal(ledgerPerformance(rows).settled,0);
assert.equal(ledgerPerformance(rows).mean,null);
const formal={signal_date:'20260922',slot:1,status:'SETTLED_1000_LIMIT_HOLD_MINUTE_PROXY',slot_net_return:.1};
assert.equal(ledgerPerformance([...rows,formal]).mean,.1);
assert.equal(ledgerPerformance([...rows,formal]).settled,1);
const csv=ledgerCsv(rows);
assert.ok(csv.includes('POSTHOC_SUPPLEMENT_NOT_FORMAL'));
for(const r of rows)assert.ok(csv.includes(String(r.slot_net_return)));
for(const change of [x=>x.formal_statistics_eligible=true,x=>x.rows[0].ts_code='000001.SZ',x=>x.rows[0].exec_date='20260923',x=>x.rows[0].net_return+=.01,x=>x.rows[0].entry_evidence.price_qualified=false]){
 const copy=structuredClone(evidence);change(copy);assert.throws(()=>ledgerPosthocSettlement(originals.map(ledgerPosthocReference),copy));
}
const pending=structuredClone(evidence);pending.rows[0].status='PENDING_SOURCE_EVIDENCE';pending.rows[0].net_return=null;
const p=ledgerPosthocSettlement(originals.map(ledgerPosthocReference),pending)[0];assert.notEqual(p.posthoc_settled,true);assert.equal(p.slot_net_return,null);
'''
    subprocess.run([node,'-e',functions+'\nconst evidence='+json.dumps(fixture)+';\n'+script],check=True,capture_output=True,text=True)


def test_result_bytes_and_every_source_are_bound():
    import hashlib
    root=ROOT/'outputs/decision/posthoc_settlement/20260921'
    raw=(root/'settlement.json').read_bytes();data=json.loads(raw)
    assert hashlib.sha256(raw).hexdigest() in (ROOT/'decision.html').read_text()
    assert data['failures']==[]
    assert data['workflow_run_id'] and re.fullmatch('[0-9a-f]{40}',data['source_commit'])
    for binding in data['source_files']:
        assert not Path(binding['path']).is_absolute() and '..' not in Path(binding['path']).parts
        content=(root/binding['path']).read_bytes()
        assert hashlib.sha256(content).hexdigest()==binding['sha256']
        assert len(content)==binding['bytes']
