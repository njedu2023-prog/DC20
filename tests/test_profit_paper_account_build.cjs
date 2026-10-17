'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const {materialize, verifyPublic, safeRelative, parseArgs, ACCOUNT_PATH, SUMMARY_PATH, WINDOW_PATH} = require('../scripts/build_profit_paper_account.cjs');
const SOURCE = path.resolve(__dirname,'../decision.html');
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const rawJson = value => Buffer.from(JSON.stringify(value,null,2)+'\n');
const dates = ['20260914','20260915','20260916','20260917'];
const canonical = value => {
  if (Array.isArray(value)) return '['+value.map(canonical).join(',')+']';
  if (value && typeof value === 'object') return '{'+Object.keys(value).sort().map(key=>canonical(key)+':'+canonical(value[key])).join(',')+'}';
  return JSON.stringify(value).replace(/[\u007f-\uffff]/g, c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
};
async function fixture(t, asOf = '20260916') {
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(),'dc20-account-build-test-')));
  t.after(()=>fs.rm(root,{recursive:true,force:true}));
  const write = async (relative,bytes) => {const target=path.join(root,relative);await fs.mkdir(path.dirname(target),{recursive:true});await fs.writeFile(target,bytes);return hash(bytes);};
  const writeJson = (relative,value)=>write(relative,rawJson(value));
  const html = await fs.readFile(SOURCE);
  await write('index.html',html); await write('decision.html',html);
  const used = dates.filter(date=>date<=asOf), model='a'.repeat(64), activation='test-activation';
  const calendar = Buffer.from('cal_date,is_open\n'+dates.map(date=>date+',1').join('\n')+'\n');
  const calendarSha = await write('data/market/trade_cal_sse.csv',calendar);
  const summary = {as_of_date:asOf,activation_id:activation,model_canonical_sha256:model,
    calendar_sha256:calendarSha,signal_dates:used,published_signal_dates:used,missing_signal_dates:[],
    round_trip_cost_rate:0.0045,coverage_complete:true,groups:{candidate_top1:{daily_sequence:[]},candidate_top2:{daily_sequence:[]}}};
  const index={schema_version:'dc20_candidate_formal_profit_index_v1',activation_id:activation,model_canonical_sha256:model,days:[]};
  const sourceFiles=[{path:'data/market/trade_cal_sse.csv',sha256:calendarSha}];
  for (const date of used) {
    const position=dates.indexOf(date), entry=dates[position+1]||'20260918', exit=dates[position+2]||'20260921';
    const slots=[1,2].map(rank=>({slot:rank,ts_code:`00000${rank}.SZ`,candidate_rank:rank,candidate_score:0.5,promotion_rank:rank}));
    const day={schema_version:'dc20_candidate_formal_profit_day_v1',signal_date:date,exec_date:entry,exit_date:exit,
      activation_id:activation,model_canonical_sha256:model,snapshot_file_sha256:'b'.repeat(64),candidate_slots:slots,
      rows:slots.map(slot=>({...slot,name:slot.slot===1?'平安银行':'万科A'}))};
    const relative=`outputs/decision/candidate_profit_v1/day_${date}.json`;
    const daySha=await writeJson(relative,day);
    index.days.push({signal_date:date,path:relative,sha256:daySha,snapshot_file_sha256:day.snapshot_file_sha256});
    for (const slot of slots) summary.groups[`candidate_top${slot.slot}`].daily_sequence.push({...slot,
      signal_date:date,exec_date:entry,exit_date:exit,snapshot_file_sha256:day.snapshot_file_sha256,
      formal_projection_sha256:hash(canonical(day)),status:position===0?'SETTLED_1000_LIMIT_HOLD_MINUTE_PROXY':'NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED',
      proxy_fill:position===0?1:0,entry_price:position===0?10:null,exit_price:position===0?11:null,
      actual_exit_date:position===0?exit:null,actual_exit_time:position===0?'2026-09-16T10:00:00+08:00':null,
      slot_net_return:position===0?0.1-0.0045:0});
    const dailyPath=`data/market/raw/2026/${date}/daily.csv`;
    sourceFiles.push({path:dailyPath,sha256:await write(dailyPath,Buffer.from(`ts_code,trade_date,close,pre_close\n000001.SZ,${date},10,10\n000002.SZ,${date},10,10\n`))});
  }
  const window={report_signal_date:asOf,source_files:sourceFiles};
  const revision={repository:'njedu2023-prog/DC20',branch:'main',head_sha:'c'.repeat(40),run_id:123,
    published_at_utc:'2026-10-18T01:00:00Z',primary_d_signal_date:asOf,
    candidate_profit_summary_url:SUMMARY_PATH,candidate_profit_summary_sha256:await writeJson(SUMMARY_PATH,summary),
    candidate_profit_index_url:'outputs/decision/candidate_profit_v1/index.json',
    candidate_profit_index_sha256:await writeJson('outputs/decision/candidate_profit_v1/index.json',index),
    compact_statistics_window_url:WINDOW_PATH,compact_statistics_window_sha256:await writeJson(WINDOW_PATH,window)};
  await writeJson('revision.json',revision);
  return {root,summary,index,window,revision,write,writeJson,
    build:()=>materialize({siteRoot:root,sourceHtml:SOURCE}),
    account:async()=>JSON.parse(await fs.readFile(path.join(root,ACCOUNT_PATH),'utf8')),
    updateWindow:async()=>{revision.compact_statistics_window_sha256=await writeJson(WINDOW_PATH,window);await writeJson('revision.json',revision);}};
}

test('build binds published account to same revision, engine, ledger, calendar and company names',async t=>{
  const f=await fixture(t), beforeSummary=await fs.readFile(path.join(f.root,SUMMARY_PATH));
  const out=await f.build(), account=await f.account();
  assert.equal(out.status,'built'); assert.equal(out.profit_paper_account_as_of_date,'20260916');
  assert.equal(account.publication.source_head_sha,f.revision.head_sha); assert.equal(account.publication.run_id,123);
  assert.equal(account.publication.published_at_utc,f.revision.published_at_utc);
  assert.match(account.data_bindings.engine_sha256,/^[0-9a-f]{64}$/);
  assert.equal(account.trades[0].name,'平安银行'); assert.equal(account.trades[1].name,'万科A');
  assert.equal(account.trades[0].name_source.signal_date,'20260914');
  assert.equal(account.initial_capital,1000000); assert.equal(account.per_trade_budget,200000);
  assert.equal(account.metrics.nav,1038009); assert.equal(account.metrics.closed_trades,2);
  const revision=JSON.parse(await fs.readFile(path.join(f.root,'revision.json'),'utf8'));
  assert.equal(revision.profit_paper_account_sha256,hash(await fs.readFile(path.join(f.root,ACCOUNT_PATH))));
  assert.equal(revision.profit_paper_account_last_valid_date,'20260916');
  assert.equal(revision.profit_paper_account_updated_at,f.revision.published_at_utc);
  assert.deepEqual(await fs.readFile(path.join(f.root,SUMMARY_PATH)),beforeSummary);
});
test('same-day rerun is byte-identical and does not append or double funds',async t=>{
  const f=await fixture(t);await f.build();
  const account=await fs.readFile(path.join(f.root,ACCOUNT_PATH)),revision=await fs.readFile(path.join(f.root,'revision.json'));
  await f.build();assert.deepEqual(await fs.readFile(path.join(f.root,ACCOUNT_PATH)),account);
  assert.deepEqual(await fs.readFile(path.join(f.root,'revision.json')),revision);
});
test('new as-of revision advances account while preserving initial capital and existing transactions',async t=>{
  const first=await fixture(t,'20260916'),next=await fixture(t,'20260917');
  await first.build();await next.build();const a=await first.account(),b=await next.account();
  assert.equal(b.last_valid_date,'20260917');assert.equal(b.as_of_date,'20260917');
  assert.equal(a.initial_capital,b.initial_capital);assert.equal(a.metrics.nav,b.metrics.nav);
  assert.equal(a.metrics.closed_trades,b.metrics.closed_trades);assert.equal(b.trades.length,a.trades.length+2);
});
test('bad summary SHA fails before any artifact or revision changes',async t=>{
  const f=await fixture(t),before=await fs.readFile(path.join(f.root,'revision.json'));
  await f.write(SUMMARY_PATH,Buffer.from('{}'));
  await assert.rejects(f.build(),/SHA256 mismatch/);
  await assert.rejects(fs.readFile(path.join(f.root,ACCOUNT_PATH)),{code:'ENOENT'});
  assert.deepEqual(await fs.readFile(path.join(f.root,'revision.json')),before);
});
test('bad daily SHA rejects rather than creating a truncated or zero-filled account',async t=>{
  const f=await fixture(t);await f.write('data/market/raw/2026/20260915/daily.csv',Buffer.from('invalid'));
  await assert.rejects(f.build(),/SHA/);
});
test('unbound historical market date truncates at missing valuation, never substitutes zero',async t=>{
  const f=await fixture(t);f.window.source_files=f.window.source_files.filter(row=>!row.path.includes('20260915/daily.csv'));
  await f.updateWindow();await f.build();const account=await f.account();
  assert.equal(account.blocked,true);assert.equal(account.last_valid_date,'20260914');
  assert.deepEqual(account.data_bindings.missing_market_dates,['20260915']);assert.equal(account.metrics.nav,1000000);
});
test('date mismatch and duplicate source paths are rejected',async t=>{
  const f=await fixture(t);f.window.report_signal_date='20260915';await f.updateWindow();
  await assert.rejects(f.build(),/dates differ/);
  f.window.report_signal_date='20260916';f.window.source_files.push(f.window.source_files[0]);await f.updateWindow();
  await assert.rejects(f.build(),/duplicate source/);
});
test('P0 may advance ahead of the bound ledger; only the ledger cutoff is replayed',async t=>{
  const f=await fixture(t);f.revision.primary_d_signal_date='20260917';f.window.report_signal_date='20260917';
  // A future day's invalid bytes must not be read by the older ledger replay.
  const futurePath='data/market/raw/2026/20260917/daily.csv';
  f.window.source_files.push({path:futurePath,sha256:await f.write(futurePath,Buffer.from('invalid future market data'))});
  await f.updateWindow();await f.build();const account=await f.account();
  assert.equal(account.as_of_date,'20260916');assert.equal(account.last_valid_date,'20260916');
  assert.equal(account.publication.latest_signal_date,'20260917');assert.equal(account.publication.ledger_lagging,true);
  assert.equal(account.days.at(-1).date,'20260916');assert.equal(account.metrics.nav,1038009);
  assert.equal(account.data_bindings.source_files.some(row=>row.path===futurePath),false);
});
test('a ledger beyond the main signal date and impossible date strings are refused',async t=>{
  const f=await fixture(t);f.revision.primary_d_signal_date='20260915';f.window.report_signal_date='20260915';
  await f.updateWindow();await assert.rejects(f.build(),/future ledger/);
  f.revision.primary_d_signal_date='20260932';f.window.report_signal_date='20260932';
  await f.updateWindow();await assert.rejects(f.build(),/dates differ/);
});
test('path traversal, encodings, URLs and duplicate CLI options are refused',()=>{
  for(const value of ['../secret','data/../../secret','/etc/passwd','data\\secret','data/%2e%2e/secret','https://example.com/x','data//x','data/./x','data/x?token=a','data/x\n'])assert.throws(()=>safeRelative(value),/unsafe/);
  assert.equal(safeRelative('data/market/raw/2026/20260914/daily.csv'),'data/market/raw/2026/20260914/daily.csv');
  assert.throws(()=>parseArgs(['--site-root','_site','--site-root','other']),/invalid/);
});
test('source and output symlinks are refused without modifying destination',async t=>{
  const f=await fixture(t);const daily=path.join(f.root,'data/market/raw/2026/20260915/daily.csv');
  await fs.unlink(daily);await fs.symlink(path.join(f.root,'revision.json'),daily);
  await assert.rejects(f.build(),/symlink/);
  await fs.unlink(daily);await f.write('data/market/raw/2026/20260915/daily.csv',Buffer.from('ts_code,trade_date,close,pre_close\n000001.SZ,20260915,10,10\n000002.SZ,20260915,10,10\n'));
  await fs.mkdir(path.dirname(path.join(f.root,ACCOUNT_PATH)),{recursive:true});
  await fs.symlink(path.join(f.root,'revision.json'),path.join(f.root,ACCOUNT_PATH));
  const revision=await fs.readFile(path.join(f.root,'revision.json'));await assert.rejects(f.build(),/symlink/);
  assert.deepEqual(await fs.readFile(path.join(f.root,'revision.json')),revision);
});
test('a deployed HTML differing from the shared engine source is a hard failure',async t=>{
  const f=await fixture(t);await f.write('index.html',Buffer.from('old page'));
  await assert.rejects(f.build(),/differs from decision.html/);
  const changed=Buffer.from((await fs.readFile(SOURCE,'utf8')).replace('const initialCapital = 1000000;','const initialCapital = 5000000;'));
  await f.write('index.html',changed);await f.write('decision.html',changed);
  await assert.rejects(f.build(),/implementation differs from --source-html/);
});
test('expected deployment banner injection is allowed when engine/loaders are unchanged',async t=>{
  const f=await fixture(t),withBanner=Buffer.from((await fs.readFile(SOURCE,'utf8')).replace('</body>','<aside>历史正文提示</aside></body>'));
  await f.write('index.html',withBanner);await f.write('decision.html',withBanner);
  await f.build();assert.equal((await f.account()).metrics.nav,1038009);
});
test('public verification requires exact artifact/revision bytes and rejects stale publication',async t=>{
  const f=await fixture(t);await f.build();const seen=[];
  const fetchImpl=async url=>{seen.push(String(url));const relative=url.pathname.replace(/^\/DC20\//,'');const raw=await fs.readFile(path.join(f.root,relative));return {ok:true,status:200,arrayBuffer:async()=>raw};};
  const result=await verifyPublic({siteRoot:f.root,baseUrl:'https://example.test/DC20/',fetchImpl});
  assert.equal(result.status,'verified');assert.equal(seen.length,2);assert.ok(seen.every(url=>url.includes('paper_account_run_id=123')));
  await assert.rejects(verifyPublic({siteRoot:f.root,baseUrl:'https://example.test/DC20/',fetchImpl:async()=>({ok:true,status:200,arrayBuffer:async()=>Buffer.from('{}')})}),/not converged/);
  await assert.rejects(verifyPublic({siteRoot:f.root,baseUrl:'http://example.test/DC20/',fetchImpl}),/HTTPS/);
});
