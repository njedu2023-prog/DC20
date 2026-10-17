"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const crypto = require("node:crypto");
const html = fs.readFileSync(path.join(__dirname,"..","decision.html"),"utf8");
const start=html.indexOf("    // Read-only cash-account view.");
const end=html.indexOf("    async function refreshCompactStatisticsWindow()",start);
assert.ok(start>0 && end>start,"actual account UI functions must exist");
const source=html.slice(start,end);
const sha=b=>crypto.createHash("sha256").update(b).digest("hex");
test('fatal source failure clears a previously displayed account', () => {
  assert.match(html,/function showError\(error\)\s*\{\s*state\.compactFatalError = true;\s*renderProfitPaperAccount\(\);/);
});

function context(extra={}) {
  const nodes=new Map();
  const c={console,setTimeout,clearTimeout,Map,Set,Uint8Array,TextDecoder,Blob,
    state:{compactLoadSequence:1,compactFatalError:false},window:{innerWidth:1200},
    document:{getElementById(id){if(!nodes.has(id))nodes.set(id,{innerHTML:""});return nodes.get(id)}},
    executableProfitExpect(ok,message){if(!ok)throw Error(message)},
    canonicalJson:JSON.stringify,sha256Hex:async b=>sha(b),
    parseStrictCsvBytes:(b)=>JSON.parse(Buffer.from(b).toString()),
    dateText:v=>v?String(v).replace(/^(\d{4})(\d{2})(\d{2})$/,"$1-$2-$3"):"—",
    escapeHtml:v=>String(v??"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;"),
    pct:v=>typeof v==="number"&&Number.isFinite(v)?(v*100).toFixed(2)+"%":"—",valueTone:()=>"",...extra};
  vm.createContext(c);vm.runInContext(source,c);return c;
}
function result() {
  const days=[{date:"20260914",nav:1e6,cash:1e6,market_value:0,realized_pnl:0,unrealized_pnl:0,drawdown:0,open_positions:0},
    {date:"20260915",nav:990000,cash:800000,market_value:190000,realized_pnl:0,unrealized_pnl:-10000,drawdown:-.01,open_positions:2}];
  return {as_of_date:"20261016",last_valid_date:"20260915",blocked:true,blocking_reason:"退出价格缺失 <unsafe>",missing_signal_dates:["20260921"],days,
    metrics:{nav:990000,cash:800000,market_value:190000,total_pnl:-10000,cumulative_return:-.01,realized_pnl:0,unrealized_pnl:-10000,
      open_trades:2,closed_trades:0,max_drawdown:.01,max_drawdown_amount:10000,max_capital_usage:.2,max_open_positions:2,
      total_costs:900,win_rate:null,profit_factor:null,longest_loss_streak:0,no_fill_trades:1,missing_trades:2,insufficient_cash_trades:0},
    rank_contributions:[{rank:1,closed_trades:0,open_trades:1,win_rate:null,realized_pnl:0,unrealized_pnl:-5000,total_pnl:-5000}],
    trades:[{signal_date:"20260921",rank:1,ts_code:null,status:"MISSING_FROZEN",net_pnl:null,unrealized_pnl:null}]};
}
test("markup distinguishes latest unavailable and last valid NAV, escaping source text",()=>{
  const c=context();const out=c.profitPaperAccountMarkup(result());
  assert.match(out,/最新账户资产：暂不可核验/);assert.match(out,/截至 2026-09-15/);assert.match(out,/已知交易子集/);
  assert.match(out,/&lt;unsafe&gt;/);assert.doesNotMatch(out,/<unsafe>/);assert.match(out,/冻结缺失 · 跳过/);
  assert.match(out,/暂不报告年化收益、夏普比/);assert.match(out,/990,000\.00 元/);
  assert.doesNotMatch(out,/onclick=/);
});
test("responsive curve handles one point and no valid data without NaN",()=>{
  const c=context();const svg=c.paperAccountCurveSvg(result().days,true);
  assert.match(svg,/viewBox="0 0 640 430"/);assert.match(svg,/paper-narrow-chart/);assert.match(svg,/role="img"/);
  assert.doesNotMatch(c.paperAccountCurveSvg(result().days.slice(0,1),false),/NaN|Infinity/);
  assert.match(c.paperAccountCurveSvg([]),/不绘制零收益线/);
});
function inputs() {
  const raw=new Map();
  raw.set("data/market/trade_cal_sse.csv",Buffer.from(JSON.stringify([{cal_date:"20260914",is_open:"1"},{cal_date:"20260915",is_open:"1"}])));
  raw.set("data/market/raw/2026/20260915/daily.csv",Buffer.from(JSON.stringify([{trade_date:"20260915",ts_code:"000001.SZ",close:"10",pre_close:"9"}])));
  const windowPayload={source_files:[...raw].map(([path,b])=>({path,sha256:sha(b)}))};
  const summary={as_of_date:"20260915",signal_dates:["20260914","20260915"],calendar_sha256:sha(raw.get("data/market/trade_cal_sse.csv"))};
  return {raw,summary,windowPayload};
}
test("loader only reads bound calendar and daily prices, leaves missing day absent",async()=>{
  const x=inputs(), calls=[];const c=context({fetchPagesOnlyPath:async(p,kind)=>{calls.push([p,kind]);return x.raw.get(p)}});
  const data=await c.loadProfitPaperAccountInputs(x.summary,x.windowPayload);
  assert.equal(data.quotes.size,1);assert.equal(data.quotes.has("20260914"),false);
  assert.equal(JSON.stringify(data.missing_market_dates),'["20260914"]');assert.equal(calls.length,2);
});
test("loader rejects SHA mismatch, duplicate symbols, and calendar drift",async()=>{
  for(const mode of ["sha","duplicate","calendar"]){
    const x=inputs();
    if(mode==="sha")x.windowPayload.source_files[1].sha256="0".repeat(64);
    if(mode==="calendar")x.summary.signal_dates=["20260915"];
    if(mode==="duplicate") {const p="data/market/raw/2026/20260915/daily.csv";const q=JSON.parse(x.raw.get(p));q.push(q[0]);x.raw.set(p,Buffer.from(JSON.stringify(q)));x.windowPayload.source_files[1].sha256=sha(x.raw.get(p));}
    const c=context({fetchPagesOnlyPath:async p=>x.raw.get(p)});
    await assert.rejects(()=>c.loadProfitPaperAccountInputs(x.summary,x.windowPayload));
  }
});
test("async stale result cannot inject after navigation or source replacement",async()=>{
  const c=context();const summary={summary:{}},window={},active={};
  Object.assign(c.state,{candidateProfitSummary:summary,currentCompactStatisticsWindow:window,candidateProfitActivation:active});
  let finish;c.loadProfitPaperAccountInputs=()=>new Promise(resolve=>{finish=resolve});
  c.buildProfitPaperAccount=()=>{throw Error("stale engine should never run")};c.renderProfitPaperAccount=()=>{throw Error("stale render should never run")};
  const work=c.refreshProfitPaperAccount(summary,window,active);c.state.compactLoadSequence=2;finish({quotes:new Map(),source_files:[],missing_market_dates:[]});await work;
  assert.equal(c.state.profitPaperAccount.status,"loading");assert.equal(c.state.compactFatalError,false);
});
test("optional loader failure does not fail ranking or substitute zero account",async()=>{
  const c=context(),summary={summary:{}},window={},active={};let renders=0;
  Object.assign(c.state,{candidateProfitSummary:summary,currentCompactStatisticsWindow:window,candidateProfitActivation:active});
  c.loadProfitPaperAccountInputs=async()=>{throw Error("bad SHA")};c.renderProfitPaperAccount=()=>{renders++};
  await c.refreshProfitPaperAccount(summary,window,active);
  assert.equal(c.state.profitPaperAccount.status,"unavailable");assert.equal(c.state.profitPaperAccount.result,undefined);
  assert.equal(c.state.compactFatalError,false);assert.equal(renders,1);
});
test("renderer does not bind latest account to selected historical D",()=>{
  const c=context(),active={status:"ready",config:{}},summary={summary:{},activation:null},window={compactLoadSequence:1};summary.activation=active.config;
  Object.assign(c.state,{candidateProfitSummary:summary,currentCompactStatisticsWindow:window,candidateProfitActivation:active,
    candidateProfitSummaryLoad:{status:"ready"},compactStatisticsWindowLoad:{status:"ready"},dailyRequestedDate:"20260914",
    profitPaperAccount:{status:"ready",loadedSummary:summary,loadedWindow:window,active,loadSequence:1,result:result()}});
  c.renderProfitPaperAccount();assert.match(c.document.getElementById("profitPaperAccountContent").innerHTML,/数据截至 2026-10-16/);
  assert.equal(typeof c.document.getElementById("profitPaperAccountExport").onclick,"function");
  c.state.compactFatalError=true;c.renderProfitPaperAccount();assert.match(c.document.getElementById("profitPaperAccountContent").innerHTML,/已清空模拟账户/);
});
test("same homepage entry and existing refresh hooks include isolated account",()=>{
  assert.ok(html.indexOf('id="profitPaperAccount"')>html.indexOf('id="profitNextDay"'));
  assert.match(html,/renderProfitNextDayStatistics\(\);\s+renderProfitPaperAccount\(\);/);
  assert.match(html,/state\.profitPaperAccount = null;/);assert.match(html,/\.paper-table-wrap[^}]*overflow-x:auto/);
});
