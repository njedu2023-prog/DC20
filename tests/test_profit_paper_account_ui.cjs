"use strict";
// Run standalone-route regressions in the shared publication's existing UI suite.
require("./test_profit_paper_account_page.cjs");
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
const canonicalSource=html.slice(html.indexOf('    function ledgerCanonicalSource(bytes) {'),html.indexOf('    async function ledgerJoinDay('));
const sha=b=>crypto.createHash("sha256").update(b).digest("hex");
test('fatal source failure clears a previously displayed account', () => {
  assert.match(html,/function showError\(error\)\s*\{\s*state\.compactFatalError = true;\s*renderProfitPaperAccount\(\);/);
});

function context(extra={}) {
  const nodes=new Map();
  const c={console,setTimeout,clearTimeout,Map,Set,Uint8Array,TextDecoder,TextEncoder,Blob,
    state:{compactLoadSequence:1,compactFatalError:false},window:{innerWidth:1200},
    document:{getElementById(id){if(!nodes.has(id))nodes.set(id,{innerHTML:""});return nodes.get(id)}},
    executableProfitExpect(ok,message){if(!ok)throw Error(message)},
    isSha256:v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v),
    canonicalJson:JSON.stringify,sha256Hex:async b=>sha(b),
    parseStrictCsvBytes:(b)=>JSON.parse(Buffer.from(b).toString()),
    dateText:v=>v?String(v).replace(/^(\d{4})(\d{2})(\d{2})$/,"$1-$2-$3"):"—",
    beijingDateTimeText:v=>String(v||''),
    escapeHtml:v=>String(v??"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;"),
    pct:v=>typeof v==="number"&&Number.isFinite(v)?(v*100).toFixed(2)+"%":"—",valueTone:()=>"",...extra};
  vm.createContext(c);vm.runInContext(canonicalSource,c);vm.runInContext(source,c);return c;
}
function result() {
  const days=[{date:"20260914",nav:1e6,cash:1e6,market_value:0,realized_pnl:0,unrealized_pnl:0,drawdown:0,open_positions:0},
    {date:"20260915",nav:990000,cash:800000,market_value:190000,realized_pnl:0,unrealized_pnl:-10000,drawdown:-.01,open_positions:2}];
  return {as_of_date:"20261016",last_valid_date:"20260915",blocked:true,blocking_reason:"退出价格缺失 <unsafe>",missing_signal_dates:["20260921"],days,
    metrics:{nav:990000,cash:800000,market_value:190000,total_pnl:-10000,cumulative_return:-.01,realized_pnl:0,unrealized_pnl:-10000,
      open_trades:2,closed_trades:0,max_drawdown:.01,max_drawdown_amount:10000,max_capital_usage:.2,max_open_positions:2,
      max_open_positions_intraday:2,max_committed_cost_intraday:200000,
      total_costs:900,win_rate:null,profit_factor:null,longest_loss_streak:0,no_fill_trades:1,missing_trades:2,insufficient_cash_trades:0},
    rank_contributions:[{rank:1,closed_trades:0,open_trades:1,win_rate:null,realized_pnl:0,unrealized_pnl:-5000,total_pnl:-5000}],
    trades:[{signal_date:"20260921",rank:1,ts_code:null,status:"MISSING_FROZEN",net_pnl:null,unrealized_pnl:null}]};
}
test("markup distinguishes latest unavailable and last valid NAV, escaping source text",()=>{
  const c=context();const out=c.profitPaperAccountMarkup(result());
  assert.match(out,/最新账户资产：暂不可核验/);assert.match(out,/截至 2026-09-15/);assert.match(out,/已知交易子集/);
  assert.match(out,/&lt;unsafe&gt;/);assert.doesNotMatch(out,/<unsafe>/);assert.match(out,/冻结缺失 · 跳过/);
  assert.match(out,/暂不报告年化收益、夏普比/);assert.match(out,/990,000\.00 元/);
  assert.match(out,/盈1、盈2每笔预算各20万元/);assert.match(out,/每笔20万元为含研究成本的预算/);
  assert.match(out,/未验证真实排队成交、20万元容量/);assert.doesNotMatch(out,/(?:各|每笔)10万元/);
  assert.doesNotMatch(out,/onclick=/);
});
test("trade table puts company name immediately after code, escapes text and labels missing names",()=>{
  const c=context(), data=result();
  data.trades.push({signal_date:'20260914',rank:1,ts_code:'000823.SZ',name:'超声电子 <unsafe>',status:'CLOSED'});
  const out=c.profitPaperAccountMarkup(data);
  assert.match(out,/<th>代码<\/th><th class="left">公司名称<\/th>/);
  assert.match(out,/<td>000823\.SZ<\/td><td class="left">超声电子 &lt;unsafe&gt;<\/td>/);
  assert.match(out,/<td class="left">名称待补<\/td>/);
  assert.doesNotMatch(out,/<unsafe>/);
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
function identityInputs(c) {
  const prefix='outputs/decision/candidate_profit_v1', date='20260914', raw=new Map();
  const day={schema_version:'dc20_candidate_formal_profit_day_v1',signal_date:date,exec_date:'20260915',exit_date:'20260916',
    activation_id:'test-model',model_canonical_sha256:'a'.repeat(64),snapshot_file_sha256:'b'.repeat(64),
    rows:[1,2].map(rank=>({ts_code:`00000${rank}.SZ`,candidate_rank:rank,candidate_score:0.1*rank,promotion_rank:rank,name:rank===1?'超声电子':'澳弘电子'}))};
  day.candidate_slots=day.rows.map(r=>({...r,slot:r.candidate_rank}));
  const path=`${prefix}/day_${date}.json`, bytes=Buffer.from(JSON.stringify(day));raw.set(path,bytes);
  const index={schema_version:'dc20_candidate_formal_profit_index_v1',activation_id:day.activation_id,model_canonical_sha256:day.model_canonical_sha256,
    days:[{signal_date:date,path,sha256:sha(bytes),snapshot_file_sha256:day.snapshot_file_sha256}]};
  const indexPath=`${prefix}/index.json`;raw.set(indexPath,Buffer.from(JSON.stringify(index)));
  const summary={activation_id:day.activation_id,model_canonical_sha256:day.model_canonical_sha256,published_signal_dates:[date],groups:{}};
  const canonicalSha=sha(Buffer.from(c.ledgerCanonicalSource(bytes)));
  for(const rank of [1,2]) summary.groups[`candidate_top${rank}`]={daily_sequence:[{...day.candidate_slots[rank-1],signal_date:date,
    exec_date:day.exec_date,exit_date:day.exit_date,formal_projection_sha256:canonicalSha,snapshot_file_sha256:day.snapshot_file_sha256}]};
  return {raw,summary,index,day,path,indexPath,revision:{candidate_profit_index_url:indexPath,candidate_profit_index_sha256:sha(raw.get(indexPath))}};
}
test('company loader uses bound same-day frozen names and records their provenance',async()=>{
  const c=context(),x=identityInputs(c);c.fetchPagesOnlyPath=async p=>x.raw.get(p);
  const names=await c.loadProfitPaperAccountIdentities(x.summary,x.revision);
  assert.equal(names.identities.get('20260914:R1').name,'超声电子');
  assert.equal(names.identities.get('20260914:R2').name,'澳弘电子');
  assert.equal(names.identities.get('20260914:R1').source_sha256,x.index.days[0].sha256);
  assert.equal(names.source_files.length,2);
  const absent=await c.loadProfitPaperAccountIdentities(x.summary);
  assert.equal(absent.identities.size,0);assert.equal(absent.source_files.length,0);
});
test('company loader rejects changed SHA, date, slot or source projection and never uses another day',async()=>{
  for(const mode of ['sha','index-date','projection','snapshot','ticker','slot']) {
    const c=context(),x=identityInputs(c);c.fetchPagesOnlyPath=async p=>x.raw.get(p);
    if(mode==='sha') x.revision.candidate_profit_index_sha256='0'.repeat(64);
    if(mode==='index-date') x.summary.published_signal_dates=['20260915'];
    if(mode==='projection') x.summary.groups.candidate_top1.daily_sequence[0].formal_projection_sha256='0'.repeat(64);
    if(mode==='snapshot') x.summary.groups.candidate_top1.daily_sequence[0].snapshot_file_sha256='0'.repeat(64);
    if(mode==='ticker') x.summary.groups.candidate_top1.daily_sequence[0].ts_code='999999.SZ';
    if(mode==='slot') x.summary.groups.candidate_top1.daily_sequence[0].slot=2;
    await assert.rejects(()=>c.loadProfitPaperAccountIdentities(x.summary,x.revision));
  }
});
function publishedInputs() {
  const account=result(), source={path:'data/market/raw/2026/20260915/daily.csv',sha256:'5'.repeat(64)};
  const revision={profit_paper_account_url:'outputs/decision/profit_paper_account/account.json',profit_paper_account_sha256:'1'.repeat(64),
    profit_paper_account_as_of_date:account.as_of_date,profit_paper_account_last_valid_date:account.last_valid_date,
    head_sha:'2'.repeat(40),run_id:'12345',profit_paper_account_producer_run_id:'12345',profit_paper_account_updated_at:'2026-10-18T00:00:00Z',
    candidate_profit_summary_sha256:'3'.repeat(64),compact_statistics_window_sha256:'4'.repeat(64)};
  Object.assign(account,{schema_version:'dc20_paper_capital_account_v1',initial_capital:1000000,per_trade_budget:200000,cost_rate:.0045,
    publication:{mode:'main_pipeline',source_head_sha:revision.head_sha,run_id:revision.run_id,published_at_utc:revision.profit_paper_account_updated_at},
    data_bindings:{summary_sha256:revision.candidate_profit_summary_sha256,window_sha256:revision.compact_statistics_window_sha256,
      engine_sha256:'6'.repeat(64),source_files:[source]}});
  return {account,revision,summary:{summary:{as_of_date:account.as_of_date,round_trip_cost_rate:.0045}},
    window:{payload:{source_files:[{...source}]}},active:{revision}};
}
test('published account accepts only the current bound main-task artifact and displays publication time',async()=>{
  const x=publishedInputs(),calls=[];
  const c=context({fetchPagesOnlyShaBoundJson:async(...args)=>{calls.push(args);return {payload:x.account}}});
  const loaded=await c.loadPublishedProfitPaperAccount(x.summary,x.window,x.active);
  assert.equal(loaded,x.account);assert.equal(calls.length,1);
  assert.equal(calls[0][0],x.revision.profit_paper_account_url);assert.equal(calls[0][1],x.revision.profit_paper_account_sha256);
  const markup=c.profitPaperAccountMarkup(loaded);
  assert.match(markup,/主任务/);assert.match(markup,/12345/);assert.match(markup,/2026\/10\/18 08:00:00/);
});
test('published account rejects mismatched run, commit, dates, summary or statistics window',async()=>{
  for(const mode of ['run','producer','head','asof','revision-asof','last-date','summary-sha','window-sha','updated-at','mode','old-budget']) {
    const x=publishedInputs();
    if(mode==='run')x.account.publication.run_id='old';
    if(mode==='producer')x.revision.profit_paper_account_producer_run_id='old';
    if(mode==='head')x.account.publication.source_head_sha='f'.repeat(40);
    if(mode==='asof')x.account.as_of_date='20260930';
    if(mode==='revision-asof')x.revision.profit_paper_account_as_of_date='20260930';
    if(mode==='last-date')x.account.last_valid_date='20260914';
    if(mode==='summary-sha')x.account.data_bindings.summary_sha256='0'.repeat(64);
    if(mode==='window-sha')x.account.data_bindings.window_sha256='0'.repeat(64);
    if(mode==='updated-at')x.account.publication.published_at_utc='2026-10-17T00:00:00Z';
    if(mode==='mode')x.account.publication.mode='browser';
    if(mode==='old-budget')x.account.per_trade_budget=100000;
    const c=context({fetchPagesOnlyShaBoundJson:async()=>({payload:x.account})});
    await assert.rejects(()=>c.loadPublishedProfitPaperAccount(x.summary,x.window,x.active),undefined,mode);
  }
});
test('published account rejects market input from another source version',async()=>{
  for(const mode of ['sha','path']) {
    const x=publishedInputs();
    if(mode==='sha')x.account.data_bindings.source_files[0].sha256='0'.repeat(64);
    else x.account.data_bindings.source_files[0].path='data/market/raw/2026/20260916/daily.csv';
    const c=context({fetchPagesOnlyShaBoundJson:async()=>({payload:x.account})});
    await assert.rejects(()=>c.loadPublishedProfitPaperAccount(x.summary,x.window,x.active),/行情来源不属于本次主任务/);
  }
});
test('missing main-task binding fails closed without falling back to browser replay',async()=>{
  const x=publishedInputs(),calls=[];delete x.revision.profit_paper_account_sha256;
  const c=context({fetchPagesOnlyShaBoundJson:async()=>{calls.push('fetch');throw Error('must not fetch without binding')}});
  c.loadProfitPaperAccountInputs=async()=>{calls.push('inputs');return {quotes:new Map()}};
  c.buildProfitPaperAccount=()=>{calls.push('engine');return x.account};c.renderProfitPaperAccount=()=>{};
  Object.assign(c.state,{candidateProfitSummary:x.summary,currentCompactStatisticsWindow:x.window,candidateProfitActivation:x.active});
  await c.refreshProfitPaperAccount(x.summary,x.window,x.active);
  assert.equal(c.state.profitPaperAccount.status,'unavailable');assert.match(c.state.profitPaperAccount.reason,/主任务尚未发布/);
  assert.equal(c.state.profitPaperAccount.result,undefined);assert.deepEqual(calls,[]);
});
test("async stale result cannot inject after navigation or source replacement",async()=>{
  const c=context();const summary={summary:{}},window={},active={};
  Object.assign(c.state,{candidateProfitSummary:summary,currentCompactStatisticsWindow:window,candidateProfitActivation:active});
  let finish;c.loadPublishedProfitPaperAccount=()=>new Promise(resolve=>{finish=resolve});
  c.renderProfitPaperAccount=()=>{throw Error("stale render should never run")};
  const work=c.refreshProfitPaperAccount(summary,window,active);c.state.compactLoadSequence=2;finish({quotes:new Map(),source_files:[],missing_market_dates:[]});await work;
  assert.equal(c.state.profitPaperAccount.status,"loading");assert.equal(c.state.compactFatalError,false);
});
test("optional loader failure does not fail ranking or substitute zero account",async()=>{
  const c=context(),summary={summary:{}},window={},active={};let renders=0;
  Object.assign(c.state,{candidateProfitSummary:summary,currentCompactStatisticsWindow:window,candidateProfitActivation:active});
  c.loadPublishedProfitPaperAccount=async()=>{throw Error("bad SHA")};c.renderProfitPaperAccount=()=>{renders++};
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
