"""Exact requested D uses a verified formal list before optional research."""
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="Node required")
def test_formal_same_d_precedence_and_confirmed_missing_research_route():
    text = (ROOT / "decision.html").read_text()
    start = text.index('        state.dailyRequestedDate = parameters.get("view")')
    end = text.index('        const research = parameters.get("view")', start)
    branch = text[start:end]
    script = r"""
const assert=require('node:assert/strict');
const run=new Function('env',`return (async()=>{const {state,parameters,loadCurrentThreeRank,loadDailyNavigation,initializeDailyHistory,loadLateResearchDisplay}=env; const LATE_RESEARCH_DAY='20261119',force=false; const executableProfitExpect=(ok,msg)=>{if(!ok)throw Error(msg)}; __BRANCH__ return 'current';})()`);
async function check({requested=null,latest='20261119',dates=['20261118','20261119'],catalogError=false,formalError=false,lateError=false,historyError=false,expect='current',late=false,error=false}){
 const calls=[],state={};const env={state,parameters:new URLSearchParams(requested===null?'':'d='+requested),
  loadCurrentThreeRank:async()=>{calls.push('current');if(formalError)throw Error('formal SHA failed');return latest?{signal_date:latest}:null},
  loadDailyNavigation:async()=>{calls.push('catalog');if(catalogError)throw Error('catalog SHA failed');return dates.map(signal_date=>({signal_date}))},
  initializeDailyHistory:async day=>{calls.push('history:'+day);if(historyError)throw Error('dated formal SHA failed');},
  loadLateResearchDisplay:async()=>{calls.push('late');if(lateError)throw Error('late SHA failed');return {signal_date:'20261119'}}};
 let thrown=false;try{const result=await run(env);assert.equal(result,expect==='history'?undefined:expect)}catch(e){if(!error)throw e;thrown=true;assert.match(e.message,/SHA failed|不能认定/)}
 assert.equal(thrown,error);assert.equal(!!state.lateResearch,late);if(!late)assert(!calls.includes('late')||lateError);
 return {requested,latest,dates,calls,error:thrown,late:!!state.lateResearch};
}
(async()=>{
 const results=[];
 for(const requested of [null,'20261119'])results.push(await check({requested,lateError:true}));
 results.push(await check({requested:'20261119',latest:'20261120',dates:['20261118','20261119','20261120'],expect:'history',lateError:true}));
 results.push(await check({latest:'20261118',dates:['20261118','20261119'],expect:'history'}));
 for(const requested of [null,'20261119'])results.push(await check({requested,latest:'20261118',dates:['20261118'],late:true}));
 results.push(await check({requested:'20261119',latest:'20261120',dates:['20261118','20261120'],late:true}));
 results.push(await check({requested:'20261119',latest:'20261118',dates:['20261118'],catalogError:true,error:true}));
 results.push(await check({requested:'20261119',formalError:true,error:true}));
 results.push(await check({requested:'20261119',latest:'20261120',historyError:true,error:true}));
 results.push(await check({requested:'20261119',latest:null,dates:['20261118'],error:true}));
 results.push(await check({requested:'20261119',latest:'20261120',dates:['20261118'],error:true}));
 results.push(await check({requested:'20261118',expect:'history',lateError:true}));
 results.push(await check({latest:'20261120',lateError:true}));
 console.log(`PASS ${results.length} route cases; formal precedence, confirmed absence and failure boundaries`);
})().catch(e=>{console.error(e);process.exit(1)});

""".replace("__BRANCH__", branch)
    process = subprocess.run([NODE, "-"], input=script, text=True, capture_output=True, timeout=30)
    assert process.returncode == 0, process.stderr
    assert "PASS 14 route cases" in process.stdout
