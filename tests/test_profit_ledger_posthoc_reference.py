"""Posthoc price display must not manufacture execution or settled returns."""
import re
import shutil
import subprocess
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]

def test_posthoc_reference_is_identity_bound_and_does_not_change_results():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required')
    html = (ROOT/'decision.html').read_text()
    functions = '\n'.join(re.search(r'^    function '+name+r'\(.*?^    }', html, re.M|re.S).group()
                          for name in ['ledgerPosthocReference','ledgerStatus','ledgerPerformance','ledgerCsv'])
    source = '''
const assert=require('node:assert/strict');
const executableProfitExpect=(x,m)=>assert.ok(x,m);
const original={signal_date:'20260921',slot:1,ts_code:'000910.SZ',status:'POSTHOC_SUPPLEMENT_NOT_FORMAL',entry_price:null,exit_price:null,slot_net_return:null};
const before=JSON.stringify(original), row=ledgerPosthocReference(original);
assert.equal(JSON.stringify(original),before);
assert.equal(row.entry_reference_price,6.03);assert.equal(row.exec_date,'20260922');
assert.equal(row.exit_date,'20260923');assert.equal(row.entry_price,null);
assert.equal(row.exit_price,null);assert.equal(row.slot_net_return,null);
assert.equal(ledgerPosthocReference({...original,slot:2,ts_code:'002589.SZ'}).entry_reference_price,3.81);
assert.throws(()=>ledgerPosthocReference({...original,slot:2}));
for(const r of [{...original,signal_date:'20260922'},{...original,status:'SETTLED_1000_LIMIT_HOLD_MINUTE_PROXY'}])assert.equal(ledgerPosthocReference(r),r);
assert.equal(ledgerPerformance([row]).settled,0);assert.equal(ledgerPerformance([row]).mean,null);
const csv=ledgerCsv([row]);assert.ok(csv.includes('事后竞价参考价'));assert.ok(csv.includes('6.03'));assert.ok(csv.includes('e54a69d28ec41f33be7f3a6dd9d127cf29593911'));
'''
    subprocess.run([node,'-e',functions+'\n'+source],check=True,capture_output=True,text=True)
    assert 'currentRows=loaded.map(ledgerPosthocReference)' in html
    assert 'price(r.entry_price ?? r.entry_reference_price)' in html
    assert '事后补算成绩（所选月份）' in html
