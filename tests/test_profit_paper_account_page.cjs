"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const html = fs.readFileSync(path.join(__dirname,"..","decision.html"),"utf8");
const start = html.indexOf("    function showProfitPaperAccountPageError(error) {");
const end = html.indexOf("    async function initializeProfitLedger() {",start);
assert.ok(start > 0 && end > start, "independent account page functions must exist");
const source = html.slice(start,end);

function fixture(extra = {}) {
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id,{innerHTML:"",disabled:false,attributes:{},setAttribute(k,v){this.attributes[k]=v;}});
    return nodes.get(id);
  };
  const revision = {compact_statistics_window_url:"outputs/decision/compact_statistics_window.json",compact_statistics_window_start_signal_date:"20260910",compact_statistics_window_sha256:"a".repeat(64),primary_d_signal_date:"20261016"};
  const header = {hidden:false};
  const calls = [];
  const context = {Map,Promise,console,
    state:{compactLoadSequence:4,profitPaperAccount:{status:"ready",result:{old:true}},resourceReads:new Map([["old",1]]),publishedRevision:{old:true},publishedRevisionSha:"old"},
    els:{mainContent:node("main"),footer:{textContent:""}},
    document:{title:"",querySelector(s){return s === "header" ? header : {remove(){calls.push("removeSkip")}};},getElementById:node},
    executableProfitExpect(ok,message){if(!ok)throw Error(message)},
    canonicalYmd:v=>v,isSha256:v=>/^[a-f0-9]{64}$/.test(v),
    escapeHtml:v=>String(v).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;"),
    COMPACT_STATISTICS_WINDOW_PATH:"outputs/decision/compact_statistics_window.json",COMPACT_STATISTICS_WINDOW_START:"20260910",
    async refreshCandidateProfitActivation(){calls.push("activation");context.state.candidateProfitActivation={status:"ready",revision};},
    async refreshCandidateProfitSummary(){calls.push("summary");context.state.candidateProfitSummary={summary:{as_of_date:"20261016"}};context.state.candidateProfitSummaryLoad={status:"ready"};},
    async fetchPagesOnlyShaBoundJson(p,h,l){calls.push(["fetch",p,h,l]);return {payload:{source_files:[]}};},
    async validateCompactStatisticsWindow(payload,date){calls.push(["validate",payload,date]);return {payload};},
    async refreshProfitPaperAccount(summary,window,active){calls.push(["account",summary,window,active]);context.state.profitPaperAccount={status:"ready",result:{fresh:true}};node("profitPaperAccountContent").innerHTML="fresh account";},
    ...extra};
  vm.createContext(context);
  vm.runInContext(source,context);
  return {context,calls,node,revision,header};
}

test("homepage has new-window entry links and no inline account loader", () => {
  const main = html.slice(0,html.indexOf("<script>"));
  const links = [...main.matchAll(/<a\b[^>]*href="\?view=paper-account"[^>]*>/g)].map(m=>m[0]);
  assert.equal(links.length,2);
  for (const link of links) {
    assert.match(link,/target="_blank"/);
    assert.match(link,/rel="noopener noreferrer"/);
  }
  assert.match(main,/id="profitPaperAccount"/);
  assert.doesNotMatch(main,/id="profitPaperAccountContent"/);
  assert.match(main,/本金100万元 · 盈1 \/ 盈2各20万元/);
});

test("standalone route selects account initializer before ledger and homepage", () => {
  assert.match(html,/if \(new URLSearchParams\(location.search\).get\("view"\) === "paper-account"\) \{\s*initializeProfitPaperAccount\(\).catch\(showProfitPaperAccountPageError\);\s*\} else if/);
  assert.doesNotMatch(source,/refreshCompactStatisticsWindow\(|refreshNextDayMarketStatistics\(|renderCompactDashboard\(|initialize\(/);
});

test("independent page shell has explicit budget, return link and usable refresh", async () => {
  const f = fixture();
  await f.context.initializeProfitPaperAccount();
  assert.equal(f.context.document.title,"盈利前二 · 模拟账户 | DC20");
  assert.equal(f.header.hidden,true);
  assert.match(f.context.els.mainContent.innerHTML,/每笔预算各20万元/);
  assert.match(f.context.els.mainContent.innerHTML,/href="\?"/);
  assert.match(f.context.els.mainContent.innerHTML,/id="profitPaperAccountContent"/);
  assert.equal(typeof f.node("paperAccountRefresh").onclick,"function");
  assert.equal(f.node("paperAccountRefresh").disabled,false);
  assert.equal(f.context.els.mainContent.attributes["aria-busy"],"false");
  assert.equal(f.calls.filter(c=>Array.isArray(c)&&c[0]==="validate").length,1);
  assert.equal(f.calls.filter(c=>Array.isArray(c)&&c[0]==="account").length,1);
});

test("refresh clears old values and caches before reading fresh sources", async () => {
  let release;
  const pending = new Promise(resolve=>{release=resolve;});
  const f = fixture();
  f.context.refreshCandidateProfitActivation = async () => {
    await pending;
    f.context.state.candidateProfitActivation={status:"ready",revision:f.revision};
  };
  f.node("profitPaperAccountContent").innerHTML="old NAV";
  const loading = f.context.refreshProfitPaperAccountPage(true);
  assert.equal(f.context.state.profitPaperAccount,null);
  assert.equal(f.context.state.publishedRevision,null);
  assert.equal(f.context.state.resourceReads.size,0);
  assert.doesNotMatch(f.node("profitPaperAccountContent").innerHTML,/old NAV/);
  assert.equal(f.node("paperAccountRefresh").disabled,true);
  release();await loading;
  assert.equal(f.node("profitPaperAccountContent").innerHTML,"fresh account");
  assert.equal(f.node("paperAccountRefresh").disabled,false);
});

test("source-validation failure clears previous NAV and escapes diagnostic text", async () => {
  const f = fixture({validateCompactStatisticsWindow:async()=>{throw Error("wrong <window>")}});
  f.node("profitPaperAccountContent").innerHTML="old NAV";
  await f.context.refreshProfitPaperAccountPage();
  assert.equal(f.context.state.profitPaperAccount,null);
  assert.equal(f.context.state.compactFatalError,true);
  assert.match(f.node("profitPaperAccountContent").innerHTML,/wrong &lt;window&gt;/);
  assert.match(f.node("profitPaperAccountContent").innerHTML,/已清空旧净值/);
  assert.doesNotMatch(f.node("profitPaperAccountContent").innerHTML,/old NAV|<window>/);
  assert.equal(f.calls.filter(c=>Array.isArray(c)&&c[0]==="account").length,0);
  assert.equal(f.node("paperAccountRefresh").disabled,false);
});

test("mismatched window revision is rejected before source validation", async () => {
  const f = fixture();f.revision.compact_statistics_window_sha256="bad";
  await f.context.refreshProfitPaperAccountPage();
  assert.match(f.node("profitPaperAccountContent").innerHTML,/统计窗口未绑定Pages精确revision/);
  assert.equal(f.calls.filter(c=>Array.isArray(c)&&c[0]==="validate").length,0);
});

test("stale page load cannot overwrite a newer load", async () => {
  let release;
  const pending = new Promise(resolve=>{release=resolve;});
  const f = fixture();
  f.context.validateCompactStatisticsWindow = async payload => {await pending;return {payload};};
  const loading = f.context.refreshProfitPaperAccountPage();
  await new Promise(resolve=>setImmediate(resolve));
  f.context.state.compactLoadSequence++;
  f.context.state.currentCompactStatisticsWindow={newer:true};
  f.node("profitPaperAccountContent").innerHTML="newer account";
  release();await loading;
  assert.equal(f.context.state.currentCompactStatisticsWindow.newer,true);
  assert.equal(f.node("profitPaperAccountContent").innerHTML,"newer account");
  assert.equal(f.calls.filter(c=>Array.isArray(c)&&c[0]==="account").length,0);
});
