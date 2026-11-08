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


@pytest.mark.parametrize('mode',['ready','failure','stale'])
def test_profit_next_day_optional_nonblocking(mode):
    r=run('''const mode="'''+mode+'''";let renders=0;renderProfitNextDayStatistics=()=>{renders++};
const active={};const loaded={marketInputs:{}};state.currentCompactStatisticsWindow=loaded;state.candidateProfitActivation=active;
loadProfitNextDayStatistics=async()=>{if(mode==='failure')throw Error('source mismatch');if(mode==='stale')state.currentCompactStatisticsWindow={};return {status:'ready',cohorts:[]}};
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
    assert r.count('<strong class="success-rate">70.00%</strong>') == 2


def test_profit_next_day_browser_no_longer_loads_all_frozen_days():
    html=(helpers.ROOT/'decision.html').read_text()
    loader=html.split('async function loadProfitNextDayStatistics(',1)[1].split('async function refreshProfitNextDayStatistics(',1)[0]
    assert 'loadPublishedNextDayStatistics' in loader
    assert 'loadCandidateProfitDay' not in loader and 'daily.csv' not in loader
    assert 'candidate_profit_index_sha256' in loader
