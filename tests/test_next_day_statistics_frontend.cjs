"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {webcrypto, createHash} = require("node:crypto");
const html = fs.readFileSync(path.join(__dirname,"..","decision.html"),"utf8");
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1].replace("initialize(false).catch(showError);", "");
const sha = bytes => createHash("sha256").update(bytes).digest("hex");
const copy = v => JSON.parse(JSON.stringify(v));

function cohort(rank, profit=false) {
  return {rank,count:4,valid:2,up:1,pending:1,missing:1,upRate:.5,mean:.03,median:.03,
    ...(profit?{limitVerified:2,limitHits:1,limitRate:.5}:{})};
}

function fixture({mutate,fetcher,timers}={}) {
  const bindings={window_sha256:"1".repeat(64),candidate_index_sha256:"2".repeat(64),candidate_activation_sha256:"3".repeat(64),primary_rows_sha256:"4".repeat(64)};
  const payload={schema_version:"dc20_next_day_statistics_v1",report_signal_date:"20261106",as_of_date:"20261106",
    promotion_start_signal_date:"20260910",profit_start_signal_date:"20260914",engine_sha256:"5".repeat(64),bindings:{...bindings},
    promotion:{status:"READY",as_of_date:"20261106",cohorts:[1,2,3,"all"].map(r=>cohort(r))},
    profit:{status:"READY",as_of_date:"20261106",cohorts:[1,2].map(r=>cohort(r,true))}};
  mutate?.(payload);
  const bytes=Buffer.from(JSON.stringify(payload));
  const revision={head_sha:"a".repeat(40),primary_d_signal_date:"20261106",compact_statistics_window_sha256:bindings.window_sha256,
    candidate_profit_index_sha256:bindings.candidate_index_sha256,candidate_profit_activation_sha256:bindings.candidate_activation_sha256,
    next_day_statistics_url:"outputs/decision/next_day_statistics.json",next_day_statistics_sha256:sha(bytes)};
  const loaded={reportSignalDate:"20261106",payload:{report_signal_date:"20261106",source_files:[{path:"outputs/decision/primary_observation/rows.csv",sha256:bindings.primary_rows_sha256}]}};
  const active={status:"ready",indexSha256:bindings.candidate_index_sha256,revision};
  const calls=[],nodes=new Map();
  const element=()=>({innerHTML:"",textContent:"",hidden:false,open:false,disabled:false,title:"",setAttribute(){},removeAttribute(){},addEventListener(){},querySelector(){return element()},querySelectorAll(){return []},classList:{toggle(){},add(){},remove(){}}});
  const node=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)};
  const location={search:"",protocol:"https:",href:"https://njedu2023-prog.github.io/DC20/",origin:"https://njedu2023-prog.github.io"};
  const context={console,crypto:webcrypto,TextEncoder,TextDecoder,Uint8Array,URL,URLSearchParams,AbortController,
    setTimeout:timers?.setTimeout||setTimeout,clearTimeout:timers?.clearTimeout||clearTimeout,
    location,window:{location,addEventListener(){}},document:{getElementById:node,querySelectorAll:()=>[],addEventListener(){}},
    fetch:async(url,options)=>{calls.push({url,options});return fetcher?fetcher(url,options,bytes,calls.length):{ok:true,arrayBuffer:async()=>bytes}},
    fixtureLoaded:loaded,fixtureActive:active,fixtureRevision:revision};
  vm.createContext(context);vm.runInContext(source,context);
  vm.runInContext("state.currentCompactStatisticsWindow=fixtureLoaded;state.candidateProfitActivation=fixtureActive;state.publishedRevision=fixtureRevision;state.compactStatisticsWindowLoad={status:'ready'};renderCompactDashboard=()=>{globalThis.dashboardRenders=(globalThis.dashboardRenders||0)+1};renderProfitNextDayStatistics=()=>{globalThis.profitRenders=(globalThis.profitRenders||0)+1};",context);
  return {context,loaded,active,revision,payload,bytes,calls,node,run:code=>vm.runInContext(code,context)};
}

test("both panels share exactly one small SHA-bound request, without historical market reads",async()=>{
  const f=fixture();
  await f.run("Promise.all([refreshNextDayMarketStatistics(fixtureLoaded,()=>true),refreshProfitNextDayStatistics(fixtureLoaded,fixtureActive)])");
  assert.equal(f.calls.length,1);assert.match(f.calls[0].url,/next_day_statistics\.json\?sha=/);
  assert.equal(f.loaded.marketPerformance.status,"ready");assert.equal(f.loaded.profitNextDay.status,"ready");
  assert.equal(f.context.dashboardRenders,1);assert.equal(f.context.profitRenders,1);
  assert.equal(f.run("state.compactStatisticsWindowLoad.status"),"ready");
  assert.ok(!/fetchPagesOnlyPath|loadCandidateProfitDay|three_rank_top10|\/daily\.csv/.test(html.slice(html.indexOf("    async function loadProfitNextDayStatistics("),html.indexOf("    async function refreshProfitNextDayStatistics("))));
});

test("wrong bytes are rejected without relaxed checks or automatic integrity retry",async()=>{
  const f=fixture({fetcher:async()=>({ok:true,arrayBuffer:async()=>Buffer.from('{}')})});
  await f.run("refreshNextDayMarketStatistics(fixtureLoaded,()=>true)");
  assert.equal(f.calls.length,1);assert.equal(f.loaded.marketPerformance.status,"unavailable");assert.match(f.loaded.marketPerformance.reason,/SHA/);
  assert.equal(f.loaded.marketPerformance.cohorts,undefined);
});

test("manual retry revalidates a temporarily stale response instead of reusing bad cached bytes",async()=>{
  const f=fixture({fetcher:async(_url,_options,bytes,n)=>({ok:true,arrayBuffer:async()=>n===1?Buffer.from('{}'):bytes})});
  await f.run("refreshNextDayMarketStatistics(fixtureLoaded,()=>true)");
  assert.equal(f.loaded.marketPerformance.status,"unavailable");assert.equal(f.calls.length,1);
  f.run("retryNextDayStatistics()");
  for(let i=0;i<50 && f.loaded.profitNextDay?.status!=="ready";i++) await new Promise(r=>setTimeout(r,2));
  assert.equal(f.loaded.marketPerformance.status,"ready");assert.equal(f.loaded.profitNextDay.status,"ready");
  assert.equal(f.calls.length,2);assert.ok(f.calls.every(call=>call.options.cache==="no-cache"));
});

test("old D and mismatched source bindings cannot show a stale result as current",async()=>{
  for(const mutate of [p=>p.report_signal_date="20261105",p=>p.as_of_date="20261105",p=>p.bindings.window_sha256="9".repeat(64),p=>p.bindings.candidate_index_sha256="9".repeat(64),p=>p.bindings.candidate_activation_sha256="9".repeat(64),p=>p.bindings.primary_rows_sha256="9".repeat(64)]) {
    const f=fixture({mutate});await f.run("refreshNextDayMarketStatistics(fixtureLoaded,()=>true)");
    assert.equal(f.loaded.marketPerformance.status,"unavailable");assert.equal(f.loaded.marketPerformance.cohorts,undefined);
  }
});

test("a malformed section is isolated; valid other panel and main dashboard survive",async()=>{
  const f=fixture({mutate:p=>p.promotion.cohorts[0].up=3});
  await f.run("Promise.all([refreshNextDayMarketStatistics(fixtureLoaded,()=>true),refreshProfitNextDayStatistics(fixtureLoaded,fixtureActive)])");
  assert.equal(f.loaded.marketPerformance.status,"unavailable");assert.equal(f.loaded.profitNextDay.status,"ready");
  assert.equal(f.run("!!state.compactFatalError"),false);
  const reverse=fixture({mutate:p=>p.profit.cohorts[0].limitRate=Infinity});
  await reverse.run("Promise.all([refreshNextDayMarketStatistics(fixtureLoaded,()=>true),refreshProfitNextDayStatistics(fixtureLoaded,fixtureActive)])");
  assert.equal(reverse.loaded.marketPerformance.status,"ready");assert.equal(reverse.loaded.profitNextDay.status,"unavailable");
});

test("slow completion beyond the former global deadline still refreshes the current panel",async()=>{
  let resolve;
  const f=fixture({fetcher:async(_u,_o,bytes)=>{await new Promise(r=>resolve=r);return {ok:true,arrayBuffer:async()=>bytes}}});
  const pending=f.run("refreshNextDayMarketStatistics(fixtureLoaded,()=>true)");
  await new Promise(r=>setTimeout(r,20));assert.equal(f.loaded.marketPerformance.status,"loading");
  resolve();await pending;
  assert.equal(f.loaded.marketPerformance.status,"ready");assert.equal(f.context.dashboardRenders,1);
  // No Promise.race can permanently discard a successful in-flight result.
  const start=html.indexOf("    async function refreshNextDayMarketStatistics(");
  assert.ok(!html.slice(start,html.indexOf("    function nextDayRetryMarkup(",start)).includes("Promise.race"));
});

test("transport failure retries once; persistent failure offers an explicit retry",async()=>{
  const f=fixture({fetcher:async(_u,_o,bytes,n)=>{if(n===1)throw Error("offline");return {ok:true,arrayBuffer:async()=>bytes}},timers:{setTimeout:(fn,ms)=>setTimeout(fn,ms===800?0:ms)}});
  await f.run("refreshNextDayMarketStatistics(fixtureLoaded,()=>true)");
  assert.equal(f.calls.length,2);assert.equal(f.loaded.marketPerformance.status,"ready");
  const bad=fixture({fetcher:async()=>{throw Error("offline")},timers:{setTimeout:(fn,ms)=>setTimeout(fn,ms===800?0:ms)}});
  await bad.run("refreshNextDayMarketStatistics(fixtureLoaded,()=>true)");
  assert.equal(bad.calls.length,2);assert.equal(bad.loaded.marketPerformance.status,"unavailable");
  assert.match(bad.run("nextDayRetryMarkup(fixtureLoaded.marketPerformance,'retry-test')"),/重试/);
});

test("late responses from a superseded load cannot repaint or replace current state",async()=>{
  let resolve;
  const f=fixture({fetcher:async(_u,_o,bytes)=>{await new Promise(r=>resolve=r);return {ok:true,arrayBuffer:async()=>bytes}}});
  const pending=f.run("refreshProfitNextDayStatistics(fixtureLoaded,fixtureActive)");
  await new Promise(r=>setTimeout(r,0));
  f.run("state.currentCompactStatisticsWindow={};");resolve();await pending;
  assert.equal(f.loaded.profitNextDay.status,"loading");assert.equal(f.context.profitRenders,undefined);
});

test("incorrect current activation cannot consume another model's summary",async()=>{
  const f=fixture();f.active.indexSha256="9".repeat(64);
  await f.run("refreshProfitNextDayStatistics(fixtureLoaded,fixtureActive)");
  assert.equal(f.loaded.profitNextDay.status,"unavailable");assert.equal(f.calls.length,0);
});

test("a missing candidate binding is allowed only for an explicitly unavailable profit section",()=>{
  const f=fixture();
  f.payload.profit={status:"UNAVAILABLE",reason:"candidate index unavailable"};
  f.payload.bindings.candidate_index_sha256=null;f.payload.bindings.candidate_activation_sha256=null;
  delete f.revision.candidate_profit_index_sha256;delete f.revision.candidate_profit_activation_sha256;
  f.context.testPayload=f.payload;
  let result=f.run("validateNextDayStatistics(testPayload,fixtureRevision,fixtureLoaded)");
  assert.equal(result.promotion.status,"ready");assert.equal(result.profit.status,"unavailable");
  f.payload.profit={status:"READY",as_of_date:"20261106",cohorts:[1,2].map(r=>cohort(r,true))};
  assert.throws(()=>f.run("validateNextDayStatistics(testPayload,fixtureRevision,fixtureLoaded)"),/指纹/);
});

test("a missing promotion source cannot poison an independently verified profit section",()=>{
  const f=fixture();f.payload.promotion={status:"UNAVAILABLE",reason:"promotion unavailable"};
  f.payload.bindings.primary_rows_sha256=null;f.loaded.payload.source_files=[];f.context.testPayload=f.payload;
  const result=f.run("validateNextDayStatistics(testPayload,fixtureRevision,fixtureLoaded)");
  assert.equal(result.promotion.status,"unavailable");assert.equal(result.profit.status,"ready");
});

test("stalled transport is aborted within the per-attempt deadline and remains bounded",async()=>{
  const f=fixture({timers:{setTimeout:(fn,ms)=>setTimeout(fn,ms===30000?2:0)},
    fetcher:async(_url,options)=>new Promise((_resolve,reject)=>options.signal.addEventListener("abort",()=>{const e=new Error("aborted");e.name="AbortError";reject(e)}))});
  await f.run("refreshNextDayMarketStatistics(fixtureLoaded,()=>true)");
  assert.equal(f.calls.length,2);assert.ok(f.calls.every(call=>call.options.signal.aborted));
  assert.equal(f.loaded.marketPerformance.status,"unavailable");assert.match(f.loaded.marketPerformance.reason,/超时/);
});
