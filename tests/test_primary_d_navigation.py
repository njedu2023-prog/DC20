"""Read-only daily navigation, exercised with the complete production script."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="Node required")


def run(body, search=""):
    script = re.search(r"<script>(.*?)</script>", (ROOT / "decision.html").read_text(), re.S).group(1)
    script = script.replace("initialize(false).catch(showError);", "")
    prelude = r"""
const fs=require('fs'), crypto=require('crypto').webcrypto;
const nodes=new Map();
const element=()=>({innerHTML:'',textContent:'',hidden:false,open:false,disabled:false,title:'',listeners:{},
 setAttribute(k,v){this[k]=v},removeAttribute(){},addEventListener(k,v){this.listeners[k]=v},
 querySelector(){return element()},querySelectorAll(){return []},classList:{toggle(){},add(){},remove(){}}});
const document={getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)},querySelectorAll:()=>[],addEventListener(){}};
let assigned=null,replaced=null;
const location={search:SEARCH,protocol:'https:',href:'https://njedu2023-prog.github.io/DC20/'+SEARCH,
 assign(url){assigned=url},replace(url){replaced=url}};
const window={location,addEventListener(){}};
""".replace("SEARCH", json.dumps(search))
    tail = r"""
const root=ROOT;
const calls=[];
const read=path=>new Uint8Array(fs.readFileSync(root+'/'+path));
const payload=path=>JSON.parse(new TextDecoder().decode(read(path)));
const hash=bytes=>require('crypto').createHash('sha256').update(bytes).digest('hex');
const entries=['20260826','20260827','20260828','20260831','20260901','20260902','20260903','20260904','20260907','20260908'].map(d=>{
 const path=`outputs/decision/primary_d_receipt_${d}.json`,receipt=payload(path),o=receipt.outputs;
 return {signal_date:d,exec_date:receipt.exec_date,exit_date:receipt.exit_date,generation_mode:receipt.generation_mode,
 receipt:{path,sha256:hash(read(path))},snapshot:{path:o.json_path,sha256:o.json_sha256},top10_members_sha256:o.top10_members_sha256};
});
let catalog={p_fill_shadow_top2_forward:{primary_only_non_shadow_records:entries}};
let catalogBytes=new TextEncoder().encode(JSON.stringify(catalog));
let catalogIndex={schema_version:'dc20_three_rank_history_index_v2',data_alias:false,
 statistics_url:'outputs/decision/three_rank_history/statistics.json',statistics_sha256:hash(catalogBytes)};
const updateCatalog=()=>{catalogBytes=new TextEncoder().encode(JSON.stringify(catalog));catalogIndex.statistics_sha256=hash(catalogBytes)};
fetchPublishedBytes=async path=>{calls.push(path);
 if(path==='outputs/decision/three_rank_history/index.json')return new TextEncoder().encode(JSON.stringify(catalogIndex));
 if(path===catalogIndex.statistics_url)return catalogBytes;
 return read(path);
};
fetchPath=async (path,kind='json')=>{const bytes=await fetchPublishedBytes(path);return kind==='bytes'?bytes:kind==='text'?new TextDecoder().decode(bytes):JSON.parse(new TextDecoder().decode(bytes))};
fetchPagesOnlyPath=fetchPath;
publishedRevisionSha=async()=> '71e6827f89215fedc919e96742ffae228f599ee4';
publishedRevisionMetadata=async()=>({head_sha:'71e6827f89215fedc919e96742ffae228f599ee4'});
const realDailyRefresh=refreshCurrentPrimaryMixedDailyTop2;
refreshCurrentPrimaryMixedDailyTop2=async()=>{state.currentPrimaryMixedDailyTop2={status:'ready',index:await validatePrimaryMixedDailyTop2Index(payload('outputs/decision/executable_profit_research/daily_mixed_top2_index.json'))}};
""".replace("ROOT", json.dumps(str(ROOT)))
    result = subprocess.run([NODE, "-"], input=prelude + script + tail + "\n(async()=>{" + body + "})().catch(e=>{console.error(e);process.exit(1)});", text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("date,count", [("20260908",10),("20260907",10),("20260904",6),("20260828",10)])
def test_real_dated_p0_path_profit_and_rendering(date,count):
    result = run(r"""
await initialize();
const c=validatedThreeRankContract(state.currentThreeRank);
const before=JSON.stringify(c.rows);
renderCompactDashboard();
const out={d:c.signal_date,t:c.exec_date,t1:c.exit_date,count:c.rows.length,
 path:state.currentThreeRankPathEvidence.status,profit:state.currentExecutableProfitResearch.status,
 profitError:state.currentExecutableProfitResearch.message,ready:unifiedProfitView(c).ready,
 unchanged:before===JSON.stringify(c.rows),dates:els.brandDates.textContent,
 hidden:nodes.get('compactLedger').hidden&&nodes.get('compactStatistics').hidden,
 shadow:state.currentExecutableProfitResearch.shadow, calls,footer:els.footer.textContent};
console.log(JSON.stringify(out));
""", f"?d={date}")
    assert result["d"] == date and result["count"] == count
    assert result["path"] == "VALID" and result["profit"] == "ready", result
    assert result["ready"] and result["unchanged"] and result["hidden"] and result["shadow"] is None
    assert "report_index" not in " ".join(result["calls"])
    assert "primary_d_runtime_index.json" not in " ".join(result["calls"])
    assert "历史 D" in result["footer"]


def test_arrows_skip_weekend_and_keep_exact_d_without_legacy_index():
    result = run(r"""
state.dailyNavigation=await loadDailyNavigation();state.dailyLatestDate='20260908';state.dailyRequestedDate='20260907';
setNavigation();navigateDaily(1);const prev=assigned;navigateDaily(-1);const next=assigned;
state.dailyRequestedDate='20260826';setNavigation();const oldest=els.prevBtn.disabled;
state.dailyRequestedDate=null;state.currentThreeRank={signal_date:'20260908'};setNavigation();
console.log(JSON.stringify({prev,next,oldest,last:els.nextBtn.disabled,previous:els.prevBtn.title}));
""", "?d=20260907")
    assert "d=20260904" in result["prev"]
    assert "d=20260908" in result["next"] and result["oldest"] and result["last"]
    assert "2026-09-07" in result["previous"]


@pytest.mark.parametrize("date", ["20260906", "20260999", "", "../../secret"])
def test_missing_or_invalid_date_never_falls_back(date):
    result = run(r"""
let error='';try{await initialize()}catch(e){error=e.message;showError(e)}
console.log(JSON.stringify({error,contract:state.currentThreeRank||null,calls,fatal:state.compactFatalError}));
""", f"?d={date}")
    assert result["error"] and result["contract"] is None and result["fatal"]
    assert not any("three_rank_index.json" in p or "action_plan" in p or "report_index" in p for p in result["calls"])


@pytest.mark.parametrize("mutation", [
    "catalogIndex.statistics_sha256='0'.repeat(64)",
    "catalog.p_fill_shadow_top2_forward.primary_only_non_shadow_records.push(entries[0]);updateCatalog()",
    "entries[0].snapshot.path='outputs/decision/three_rank_top10_latest.json';updateCatalog()",
    "entries[0].receipt.sha256='broken';updateCatalog()",
])
def test_catalog_rejects_bad_hash_duplicate_or_alias(mutation):
    result = run(mutation + ";try{await loadDailyNavigation();console.log(false)}catch(e){console.log(true)}")
    assert result is True


@pytest.mark.parametrize("mutation", [
    "entry.receipt.sha256='0'.repeat(64)",
    "entry.exec_date='20260907'",
    "entry.snapshot.sha256='0'.repeat(64)",
    "entry.top10_members_sha256='0'.repeat(64)",
    "const original=fetchPublishedBytes;fetchPublishedBytes=async p=>p==='data/market/trade_cal_sse.csv'?new Uint8Array([1]):original(p)",
])
def test_historical_bundle_fail_closed(mutation):
    result = run("const entry=entries.at(-1);" + mutation + ";try{await loadPublishedDailyEntry(entry);console.log(false)}catch(e){console.log(true)}")
    assert result is True


@pytest.mark.parametrize("mutation", [
    "refreshCurrentPrimaryMixedDailyTop2=async()=>{state.currentPrimaryMixedDailyTop2={status:'invalid',message:'missing'}}",
    "const old=refreshCurrentPrimaryMixedDailyTop2;refreshCurrentPrimaryMixedDailyTop2=async()=>{await old();state.currentPrimaryMixedDailyTop2.index.entries.find(e=>e.signal_date==='20260907').projection_json_sha256='0'.repeat(64)}",
    "const old=refreshCurrentPrimaryMixedDailyTop2;refreshCurrentPrimaryMixedDailyTop2=async()=>{await old();state.currentPrimaryMixedDailyTop2.index.entries.find(e=>e.signal_date==='20260907').exec_date='20260909'}",
])
def test_missing_or_wrong_profit_never_removes_frozen_promotion(mutation):
    result = run(mutation + r""";
await initialize();const c=validatedThreeRankContract(state.currentThreeRank);
console.log(JSON.stringify({d:c.signal_date,count:c.rows.length,profit:state.currentExecutableProfitResearch.status,ready:unifiedProfitView(c).ready}));
""", "?d=20260907")
    assert result == {"d":"20260907","count":10,"profit":"invalid","ready":False}


def test_refresh_clears_selected_d_and_research_arrows_remain_separate():
    result = run(r"""
els.refreshBtn.listeners.click();const refresh=replaced;
location.search='?view=research';state.reports=[1,2,3];state.index=1;
let called=null;loadReport=async i=>{called=i};await navigateDaily(1);
setNavigation();console.log(JSON.stringify({refresh,called,previous:els.prevBtn.disabled,next:els.nextBtn.disabled}));
""", "?d=20260907")
    assert "d=" not in result["refresh"] and result["called"] == 2
    assert not result["previous"] and not result["next"]


def test_current_list_remains_navigable_when_catalog_temporarily_missing():
    result = run(r"""
state.currentThreeRank={signal_date:'20260908'};state.dailyLatestDate='20260908';
loadDailyNavigation=async()=>{throw Error('HTTP 503')};await refreshDailyNavigation();
console.log(JSON.stringify({d:state.currentThreeRank.signal_date,error:state.dailyNavigationError,disabled:els.prevBtn.disabled}));
""")
    assert result["d"] == "20260908" and "503" in result["error"] and result["disabled"]


def test_stale_catalog_does_not_redirect_the_right_arrow_to_a_later_latest():
    result = run(r"""
await initialize();navigateDaily(-1);
console.log(JSON.stringify({target:assigned,latest:state.dailyLatestDate}));
""", "?d=20260907")
    assert "d=20260908" in result["target"] and result["latest"] is None
