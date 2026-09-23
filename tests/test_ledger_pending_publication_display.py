import re
import shutil
import subprocess
from pathlib import Path
import pytest

def test_pending_publication_does_not_mask_historical_missing_or_settlement():
    node=shutil.which('node')
    if not node: pytest.skip('Node required')
    html=(Path(__file__).resolve().parents[1]/'decision.html').read_text()
    function=re.search(r'^    function ledgerStatus\(.*?^    }',html,re.M|re.S).group()
    script="""
const assert=require('node:assert/strict');
const now=Date.parse('2026-09-23T16:15:00+08:00');
assert.equal(ledgerStatus({status:'MISSING_D_PUBLICATION',signal_date:'20260923'},now).label,'等待晚间名单发布');
assert.equal(ledgerStatus({status:'MISSING_D_PUBLICATION',signal_date:'20260921'},now).kind,'missing');
assert.equal(ledgerStatus({status:'PENDING_T_NOT_DUE',exec_date:'20260924'},now).label,'等待买入日竞价');
assert.equal(ledgerStatus({status:'PENDING_T1'},now).label,'待结算');
"""
    subprocess.run([node,'-e',function+script],check=True,capture_output=True,text=True)
    assert 'r.ts_code ? (status.label === "等待买入日竞价"' in html
