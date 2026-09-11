"""Loading speed must not weaken the frozen P0/P1 or truth boundary."""
import json
import re
import runpy
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
run = runpy.run_path(str(ROOT / "tests/test_primary_d_navigation.py"))["run"]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="Node required")


def readers(body):
    html = (ROOT / "decision.html").read_text()
    names = ["localUrl", "readPageResourceBytes", "fetchPath", "fetchPagesOnlyPath",
             "publishedRevisionMetadata", "publishedRevisionSha", "fetchPublishedBytes",
             "fetchLegacyProfitRelativePath"]
    functions = "\n".join(re.search(rf"^    (?:async )?function {name}\(.*?^    }}", html, re.M | re.S).group() for name in names)
    prelude = r"""
const state={},REPO={owner:'njedu2023-prog',name:'DC20',branch:'main'};
const RAW_BASE='https://raw.githubusercontent.com/njedu2023-prog/DC20/main/';
const location={protocol:'https:',origin:'https://njedu2023-prog.github.io',href:'https://njedu2023-prog.github.io/DC20/'};
const calls=[],sha='a'.repeat(40),revision={repository:'njedu2023-prog/DC20',branch:'main',head_sha:sha};
let reply=async url=>new Response(JSON.stringify(url.includes('revision.json')?revision:{value:1}));
globalThis.fetch=async(url,options)=>{calls.push({url,options});return reply(url)};
"""
    result = subprocess.run([NODE, "-"], input=prelude + functions + "\n(async()=>{" + body + "})().catch(e=>{console.error(e);process.exit(1)});", capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_same_pages_url_shares_bytes_across_readers_without_mutable_aliases():
    result = readers(r"""
const all=await Promise.all([fetchPath('data.json'),fetchPath('data.json','text'),fetchPublishedBytes('data.json'),fetchPagesOnlyPath('data.json'),fetchLegacyProfitRelativePath('data.json')]);
all[0].value=99;all[2][0]=0;
const again=await fetchPath('data.json');
console.log(JSON.stringify({count:calls.length,value:again.value,strict:all[3].value,legacy:all[4].value,text:all[1],cache:calls[0].options.cache}));
""")
    assert result == {"count": 1, "value": 1, "strict": 1, "legacy": 1, "text": '{"value":1}', "cache": "no-store"}


@pytest.mark.parametrize("failure", ["throw new Error('offline')", "return new Response('',{status:503})"])
def test_failed_shared_request_can_be_retried(failure):
    result = readers("reply=async()=>{" + failure + r"""};
const first=await Promise.allSettled([readPageResourceBytes('https://example.test/a'),readPageResourceBytes('https://example.test/a')]);
reply=async()=>new Response('ok');
const value=new TextDecoder().decode(await readPageResourceBytes('https://example.test/a'));
console.log(JSON.stringify({count:calls.length,rejected:first.every(x=>x.status==='rejected'),value}));
""")
    assert result == {"count": 2, "rejected": True, "value": "ok"}


def test_raw_fallback_cannot_be_reused_by_pages_only_or_legacy_reader():
    result = readers(r"""
reply=async url=>url.includes('revision.json')?new Response(JSON.stringify(revision)):url.includes('raw.githubusercontent')?new Response('{"value":7}'):new Response('',{status:404});
const fallback=await fetchPath('missing.json');
const strict=await Promise.allSettled([fetchPagesOnlyPath('missing.json'),fetchLegacyProfitRelativePath('missing.json')]);
console.log(JSON.stringify({value:fallback.value,strict:strict.every(x=>x.status==='rejected'),raw:calls.filter(x=>x.url.includes('raw.githubusercontent')).map(x=>x.url)}));
""")
    assert result["value"] == 7 and result["strict"]
    assert len(result["raw"]) == 1 and "/" + "a" * 40 + "/missing.json" in result["raw"][0]


def test_concurrent_revision_read_has_one_identity_and_invalid_identity_fails():
    result = readers(r"""
const results=await Promise.all([publishedRevisionMetadata(),publishedRevisionMetadata(),publishedRevisionSha()]);
const count=calls.length;
state.resourceReads=new Map();state.publishedRevision=null;reply=async()=>new Response(JSON.stringify({...revision,repository:'wrong/repo'}));
let invalid=false;try{await publishedRevisionMetadata()}catch(e){invalid=true}
console.log(JSON.stringify({count,identical:results[0].head_sha===results[1].head_sha&&results[2]===sha,invalid}));
""")
    assert result == {"count": 1, "identical": True, "invalid": True}


def test_new_page_cycle_reads_new_bytes():
    result = readers(r"""
const before=await fetchPath('index.json');state.resourceReads=new Map();reply=async()=>new Response('{"value":2}');
const after=await fetchPath('index.json');console.log(JSON.stringify({count:calls.length,before:before.value,after:after.value}));
""")
    assert result == {"count": 2, "before": 1, "after": 2}


def test_home_does_not_request_hidden_report_or_legacy_profit():
    result = run(r"""
let legacy=0;refreshCurrentLegacyProfitRelativeResearch=async()=>{legacy++};
await initialize();const c=validatedThreeRankContract(state.currentThreeRank);
console.log(JSON.stringify({legacy,calls,d:c.signal_date,count:c.rows.length,profit:unifiedProfitView(c).ready,path:state.currentThreeRankPathEvidence.status}));
""")
    assert result["legacy"] == 0 and result["count"] > 0 and result["profit"] and result["path"] == "VALID"
    assert not any("report_index" in path or "research_context" in path or "decision_report_" in path or "eval_" in path for path in result["calls"])


def test_explicit_research_route_keeps_old_report_loading():
    result = run(r"""
let legacy=0;refreshCurrentLegacyProfitRelativeResearch=async()=>{legacy++};
await initialize();console.log(JSON.stringify({legacy,report:calls.includes('outputs/decision/report_index.json')}));
""", "?view=research")
    assert result == {"legacy": 1, "report": True}


@pytest.mark.parametrize("delayed", ["summary", "profit"])
def test_slow_auxiliary_source_does_not_block_validated_primary_list(delayed):
    result = run(r"""
let release,painted,done=false;
const gate=new Promise(r=>release=r),firstPaint=new Promise(r=>painted=r);
const oldRead=fetchPublishedBytes,oldRender=renderPrimaryCoreOnly;
fetchPublishedBytes=async p=>{if(p===DELAY_PATH)await gate;return oldRead(p)};
renderPrimaryCoreOnly=error=>{oldRender(error);painted()};
const pending=initialize().then(()=>done=true);
await Promise.race([firstPaint,new Promise((_,reject)=>setTimeout(()=>reject(Error('primary list blocked')),2000))]);
const early={done,valid:!!validatedThreeRankContract(state.currentThreeRank),path:state.currentThreeRankPathEvidence.status,
 table:els.stageContent.innerHTML.includes('data-three-rank-body'),profitReady:unifiedProfitView(validatedThreeRankContract(state.currentThreeRank)).ready};
release();await pending;
console.log(JSON.stringify({early,done,ready:unifiedProfitView(validatedThreeRankContract(state.currentThreeRank)).ready}));
""".replace("DELAY_PATH", json.dumps("outputs/decision/primary_observation/summary.json" if delayed == "summary" else "outputs/decision/executable_profit_research/index.json")))
    assert result["early"]["valid"] and result["early"]["table"] and result["early"]["path"] == "VALID"
    assert not result["early"]["done"] and result["done"] and result["ready"]
    if delayed == "profit":
        assert not result["early"]["profitReady"]


def test_truth_not_published_before_cross_source_validation():
    result = run(r"""
const current=await loadCurrentThreeRank();let release,entered;
const waiting=new Promise(r=>entered=r),gate=new Promise(r=>release=r);
loadCurrentThreeRankTTruth=async()=>({status:'READY',rows:[{ts_code:'a',continuation_limit_up_hit:1}]});
loadCurrentThreeRankObservationTruth=async()=>{entered();await gate;return {status:'READY',rows:[{ts_code:'a',continuation_limit_up_hit:0}]}};
const pending=refreshCurrentThreeRankTTruth(current);await waiting;
const premature=state.currentThreeRankTTruth!==null||state.currentThreeRankObservationTruth!==null;
release();await pending;
console.log(JSON.stringify({premature,t:state.currentThreeRankTTruth.status,t1:state.currentThreeRankObservationTruth.status}));
""")
    assert result == {"premature": False, "t": "INVALID_T_TRUTH", "t1": "OBSERVATION_INVALID"}


def test_refresh_clears_previous_cycle_caches_and_truth_before_loading():
    result = run(r"""
state.resourceReads=new Map([['stale',1]]);state.publishedRevision={head_sha:'old'};
state.currentThreeRankTTruth={status:'READY'};state.currentExecutableProfitResearch={status:'ready'};
let clean=false;const old=loadCurrentThreeRank;
loadCurrentThreeRank=async()=>{clean=state.resourceReads.size===0&&state.publishedRevision===null&&state.currentThreeRankTTruth===null&&state.currentExecutableProfitResearch===null;return old()};
await initialize(true);console.log(JSON.stringify({clean}));
""")
    assert result["clean"]


def test_loading_repaint_keeps_user_sort_without_mutating_frozen_ranks():
    result = run(r"""
await initialize();const c=validatedThreeRankContract(state.currentThreeRank),before=JSON.stringify(c.rows);
state.currentRankSort={signal_date:c.signal_date,bundle_sha256:c.bundle_sha256,field:'mixed_profit_rank'};
renderPrimaryCoreOnly(Error('repaint'));
console.log(JSON.stringify({field:state.currentRankSort.field,unchanged:before===JSON.stringify(c.rows)}));
""")
    assert result == {"field": "mixed_profit_rank", "unchanged": True}
