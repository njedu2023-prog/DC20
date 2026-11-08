"""Optional T-close descriptive statistics: no ranking or settlement mutations."""
import json
import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')
pytestmark = pytest.mark.skipif(not NODE, reason='Node required')


def run(body):
    script = re.search(r'<script>(.*?)</script>', (ROOT/'decision.html').read_text(), re.S).group(1)
    script = script.replace('initialize(false).catch(showError);', '')
    prelude = r'''
const crypto=require('crypto').webcrypto;
const nodes=new Map();
const element=()=>({innerHTML:'',textContent:'',hidden:false,open:false,disabled:false,title:'',
setAttribute(){},removeAttribute(){},addEventListener(){},querySelector(){return element()},
querySelectorAll(){return []},classList:{toggle(){},add(){},remove(){}}});
const document={getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)},querySelectorAll:()=>[],addEventListener(){}};
const location={search:'',protocol:'https:',href:'https://njedu2023-prog.github.io/DC20/'};
const window={location,addEventListener(){}};
'''
    setup = r'''
const row=(d,t,rank=1,code='000001.SZ')=>({signal_date:d,exec_date:t,promotion_rank:rank,ts_code:code});
const quote=(t,close,pre=100,vol=1)=>({ts_code:'000001.SZ',trade_date:t,close,pre_close:pre,vol});
'''
    result = subprocess.run([NODE,'-'], input=prelude+script+setup+'\n(async()=>{'+body+'})().catch(e=>{console.error(e);process.exit(1)});',text=True,capture_output=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_start_inclusive_flat_in_denominator_negative_in_mean_and_even_median():
    r=run('''const rows=[row('20260909','20260910'),row('20260910','20260911'),row('20260911','20260914'),row('20260914','20260915'),row('20260915','20260916'),row('20260924','20260928')];
const tables=new Map([['20260910',[quote('20260910',999)]],['20260911',[quote('20260911',110)]],['20260914',[quote('20260914',90)]],['20260915',[quote('20260915',100)]],['20260916',[quote('20260916',120)]]]);
console.log(JSON.stringify(nextDayMarketStatistics(rows,'20260910','20260924','20260924',tables)));''')
    c=r[0]
    assert (c['count'],c['valid'],c['up'],c['pending'],c['missing'])==(5,4,2,1,0)
    assert c['upRate']==.5
    assert c['mean']==pytest.approx(.05) and c['median']==pytest.approx(.05)
    assert r[1]['mean'] is None and r[1]['upRate'] is None


@pytest.mark.parametrize('bad', ['null','quote("20260911",110,0)','quote("20260911",110," ")','quote("20260911",110,100,0)','quote("20260911","NaN")','quote("20260912",110)','quote("20260911",110),quote("20260911",110)'])
def test_missing_suspended_duplicate_and_wrong_day_not_zero(bad):
    r=run(f'''const table=[{bad}].filter(Boolean);console.log(JSON.stringify(nextDayMarketStatistics([row('20260910','20260911')],'20260910','20260910','20260911',new Map([['20260911',table]]))[0]));''')
    assert r['missing']==1 and r['valid']==0 and r['mean'] is None and r['upRate'] is None


def test_rank_two_is_second_stock_not_top_two_portfolio_and_odd_median():
    r=run('''const rows=[row('20260910','20260911',1),row('20260910','20260911',2),row('20260911','20260914',2),row('20260914','20260915',2)];
const tables=new Map([['20260911',[quote('20260911',110)]],['20260914',[quote('20260914',80)]],['20260915',[quote('20260915',105)]]]);console.log(JSON.stringify(nextDayMarketStatistics(rows,'20260910','20260914','20260915',tables)));''')
    assert r[0]['count']==1 and r[1]['count']==3 and r[3]['count']==4
    assert r[1]['median']==pytest.approx(.05)


def test_browser_loader_uses_precomputed_artifact_not_historical_quotes():
    html=(ROOT/'decision.html').read_text()
    loader=html.split('async function loadNextDayMarketStatistics(',1)[1].split('async function refreshNextDayMarketStatistics(',1)[0]
    assert 'loadPublishedNextDayStatistics' in loader
    assert 'daily.csv' not in loader and 'fetchPagesOnlyPath' not in loader


@pytest.mark.parametrize('mode',['ready','failure','stale'])
def test_optional_completion_cannot_invalidate_core_or_stale_page(mode):
    r=run('''const mode='''+json.dumps(mode)+''';let renders=0;renderCompactDashboard=()=>{renders++};
state.compactStatisticsWindowLoad={status:'ready'};const loaded={marketInputs:{}};
loadNextDayMarketStatistics=async()=>{if(mode==='failure')throw Error('bad source');return {status:'ready',cohorts:[]}};
await refreshNextDayMarketStatistics(loaded,()=>mode!=='stale');console.log(JSON.stringify({status:loaded.marketPerformance.status,core:state.compactStatisticsWindowLoad.status,renders}));''')
    assert r['core']=='ready'
    assert r['status']=={'ready':'ready','failure':'unavailable','stale':'loading'}[mode]
    assert r['renders']==(0 if mode=='stale' else 1)


def test_real_render_has_four_original_cards_and_no_profit_confusion():
    r=run('''const cohorts=nextDayMarketStatistics([row('20260910','20260911')],'20260910','20260910','20260911',new Map([['20260911',[quote('20260911',110)]]]));
compactStatisticsWindowView=()=>({promotion:{status:'READY',as_of_date:'20260911',ranks:[1,2,3].map(rank=>({rank,count:1,verified:1,hits:1,hit_rate:1}))}});
state.currentCompactStatisticsWindow={allPromotion:{count:1,verified:1,hits:1,hit_rate:1},marketPerformance:{status:'ready',cohorts}};
renderCompactDashboard();console.log(JSON.stringify(els.compactStatisticsContent.innerHTML));''')
    assert r.count('class="success-rate"')==4
    assert r.count('平均涨跌幅')==4 and r.count('中位涨跌幅')==4
    assert 'D 2026-09-10起' in r and '100.00%' in r
    assert '(1/1)' not in r and '(0/0)' not in r
    assert '有效 1 · 待验证 0 · 缺行情 0' not in r
    assert '次日涨跌幅＝' not in r
    assert '<h2 id="compactStatisticsTitle">晋级次日表现</h2>' in (ROOT/'decision.html').read_text()


def test_optional_async_is_not_awaited_and_production_backend_unchanged():
    html=(ROOT/'decision.html').read_text()
    assert 'void refreshNextDayMarketStatistics(state.currentCompactStatisticsWindow, current);' in html
    assert 'await refreshNextDayMarketStatistics(state.currentCompactStatisticsWindow' not in html


def test_source_review_changes_only_frontend_and_its_pins():
    manifest=json.loads((ROOT/'models/decision_model_freeze.json').read_bytes())
    html=(ROOT/'decision.html').read_bytes()
    assert manifest['pinned_files']['decision.html']==hashlib.sha256(html).hexdigest()
    review=json.loads((ROOT/'models/decision_source_surface_review_20260928_nightly_stats.json').read_bytes())
    assert {c['path'] for c in review['source_changes']}=={'.github/workflows/deploy_dc20_pages.yml','decision.html','models/decision_model_freeze.json','forward/model_inventory.json'}
    for item in review['source_changes']:
        assert item['current_sha256']==hashlib.sha256((ROOT/item['path']).read_bytes()).hexdigest()
    entry=next(a for a in json.loads((ROOT/'forward/model_inventory.json').read_bytes())['assets'] if a['path']=='models/decision_model_freeze.json')
    assert entry['sha256']==hashlib.sha256((ROOT/entry['path']).read_bytes()).hexdigest()
