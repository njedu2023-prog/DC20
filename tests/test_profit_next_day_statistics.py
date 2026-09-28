"""Frozen new-model T-close descriptives; never settlement or trading returns."""
import pytest
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('market_helpers', Path(__file__).with_name('test_next_day_market_statistics.py'))
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
run = helpers.run


def test_profit_next_day_start_ranks_pending_and_limit_close():
    r=run('''const rows=[row('20260913','20260914'),row('20260914','20260915'),row('20260915','20260916'),row('20260916','20260917'),row('20260924','20260928'),row('20260914','20260915',2)];
const daily=new Map([['20260914',[quote('20260914',999)]],['20260915',[quote('20260915',110)]],['20260916',[quote('20260916',90)]],['20260917',[quote('20260917',100)]]]);
const limits=new Map([['20260915',[{ts_code:'000001.SZ',trade_date:'20260915',up_limit:110}]],['20260916',[{ts_code:'000001.SZ',trade_date:'20260916',up_limit:110}]],['20260917',[{ts_code:'000001.SZ',trade_date:'20260917',up_limit:110}]]]);
console.log(JSON.stringify(profitNextDayStatistics(rows,'20260924','20260924',daily,limits)));''')
    assert len(r)==2
    assert (r[0]['valid'],r[0]['pending'],r[0]['up'])==(3,1,1)
    assert r[0]['mean']==pytest.approx(0) and r[0]['median']==0
    assert r[0]['upRate']==r[0]['limitRate']==pytest.approx(1/3)
    assert r[1]['upRate']==r[1]['limitRate']==1


@pytest.mark.parametrize('bands', ['[]','[{ts_code:"000001.SZ",trade_date:"20260915",up_limit:0}]','[{ts_code:"000001.SZ",trade_date:"20260916",up_limit:110}]'])
def test_profit_next_day_missing_limit_not_zero_or_guessed(bands):
    r=run('''console.log(JSON.stringify(profitNextDayStatistics([row('20260914','20260915')],'20260914','20260915',new Map([['20260915',[quote('20260915',110)]]]),new Map([['20260915','''+bands+''']]))[0]));''')
    assert r['upRate']==1 and r['limitRate'] is None


@pytest.mark.parametrize('mode',['ready','failure','stale','timeout'])
def test_profit_next_day_optional_nonblocking(mode):
    r=run('''const mode="'''+mode+'''";let renders=0;renderProfitNextDayStatistics=()=>{renders++};
const active={};const loaded={marketInputs:{}};state.currentCompactStatisticsWindow=loaded;state.candidateProfitActivation=active;
loadProfitNextDayStatistics=async()=>{if(mode==='failure')throw Error('source mismatch');if(mode==='stale')state.currentCompactStatisticsWindow={};if(mode==='timeout')return new Promise(()=>{});return []};
if(mode==='timeout'){globalThis.setTimeout=fn=>{queueMicrotask(fn);return 0};globalThis.clearTimeout=()=>{}};
await refreshProfitNextDayStatistics(loaded,active);console.log(JSON.stringify({status:loaded.profitNextDay.status,renders,fatal:!!state.compactFatalError}));''')
    assert not r['fatal']
    assert r['status']==('ready' if mode=='ready' else 'loading' if mode=='stale' else 'unavailable')
    assert r['renders']==(0 if mode=='stale' else 1)


def test_profit_next_day_two_cards_four_metrics_no_fraction():
    r=run('''const active={};state.candidateProfitActivation=active;
state.currentCompactStatisticsWindow={marketInputs:{summary:{as_of_date:'20260924'}},profitNextDay:{active,status:'ready',cohorts:[1,2].map(rank=>({rank,upRate:.7,limitRate:.6,mean:.0581,median:.0999}))}};
renderProfitNextDayStatistics();console.log(JSON.stringify(document.getElementById('profitNextDayContent').innerHTML));''')
    assert r.count('上涨率')==r.count('涨停率')==r.count('平均涨跌幅')==r.count('中位涨跌幅')==2
    assert 'D 2026-09-14起' in r and '盈1' in r and '盈2' in r
    assert '70.00%' in r and '+5.81%' in r and '(7/10)' not in r


@pytest.mark.parametrize('mode',['ok','sha','unbound'])
def test_profit_next_day_loader_bound_sources_and_frozen_slots(mode):
    r=run('''const mode="'''+mode+'''";const calls=[];const active={config:{},index:{days:[{signal_date:'20260913'},{signal_date:'20260914',p0_file_sha256:'p0'}]}};
fetchPagesOnlyShaBoundJson=async(path,sha)=>{calls.push([path,sha]);return {payload:{signal_date:'20260914',exec_date:'20260915',exit_date:'20260916'}}};
loadCandidateProfitDay=async p=>({activation:active.config,projection:{candidate_slots:[{slot:1,ts_code:'000001.SZ'},{slot:2,ts_code:null}]}});
const daily=new TextEncoder().encode('ts_code,trade_date,close,pre_close,vol\\n000001.SZ,20260915,110,100,1\\n');
const limit=new TextEncoder().encode('ts_code,trade_date,up_limit\\n000001.SZ,20260915,110\\n');
fetchPagesOnlyPath=async p=>{calls.push([p]);return p.endsWith('/daily.csv')?daily:limit};
const files=await Promise.all([['daily',daily],['stk_limit',limit]].map(async([name,bytes])=>({path:'data/market/raw/2026/20260915/'+name+'.csv',sha256:mode==='sha'?'0'.repeat(64):await sha256Hex(bytes)})));
try{const cohorts=await loadProfitNextDayStatistics(active,{summary:{as_of_date:'20260915'},payload:{report_signal_date:'20260914',source_files:mode==='unbound'?[]:files}});console.log(JSON.stringify({cohorts,calls}))}catch(e){console.log(JSON.stringify({error:e.message,calls}))}''')
    assert all('20260913' not in call[0] for call in r['calls'])
    if mode=='sha':
        assert 'SHA' in r['error']
    else:
        assert r['cohorts'][1]['upRate'] is None
        assert r['cohorts'][0]['valid']==(1 if mode=='ok' else 0)
        if mode=='unbound': assert len(r['calls'])==1
