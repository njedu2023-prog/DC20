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
  return {schema_version:'dc20_top1_realized_paper_account_v1',as_of_date:'20261022',
    last_exit_date:'20261021',start_signal_date:'20260914',per_trade_budget:100000,cost_rate:.0045,
    closed_trades:2,metrics:{cumulative_pnl:4500,max_profit:10000,max_loss:-5500},missing_signal_dates:['20261021'],
    trades:[{signal_date:'20260914',rank:1,status:'CLOSED',exit_date:'20260916',net_pnl:10000},
      {signal_date:'20261019',rank:1,status:'CLOSED',exit_date:'20261021',net_pnl:-5500}]};
}
test("the ledger displays the three signed realized amounts with dates and coverage",()=>{
  const c=context({valueTone:n=>n>0?'positive':'negative'});
  const markup=c.profitPaperAccountMarkup(result());
  assert.equal((markup.match(/class="paper-card"/g)||[]).length,3);
  assert.match(markup,/<dt>累计已实现盈亏<\/dt><dd class="positive">\+4,500\.00 元/);
  assert.match(markup,/<dt>最大单笔盈利<\/dt><dd class="positive">\+10,000\.00 元/);
  assert.match(markup,/<dt>最大单笔亏损<\/dt><dd class="negative">-5,500\.00 元/);
  assert.match(markup,/最近退出日 2026-10-21/);
  assert.match(markup,/以下信号日缺少正式冻结名单/);
  assert.doesNotMatch(markup,/盈2|净值|最大回撤|<table|<svg/);
});
test('an empty realized set shows zero cumulative P&L and no extremes',()=>{
  const data=result();
  Object.assign(data,{closed_trades:0,last_exit_date:null,metrics:{cumulative_pnl:0,max_profit:null,max_loss:null},
    trades:[],missing_signal_dates:[]});
  const markup=context().profitPaperAccountMarkup(data);
  assert.match(markup,/尚无已退出交易/);
  assert.equal((markup.match(/>—<\/dd>/g)||[]).length,2);
  assert.match(markup,/>0\.00 元<\/dd>/);
  assert.doesNotMatch(markup,/缺少正式冻结名单/);
});
function inputs() {
  const raw=new Map();
  raw.set("data/market/trade_cal_sse.csv",Buffer.from(JSON.stringify([{cal_date:"20260914",is_open:"1"},{cal_date:"20261019",is_open:"1"},{cal_date:"20261020",is_open:"1"}])));
  raw.set("data/market/raw/2026/20261020/daily.csv",Buffer.from(JSON.stringify([{trade_date:"20261020",ts_code:"000001.SZ",close:"10",pre_close:"9"}])));
  const windowPayload={source_files:[...raw].map(([path,b])=>({path,sha256:sha(b)}))};
  const summary={as_of_date:"20261020",groups:{candidate_top1:{daily_sequence:[
    {signal_date:"20260914",exec_date:"20261019",actual_exit_date:"20261020",status:"SETTLED_1000_LIMIT_HOLD_MINUTE_PROXY"}]}},signal_dates:["20260914","20261019","20261020"],calendar_sha256:sha(raw.get("data/market/trade_cal_sse.csv"))};
  return {raw,summary,windowPayload};
}
test("loader only reads bound calendar and daily prices, leaves missing day absent",async()=>{
  const x=inputs(), calls=[];const c=context({fetchPagesOnlyPath:async(p,kind)=>{calls.push([p,kind]);return x.raw.get(p)}});
  const data=await c.loadProfitPaperAccountInputs(x.summary,x.windowPayload);
  assert.equal(data.quotes.size,1);assert.equal(data.quotes.has("20260914"),false);
  assert.equal(JSON.stringify(data.missing_market_dates),'["20261019"]');assert.equal(calls.length,2);
});
test('loader still checks the full legacy calendar but does not read legacy quote bindings',async()=>{
  const x=inputs(),calls=[],old='data/market/raw/2026/20260914/daily.csv';
  x.windowPayload.source_files.push({path:old,sha256:'0'.repeat(64)});
  const c=context({fetchPagesOnlyPath:async p=>{calls.push(p);assert.notEqual(p,old);return x.raw.get(p)}});
  const data=await c.loadProfitPaperAccountInputs(x.summary,x.windowPayload);
  assert.deepEqual(calls,['data/market/trade_cal_sse.csv','data/market/raw/2026/20261020/daily.csv']);
  assert.equal(JSON.stringify(data.missing_market_dates),'["20261019"]');
  x.summary.as_of_date='20261016';x.summary.signal_dates=['20260914'];calls.length=0;
  const before=await c.loadProfitPaperAccountInputs(x.summary,x.windowPayload);
  assert.equal(before.quotes.size,0);assert.equal(before.missing_market_dates.length,0);
  assert.deepEqual(calls,['data/market/trade_cal_sse.csv']);
});
test("loader rejects SHA mismatch, duplicate symbols, and calendar drift",async()=>{
  for(const mode of ["sha","duplicate","calendar"]){
    const x=inputs();
    if(mode==="sha")x.windowPayload.source_files[1].sha256="0".repeat(64);
    if(mode==="calendar")x.summary.signal_dates=["20261019","20261020"];
    if(mode==="duplicate") {const p="data/market/raw/2026/20261020/daily.csv";const q=JSON.parse(x.raw.get(p));q.push(q[0]);x.raw.set(p,Buffer.from(JSON.stringify(q)));x.windowPayload.source_files[1].sha256=sha(x.raw.get(p));}
    const c=context({fetchPagesOnlyPath:async p=>x.raw.get(p)});
    await assert.rejects(()=>c.loadProfitPaperAccountInputs(x.summary,x.windowPayload));
  }
});
function identityInputs(c) {
  const prefix='outputs/decision/candidate_profit_v1', date='20261019', raw=new Map();
  const day={schema_version:'dc20_candidate_formal_profit_day_v1',signal_date:date,exec_date:'20261020',exit_date:'20261021',
    activation_id:'test-model',model_canonical_sha256:'a'.repeat(64),snapshot_file_sha256:'b'.repeat(64),
    rows:[1,2].map(rank=>({ts_code:`00000${rank}.SZ`,candidate_rank:rank,candidate_score:0.1*rank,promotion_rank:rank,name:rank===1?'超声电子':'澳弘电子'}))};
  day.candidate_slots=day.rows.map(r=>({...r,slot:r.candidate_rank}));
  const path=`${prefix}/day_${date}.json`, bytes=Buffer.from(JSON.stringify(day));raw.set(path,bytes);
  const index={schema_version:'dc20_candidate_formal_profit_index_v1',activation_id:day.activation_id,model_canonical_sha256:day.model_canonical_sha256,
    days:[{signal_date:date,path,sha256:sha(bytes),snapshot_file_sha256:day.snapshot_file_sha256}]};
  const indexPath=`${prefix}/index.json`;raw.set(indexPath,Buffer.from(JSON.stringify(index)));
  const summary={as_of_date:date,activation_id:day.activation_id,model_canonical_sha256:day.model_canonical_sha256,published_signal_dates:[date],groups:{}};
  const canonicalSha=sha(Buffer.from(c.ledgerCanonicalSource(bytes)));
  for(const rank of [1,2]) summary.groups[`candidate_top${rank}`]={daily_sequence:[{...day.candidate_slots[rank-1],signal_date:date,
    exec_date:day.exec_date,exit_date:day.exit_date,formal_projection_sha256:canonicalSha,snapshot_file_sha256:day.snapshot_file_sha256}]};
  return {raw,summary,index,day,path,indexPath,revision:{candidate_profit_index_url:indexPath,candidate_profit_index_sha256:sha(raw.get(indexPath))}};
}
test('company loader uses bound same-day frozen names and records their provenance',async()=>{
  const c=context(),x=identityInputs(c);c.fetchPagesOnlyPath=async p=>x.raw.get(p);
  const names=await c.loadProfitPaperAccountIdentities(x.summary,x.revision);
  assert.equal(names.identities.get('20261019:R1').name,'超声电子');
  assert.equal(names.identities.has('20261019:R2'),false);
  assert.equal(names.identities.get('20261019:R1').source_sha256,x.index.days[0].sha256);
  assert.equal(names.source_files.length,2);
  const absent=await c.loadProfitPaperAccountIdentities(x.summary);
  assert.equal(absent.identities.size,0);assert.equal(absent.source_files.length,0);
});
test('company loader validates full index date coverage but reads only reset-period names',async()=>{
  const c=context(),x=identityInputs(c),calls=[];
  x.index.days.unshift({signal_date:'20260911',path:'outputs/decision/candidate_profit_v1/day_20260911.json',sha256:'0'.repeat(64),snapshot_file_sha256:'b'.repeat(64)});
  x.summary.published_signal_dates.unshift('20260911');
  x.raw.set(x.indexPath,Buffer.from(JSON.stringify(x.index)));
  x.revision.candidate_profit_index_sha256=sha(x.raw.get(x.indexPath));
  c.fetchPagesOnlyPath=async p=>{calls.push(p);return x.raw.get(p)};
  const names=await c.loadProfitPaperAccountIdentities(x.summary,x.revision);
  assert.equal(names.identities.size,1);assert.equal(names.identities.has('20260911:R1'),false);
  assert.deepEqual(calls,[x.indexPath,x.path]);
  x.summary.published_signal_dates=['20261019'];
  await assert.rejects(()=>c.loadProfitPaperAccountIdentities(x.summary,x.revision),/目录与冻结账本日期/);
});
test('company loader rejects changed SHA, date, slot or source projection and never uses another day',async()=>{
  for(const mode of ['sha','index-date','projection','snapshot','ticker','slot']) {
    const c=context(),x=identityInputs(c);c.fetchPagesOnlyPath=async p=>x.raw.get(p);
    if(mode==='sha') x.revision.candidate_profit_index_sha256='0'.repeat(64);
    if(mode==='index-date') x.summary.published_signal_dates=['20261020'];
    if(mode==='projection') x.summary.groups.candidate_top1.daily_sequence[0].formal_projection_sha256='0'.repeat(64);
    if(mode==='snapshot') x.summary.groups.candidate_top1.daily_sequence[0].snapshot_file_sha256='0'.repeat(64);
    if(mode==='ticker') x.summary.groups.candidate_top1.daily_sequence[0].ts_code='999999.SZ';
    if(mode==='slot') x.summary.groups.candidate_top1.daily_sequence[0].slot=2;
    await assert.rejects(()=>c.loadProfitPaperAccountIdentities(x.summary,x.revision));
  }
});
function publishedInputs() {
  const account=result(), source={path:'data/market/raw/2026/20261020/daily.csv',sha256:'5'.repeat(64)};
  const revision={profit_paper_account_url:'outputs/decision/profit_paper_account/account.json',profit_paper_account_sha256:'1'.repeat(64),
    profit_paper_account_as_of_date:account.as_of_date,profit_paper_account_last_valid_date:account.last_exit_date,
    head_sha:'2'.repeat(40),run_id:'12345',profit_paper_account_producer_run_id:'12345',profit_paper_account_updated_at:'2026-10-22T00:00:00Z',
    candidate_profit_index_sha256:'7'.repeat(64),candidate_profit_summary_sha256:'3'.repeat(64),compact_statistics_window_sha256:'4'.repeat(64)};
  Object.assign(account,{
    publication:{mode:'main_pipeline',source_head_sha:revision.head_sha,run_id:revision.run_id,published_at_utc:revision.profit_paper_account_updated_at},
    data_bindings:{summary_sha256:revision.candidate_profit_summary_sha256,window_sha256:revision.compact_statistics_window_sha256,index_sha256:revision.candidate_profit_index_sha256,
      engine_sha256:'6'.repeat(64),source_files:[source]}});
  return {account,revision,summary:{summary:{as_of_date:account.as_of_date,round_trip_cost_rate:.0045}},
    window:{payload:{source_files:[{...source}]}},active:{revision}};
}
test('published account accepts only the current bound main-task artifact and displays the last exit date',async()=>{
  const x=publishedInputs(),calls=[];
  const c=context({fetchPagesOnlyShaBoundJson:async(...args)=>{calls.push(args);return {payload:x.account}}});
  const loaded=await c.loadPublishedProfitPaperAccount(x.summary,x.window,x.active);
  assert.equal(loaded,x.account);assert.equal(calls.length,1);
  assert.equal(calls[0][0],x.revision.profit_paper_account_url);assert.equal(calls[0][1],x.revision.profit_paper_account_sha256);
  const markup=c.profitPaperAccountMarkup(loaded);
  assert.match(markup,/最近退出日 2026-10-21/);
});
test('published account accepts an empty settled set with no exit date or extremes',async()=>{
  const x=publishedInputs();
  Object.assign(x.account,{last_exit_date:null,closed_trades:0,metrics:{cumulative_pnl:0,max_profit:null,max_loss:null},
    missing_signal_dates:[],trades:[]});
  x.revision.profit_paper_account_last_valid_date=null;
  const c=context({fetchPagesOnlyShaBoundJson:async()=>({payload:x.account})});
  assert.equal(await c.loadPublishedProfitPaperAccount(x.summary,x.window,x.active),x.account);
});
test('published account rejects mismatched run, commit, dates, summary or statistics window',async()=>{
  for(const mode of ['run','producer','head','asof','revision-asof','last-date','summary-sha','window-sha','updated-at','mode','old-budget','old-schema','old-start','missing-start','bad-loss','bad-profit','old-trade','other-rank','old-missing-date','index-sha']) {
    const x=publishedInputs();
    if(mode==='run')x.account.publication.run_id='old';
    if(mode==='producer')x.revision.profit_paper_account_producer_run_id='old';
    if(mode==='head')x.account.publication.source_head_sha='f'.repeat(40);
    if(mode==='asof')x.account.as_of_date='20260930';
    if(mode==='revision-asof')x.revision.profit_paper_account_as_of_date='20260930';
    if(mode==='last-date')x.account.last_exit_date='20261019';
    if(mode==='summary-sha')x.account.data_bindings.summary_sha256='0'.repeat(64);
    if(mode==='window-sha')x.account.data_bindings.window_sha256='0'.repeat(64);
    if(mode==='updated-at')x.account.publication.published_at_utc='2026-10-17T00:00:00Z';
    if(mode==='mode')x.account.publication.mode='browser';
    if(mode==='old-budget')x.account.per_trade_budget=200000;
    if(mode==='old-schema')x.account.schema_version='dc20_paper_capital_account_v1';
    if(mode==='old-start')x.account.start_signal_date='20261019';
    if(mode==='missing-start')delete x.account.start_signal_date;
    if(mode==='bad-profit')x.account.metrics.max_profit=-1;
    if(mode==='bad-loss')x.account.metrics.max_loss=1;
    if(mode==='old-trade')x.account.trades[0].signal_date='20260911';
    if(mode==='other-rank')x.account.trades[0].rank=2;
    if(mode==='old-missing-date')x.account.missing_signal_dates=['20260911'];
    if(mode==='index-sha')x.account.data_bindings.index_sha256='0'.repeat(64);
    const c=context({fetchPagesOnlyShaBoundJson:async()=>({payload:x.account})});
    await assert.rejects(()=>c.loadPublishedProfitPaperAccount(x.summary,x.window,x.active),undefined,mode);
  }
});
test('published account rejects market input from another source version',async()=>{
  for(const mode of ['sha','path']) {
    const x=publishedInputs();
    if(mode==='sha')x.account.data_bindings.source_files[0].sha256='0'.repeat(64);
    else x.account.data_bindings.source_files[0].path='data/market/raw/2026/20261021/daily.csv';
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
  c.renderProfitPaperAccount();assert.match(c.document.getElementById("profitPaperAccountContent").innerHTML,/数据截至 2026-10-22/);
  assert.equal((c.document.getElementById("profitPaperAccountContent").innerHTML.match(/class="paper-card"/g)||[]).length,3);
  c.state.compactFatalError=true;c.renderProfitPaperAccount();assert.match(c.document.getElementById("profitPaperAccountContent").innerHTML,/已清空模拟账户/);
});
test("same homepage entry and existing refresh hooks include isolated account",()=>{
  assert.ok(html.indexOf('id="profitPaperAccount"')>html.indexOf('id="profitNextDay"'));
  assert.match(html,/renderProfitNextDayStatistics\(\);\s+renderProfitPaperAccount\(\);/);
  assert.match(html,/state\.profitPaperAccount = null;/);assert.match(html,/\.paper-cards[^}]*grid-template-columns:repeat\(3,/);
});

