"""The compact view consumes the real inclusive-D window, never D28 totals."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
WINDOW_PATH = "outputs/decision/compact_statistics_window.json"
pytestmark = pytest.mark.skipif(not NODE, reason="Node required")


@pytest.fixture(scope="module")
def window_data():
    from scripts.build_compact_statistics_window import build_window

    base = ROOT / "outputs/decision/executable_profit_research"
    index = json.loads((base / "shadow_index.json").read_text())
    now = datetime.now(timezone.utc)
    window = build_window(ROOT, signal_date=index["latest_signal_date"], now=now)
    assert window["profit"]["status"] == "READY", window["profit"]
    assert window["promotion"]["status"] == "READY", window["promotion"]
    raw = json.dumps(window, ensure_ascii=False, indent=2).encode()
    daily_raw = (base / "daily_mixed_top2_index.json").read_bytes()
    daily = json.loads(daily_raw)
    revision = dict(
        head_sha="a" * 40, compact_statistics_window_url=WINDOW_PATH,
        primary_d_signal_date=window["report_signal_date"],
        compact_statistics_window_sha256=hashlib.sha256(raw).hexdigest(),
        compact_statistics_window_start_signal_date="20260910",
        primary_mixed_daily_top2_index_url="outputs/decision/executable_profit_research/daily_mixed_top2_index.json",
        primary_mixed_daily_top2_index_sha256=hashlib.sha256(daily_raw).hexdigest(),
        primary_mixed_daily_top2_latest_signal_date=daily["latest_signal_date"],
        primary_mixed_daily_top2_recorded_days=daily["recorded_days"],
        primary_mixed_daily_top2_recorded_slots=daily["recorded_slots"],
    )
    return dict(window=window, raw=raw.decode(), revision=revision, now=now.isoformat())


def run(window_data, body):
    script = re.search(r"<script>(.*?)</script>", (ROOT / "decision.html").read_text(), re.S).group(1)
    script = script.replace("initialize(false).catch(showError);", "")
    prelude = r"""
const fs=require('fs'),crypto=require('crypto').webcrypto;
const nodes=new Map();
const element=()=>({innerHTML:'',textContent:'',hidden:false,open:false,disabled:false,title:'',
 listeners:{},setAttribute(k,v){this[k]=v},removeAttribute(){},addEventListener(k,v){this.listeners[k]=v},querySelector(){return element()},
 querySelectorAll(){return []},classList:{toggle(){},add(){},remove(){}}});
const document={getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)},querySelectorAll:()=>[],addEventListener(){}};
let assigned=null,replaced=null;
const location={search:'',protocol:'https:',href:'https://njedu2023-prog.github.io/DC20/',assign(url){assigned=url},replace(url){replaced=url}};
const window={location,addEventListener(){}};
"""
    tail = "\nconst root=" + json.dumps(str(ROOT)) + ",input=" + json.dumps(window_data, ensure_ascii=False) + ";\n"
    tail += r"""
Date.now=()=>Date.parse(input.now);
const calls=[],revision=input.revision,windowPayload=input.window;
let windowBytes=new TextEncoder().encode(input.raw),readHook=async()=>{};
fetchPagesOnlyPath=async(path,kind='json')=>{
  calls.push(path);await readHook(path);
  const bytes=path===COMPACT_STATISTICS_WINDOW_PATH?windowBytes:new Uint8Array(fs.readFileSync(root+'/'+path));
  return kind==='bytes'?bytes:kind==='text'?new TextDecoder().decode(bytes):JSON.parse(new TextDecoder().decode(bytes));
};
fetchPublishedBytes=path=>fetchPagesOnlyPath(path,'bytes');
fetchPath=fetchPagesOnlyPath;
publishedRevisionMetadata=async()=>revision;
const rehashWindow=async(snapshot=true)=>{
  if(snapshot){const unsigned={...windowPayload};delete unsigned.snapshot_sha256;
    windowPayload.snapshot_sha256=await sha256Hex(new TextEncoder().encode(canonicalJson(unsigned)));}
  windowBytes=new TextEncoder().encode(JSON.stringify(windowPayload));
  revision.compact_statistics_window_sha256=await sha256Hex(windowBytes);
};
const loadSources=async()=>{
  state.currentThreeRank={signal_date:windowPayload.report_signal_date};
  state.compactLoadSequence=1;
  await refreshPublicObservationStatistics();
  await refreshCurrentPrimaryMixedDailyTop2();
  const index=await fetchPagesOnlyPath(EXECUTABLE_PROFIT_RESEARCH_ROOT+'/index.json');
  const projection=await fetchPagesOnlyPath(index.latest_projection_json_url);
  const shadow=await loadPrimaryProfitShadowSidecar(projection,index);
  state.currentExecutableProfitResearch={status:'ready',kind:'primary_core',index,projection,shadow};
};
const result=()=>{
  renderCompactDashboard();
  return {load:state.compactStatisticsWindowLoad,ready:!!compactStatisticsWindowView(),
    profit:els.compactLedgerContent.innerHTML,header:els.compactLedgerState.textContent,
    promotion:els.compactStatisticsContent.innerHTML,calls};
};
"""
    tail += "\n(async()=>{await loadSources();" + body + "})().catch(e=>{console.error(e);process.exit(1)});"
    process = subprocess.run([NODE, "-"], input=prelude + script + tail, text=True, capture_output=True)
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


def test_real_window_excludes_0909_and_includes_0910_in_actual_counts(window_data):
    window = window_data["window"]
    assert window["start_signal_date"] == "20260910"
    selections = [source["path"] for source in window["source_files"] if "/selections/shadow_" in source["path"] and source["path"].endswith(".json")]
    assert "data/decision_executable_profit/forward/selections/shadow_20260910.json" in selections
    assert "data/decision_executable_profit/forward/selections/shadow_20260909.json" not in selections
    summary = json.loads((ROOT / "outputs/decision/primary_observation/summary.json").read_text())
    assert any(day["signal_date"] == "20260909" for day in summary["daily_summaries"])
    for rank in window["promotion"]["ranks"]:
        dates = [day["signal_date"] for day in summary["daily_summaries"] if day["rows"] >= rank["rank"] and "20260910" <= day["signal_date"] <= window["promotion"]["as_of_date"]]
        assert "20260910" in dates
        assert rank["count"] == len(dates)
        assert rank["count"] < sum(day["rows"] >= rank["rank"] for day in summary["daily_summaries"])
    original = copy.deepcopy(window_data)
    rendered = run(window_data, "await refreshCompactStatisticsWindow();console.log(JSON.stringify(result()));")
    assert rendered["ready"] and rendered["load"]["status"] == "ready"
    assert rendered["profit"].count('class="rank-mark rank-profit"') == 2
    assert f"{window['profit']['recorded_days']}日 / {window['profit']['recorded_slots']}席" in rendered["header"]
    assert "D 2026-09-10起" in rendered["header"] and "D 2026-09-10起" in rendered["promotion"]
    assert "2026-08-28" not in rendered["header"] + rendered["promotion"]
    for rank in window["promotion"]["ranks"]:
        expected = "暂无已验证样本" if not rank["verified"] else f"{rank['hits']} / {rank['verified']} 成功 / 已验证"
        assert expected in rendered["promotion"]
    assert WINDOW_PATH in rendered["calls"]
    assert window_data == original


@pytest.mark.parametrize("mutation,error", [
    ("revision.compact_statistics_window_sha256='0'.repeat(64)", "字节SHA256不一致"),
    ("revision.compact_statistics_window_url='outputs/decision/other.json'", "Pages精确revision"),
    ("revision.compact_statistics_window_start_signal_date='20260828'", "Pages精确revision"),
    ("windowPayload.start_signal_date='20260909';await rehashWindow()", "起点或当前D"),
    ("windowPayload.report_signal_date='20260910';state.currentThreeRank.signal_date='20260911';await rehashWindow()", "起点或当前D"),
    ("windowPayload.profit.as_of_date='20990101';await rehashWindow()", "实际收盘时间"),
    ("windowPayload.promotion.as_of_date='20990101';await rehashWindow()", "实际收盘时间"),
    ("windowPayload.config_sha256='0'.repeat(64);await rehashWindow()", "配置漂移"),
    ("windowPayload.snapshot_sha256='broken';await rehashWindow(false)", "来源指纹缺失"),
    ("windowPayload.profit.source_statistics.sha256='0'.repeat(64);await rehashWindow()", "当前Shadow统计来源"),
    ("windowPayload.profit.source_statistics.path='data/decision_executable_profit/forward/statistics/summary.json';await rehashWindow()", "当前Shadow统计来源"),
    ("windowPayload.promotion.source_summary.sha256='0'.repeat(64);await rehashWindow()", "summary及逐行SHA"),
    ("windowPayload.promotion.rows_sha256='0'.repeat(64);await rehashWindow()", "summary及逐行SHA"),
    ("windowPayload.profit.recorded_days+=1;await rehashWindow()", "按D起点筛选"),
    ("windowPayload.promotion.ranks[0].count+=1;await rehashWindow()", "按D起点筛选样本"),
    ("windowPayload.promotion.ranks[0].hits=999;await rehashWindow()", "分母或成功率"),
    ("windowPayload.source_files.push(windowPayload.source_files[0]);await rehashWindow()", "来源重复"),
    ("windowPayload.source_files[0].path='../escape.json';await rehashWindow()", "路径或SHA"),
    ("readHook=async path=>{if(path===COMPACT_STATISTICS_WINDOW_PATH)throw Error('HTTP 404 window')}", "HTTP 404"),
])
def test_window_binding_scope_and_counts_fail_closed_without_old_totals(window_data, mutation, error):
    rendered = run(window_data, mutation + ";await refreshCompactStatisticsWindow();console.log(JSON.stringify(result()));")
    assert rendered["ready"] is False and rendered["load"]["status"] == "invalid"
    assert error in rendered["load"]["message"]
    assert rendered["profit"] == ""
    assert rendered["promotion"].count('class="success-rate">—') == 3
    assert 'class="success-rate">0.00%' not in rendered["promotion"]


@pytest.mark.parametrize("section", ["profit", "promotion"])
def test_unavailable_section_has_no_fake_zero_or_other_section_fallback(window_data, section):
    rendered = run(window_data, f"windowPayload.{section}={{status:'UNAVAILABLE',reason:'missing source'}};await rehashWindow();await refreshCompactStatisticsWindow();console.log(JSON.stringify(result()));")
    assert rendered["ready"]
    if section == "profit":
        assert rendered["profit"] == "" and 'class="success-rate">—' not in rendered["promotion"]
    else:
        assert "profit-summary-table" in rendered["profit"]
        assert rendered["promotion"].count('class="success-rate">—') == 3


@pytest.mark.parametrize("stale", [
    "state.compactLoadSequence+=1", "state.compactStatisticsWindowSequence+=1", "state.compactFatalError=true",
])
def test_old_async_window_cannot_inject_after_new_load_or_fatal_error(window_data, stale):
    rendered = run(window_data, r"""
let release,entered;const gate=new Promise(r=>release=r),waiting=new Promise(r=>entered=r);
readHook=async path=>{if(path===COMPACT_STATISTICS_WINDOW_PATH){entered();await gate}};
const pending=refreshCompactStatisticsWindow();await waiting;
""" + stale + r""";release();await pending;
console.log(JSON.stringify({injected:state.currentCompactStatisticsWindow!==null,visible:!!compactStatisticsWindowView()}));
""")
    assert rendered == {"injected": False, "visible": False}


@pytest.mark.parametrize("navigation", [
    "state.dailyRequestedDate='20260908';location.search='?d=20260908'",
    "state.index=1", "state.publicObservationLoadSequence+=1",
])
def test_latest_window_stays_independent_of_selected_historical_state(window_data, navigation):
    rendered = run(window_data, r"""
let release,entered;const gate=new Promise(r=>release=r),waiting=new Promise(r=>entered=r);
readHook=async path=>{if(path===COMPACT_STATISTICS_WINDOW_PATH){entered();await gate}};
const pending=refreshCompactStatisticsWindow();await waiting;
state.currentThreeRank={signal_date:'20260908',rows:[{ts_code:'historical'}]};
state.currentExecutableProfitResearch={status:'ready',projection:{signal_date:'20260908'}};
state.currentThreeRankTTruth={signal_date:'20260908',rows:[{validation_status:'historical'}]};
state.currentThreeRankObservationTruth={signal_date:'20260908',rows:[{net_return_after_cost:-0.01}]};
const selected=()=>JSON.stringify([state.currentThreeRank,state.currentExecutableProfitResearch,state.currentThreeRankTTruth,state.currentThreeRankObservationTruth,state.currentPublicObservationStatistics]);
const before=selected();
""" + navigation + r""";
release();await pending;const out=result();
console.log(JSON.stringify({...out,unchanged:before===selected(),selectedD:state.currentThreeRank.signal_date,
 latestD:compactStatisticsWindowView().report_signal_date,
 hidden:nodes.get('compactLedger').hidden||nodes.get('compactStatistics').hidden}));
""")
    assert rendered["ready"] and not rendered["hidden"] and rendered["unchanged"]
    assert rendered["selectedD"] == "20260908" and rendered["latestD"] == window_data["window"]["report_signal_date"]
    assert "最新累计" in rendered["header"] and "profit-summary-table" in rendered["profit"]


def test_old_summary_request_cannot_replace_new_summary_bytes_or_sha(window_data):
    rendered = run(window_data, r"""
let release,entered;const gate=new Promise(r=>release=r),waiting=new Promise(r=>entered=r);
let reads=0;readHook=async path=>{if(path===PUBLIC_OBSERVATION_STATISTICS_PATH&&++reads===1){entered();await gate}};
const pending=refreshPublicObservationStatistics();await waiting;
await refreshPublicObservationStatistics();const newer=state.currentPublicObservationStatistics,sha=state.currentPublicObservationStatisticsSha256;
release();await pending;
console.log(JSON.stringify({same:state.currentPublicObservationStatistics===newer,sha:state.currentPublicObservationStatisticsSha256===sha,status:state.publicObservationLoad.status}));
""")
    assert rendered == {"same": True, "sha": True, "status": "ready"}


def test_slow_window_never_blocks_the_current_d_ranking(window_data):
    rendered = run(window_data, r"""
let release,windowPending;const gate=new Promise(r=>release=r);
readHook=async path=>{if(path===COMPACT_STATISTICS_WINDOW_PATH)await gate};
const refresh=refreshCompactStatisticsWindow;
refreshCompactStatisticsWindow=ready=>(windowPending=refresh(ready));
await initialize();
const before={table:els.stageContent.innerHTML.includes('data-three-rank-body'),
 loaded:state.compactStatisticsWindowLoad.status,window:!!compactStatisticsWindowView()};
release();await windowPending;
console.log(JSON.stringify({before,after:result()}));
""")
    assert rendered["before"] == {"table": True, "loaded": "loading", "window": False}
    assert rendered["after"]["ready"]


@pytest.mark.parametrize("value", [1e-6, -1e-7, 1e-12, 1 / 3])
def test_python_encoded_window_preserves_numeric_values_without_js_snapshot_rehash(window_data, value):
    # Exercise byte transport, not the producer's independently tested math.
    # Python's 1e-07 / JS's 1e-7 (and 1e-06 / 0.000001) must not hide valid bytes.
    variant = copy.deepcopy(window_data)
    for cohort in variant["window"]["profit"]["cohorts"].values():
        cohort["equal_weight_cumulative_return"] = value
    unsigned = {key: item for key, item in variant["window"].items() if key != "snapshot_sha256"}
    variant["window"]["snapshot_sha256"] = hashlib.sha256(json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    variant["raw"] = json.dumps(variant["window"], ensure_ascii=False, indent=2)
    variant["revision"]["compact_statistics_window_sha256"] = hashlib.sha256(variant["raw"].encode()).hexdigest()
    rendered = run(variant, r"""
await refreshCompactStatisticsWindow();const view=compactStatisticsWindowView();
console.log(JSON.stringify({ready:!!view,load:state.compactStatisticsWindowLoad,
 values:view?Object.values(view.profit.cohorts).map(c=>c.equal_weight_cumulative_return):[],
 types:view?Object.values(view.profit.cohorts).map(c=>typeof c.equal_weight_cumulative_return):[]}));
""")
    assert rendered["ready"], rendered
    assert rendered["values"] == [value] * 5
    assert rendered["types"] == ["number"] * 5
    rejected = run(variant, "windowBytes=new TextEncoder().encode(input.raw+' ');await refreshCompactStatisticsWindow();console.log(JSON.stringify(result()));")
    assert not rejected["ready"] and "字节SHA256不一致" in rejected["load"]["message"]


@pytest.mark.parametrize("date", ["20260908", "20260907", "20260904"])
def test_direct_historical_page_and_same_url_refresh_keep_latest_cumulative(window_data, date):
    rendered = run(window_data, "location.search='?d=" + date + "';location.href='https://njedu2023-prog.github.io/DC20/'+location.search;" + r"""
let windowPending;const refresh=refreshCompactStatisticsWindow;
refreshCompactStatisticsWindow=()=>(windowPending=refresh());
await initialize();await windowPending;
const first=result(),selected=validatedThreeRankContract(state.currentThreeRank),rows=JSON.stringify(selected.rows);
const firstTruth=JSON.stringify([state.currentThreeRankTTruth,state.currentThreeRankObservationTruth]);
await initialize(true);await windowPending;const second=result();
console.log(JSON.stringify({first,second,d:validatedThreeRankContract(state.currentThreeRank).signal_date,
 unchanged:JSON.stringify(validatedThreeRankContract(state.currentThreeRank).rows)===rows,
 sameTruth:JSON.stringify([state.currentThreeRankTTruth,state.currentThreeRankObservationTruth])===firstTruth,
 latestD:compactStatisticsWindowView().report_signal_date,shadow:state.currentExecutableProfitResearch.shadow,
 selectedProfitD:state.currentExecutableProfitResearch.projection.signal_date,
 hidden:nodes.get('compactLedger').hidden||nodes.get('compactStatistics').hidden}));
""")
    assert rendered["first"]["ready"] and rendered["second"]["ready"], rendered
    assert rendered["d"] == date and rendered["selectedProfitD"] == date
    assert rendered["latestD"] == window_data["window"]["report_signal_date"]
    assert rendered["unchanged"] and rendered["sameTruth"] and not rendered["hidden"]
    assert rendered["shadow"] is None
    assert rendered["first"]["profit"] == rendered["second"]["profit"]
    assert rendered["first"]["promotion"] == rendered["second"]["promotion"]


def test_historical_previous_next_arrows_keep_the_same_latest_cumulative(window_data):
    rendered = run(window_data, r"""
let windowPending;const refresh=refreshCompactStatisticsWindow;
refreshCompactStatisticsWindow=()=>(windowPending=refresh());
const visit=async url=>{location.href=url;location.search=new URL(url).search;
 await initialize();await windowPending;return {...result(),selectedD:validatedThreeRankContract(state.currentThreeRank).signal_date};};
const first=await visit('https://njedu2023-prog.github.io/DC20/?d=20260907');
navigateDaily(1);const previousUrl=assigned,previous=await visit(previousUrl);
navigateDaily(-1);const nextUrl=assigned,next=await visit(nextUrl);
console.log(JSON.stringify({first,previous,next,previousUrl,nextUrl}));
""")
    assert "d=20260904" in rendered["previousUrl"] and "d=20260907" in rendered["nextUrl"]
    assert [rendered[name]["selectedD"] for name in ("first", "previous", "next")] == ["20260907", "20260904", "20260907"]
    for name in ("first", "previous", "next"):
        assert rendered[name]["ready"]
        assert "最新累计" in rendered[name]["header"]
        assert rendered[name]["profit"] == rendered["first"]["profit"]
        assert rendered[name]["promotion"] == rendered["first"]["promotion"]


def test_research_view_keeps_compact_statistics_hidden(window_data):
    rendered = run(window_data, r"""
await refreshCompactStatisticsWindow();location.search='?view=research';state.index=1;renderCompactDashboard();
console.log(JSON.stringify({ledger:nodes.get('compactLedger').hidden,promotion:nodes.get('compactStatistics').hidden}));
""")
    assert rendered == {"ledger": True, "promotion": True}
