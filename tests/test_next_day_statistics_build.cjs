'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs/promises'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const {build,materialize,OUTPUT_PATH,WINDOW_PATH,INDEX_PATH,ACTIVATION_PATH,ROWS_PATH,SUMMARY_PATH}=require('../scripts/build_next_day_statistics.cjs');
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
const raw=v=>Buffer.from(JSON.stringify(v,null,2)+'\n');
const SOURCE=path.resolve(__dirname,'../decision.html');
const MODEL='999666791b147e4d120ba9b7e10d9d1fc846ba171efbba73b2488ca56ce6f589';
async function fixture(t) {
  const root=await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(),'dc20-next-day-')));
  t.after(()=>fs.rm(root,{recursive:true,force:true}));
  const write=async(p,b)=>{const f=path.join(root,p);await fs.mkdir(path.dirname(f),{recursive:true});await fs.writeFile(f,b);return hash(b);};
  const json=(p,v)=>write(p,raw(v));
  await write('decision.html',await fs.readFile(SOURCE));
  const active={enabled:true,activation_id:'fixed-ridge',model_canonical_sha256:MODEL,effective_from_signal_date:'20260914'};
  const index={schema_version:'dc20_candidate_formal_profit_index_v1',activation_id:active.activation_id,model_canonical_sha256:MODEL,days:[]};
  const source_files=[],days=[];
  for(const [signal_date,exec_date,exit_date] of [['20260914','20260915','20260916'],['20260915','20260916','20260917']]) {
    const rows=[1,2].map(rank=>({ts_code:`00000${rank}.SZ`,name:`Company${rank}`,industry:'test',stage_transition:'2→3',promotion_rank:rank,candidate_rank:rank,candidate_score:-rank*0.01}));
    const p0={signal_date,exec_date,exit_date,rows};
    const p0Path=`outputs/decision/three_rank_top10_${signal_date}.json`,p0Sha=await json(p0Path,p0);
    const day={schema_version:'dc20_candidate_formal_profit_day_v1',status:'FROZEN_FORMAL_PROFIT_FORWARD_VALIDATION',
      signal_date,exec_date,exit_date,activation_id:active.activation_id,model_canonical_sha256:MODEL,
      effective_from_signal_date:'20260914',p0_file_sha256:p0Sha,p0_source:{path:p0Path,sha256:p0Sha},
      snapshot_file_sha256:'a'.repeat(64),snapshot_source:{path:`work/profit_1000_upgrade/candidate_natural_forward/day_${signal_date}.json`,sha256:'a'.repeat(64)},
      candidate_count:2,rows,candidate_slots:rows.map(r=>({...r,slot:r.candidate_rank,status:'PENDING_T',proxy_fill:null,net_return:null,slot_net_return:null})),
      entry_policy_id:'research_canonical_price_capacity_split_no_cap_v3',exit_policy_id:'dc20_exit_1000_limit_hold_20260912_v1',
      round_trip_cost_rate:0.0045,promotion_ranking_unchanged:true,historical_reranking_performed:false,model_retrained:false,actual_execution_claimed:false,
      prediction_generated_at_utc:`2026-09-${signal_date.slice(6)}T16:00:00+08:00`,
      pre_cas_freeze_at_utc:`2026-09-${signal_date.slice(6)}T16:01:00+08:00`,
      projection_generated_at_utc:`2026-09-${signal_date.slice(6)}T16:02:00+08:00`,formal_publication_deadline_utc:`2026-09-${exec_date.slice(6)}T09:20:00+08:00`};
    const p=`outputs/decision/candidate_profit_v1/day_${signal_date}.json`;
    index.days.push({signal_date,path:p,sha256:await json(p,day),p0_file_sha256:p0Sha,snapshot_file_sha256:day.snapshot_file_sha256});
    days.push(day);
  }
  const rows='signal_date,exec_date,exit_date,ts_code,promotion_rank\n'+days.flatMap(d=>d.rows.map(r=>`${d.signal_date},${d.exec_date},${d.exit_date},${r.ts_code},${r.promotion_rank}`)).join('\n')+'\n';
  const rowSha=await write(ROWS_PATH,Buffer.from(rows));source_files.push({path:ROWS_PATH,sha256:rowSha});
  const summary={as_of_date:'20260915',rows_path:ROWS_PATH,rows_sha256:rowSha};
  const sumSha=await json(SUMMARY_PATH,summary);source_files.push({path:SUMMARY_PATH,sha256:sumSha});
  const dailyPath='data/market/raw/2026/20260915/daily.csv',limitPath='data/market/raw/2026/20260915/stk_limit.csv';
  source_files.push({path:dailyPath,sha256:await write(dailyPath,Buffer.from('ts_code,trade_date,close,pre_close,vol\n000001.SZ,20260915,11,10,100\n000002.SZ,20260915,9.5,10,100\n'))});
  source_files.push({path:limitPath,sha256:await write(limitPath,Buffer.from('ts_code,trade_date,up_limit\n000001.SZ,20260915,11\n000002.SZ,20260915,11\n'))});
  const window={schema_version:'dc20_compact_statistics_window_v1',start_signal_date:'20260910',report_signal_date:'20260915',
    promotion:{status:'READY',as_of_date:'20260915',source_summary:{path:SUMMARY_PATH,sha256:sumSha},rows_sha256:rowSha},
    profit:{status:'UNAVAILABLE',reason:'LEGACY_P1_SHADOW_ARCHIVED'},source_files};
  const revision={repository:'njedu2023-prog/DC20',branch:'main',head_sha:'b'.repeat(40),run_id:1,
    primary_d_signal_date:'20260915',published_at_utc:'2026-09-15T12:00:00Z',
    compact_statistics_window_url:WINDOW_PATH,compact_statistics_window_sha256:await json(WINDOW_PATH,window),
    candidate_profit_activation_url:ACTIVATION_PATH,candidate_profit_activation_sha256:await json(ACTIVATION_PATH,active),
    candidate_profit_index_url:INDEX_PATH,candidate_profit_index_sha256:await json(INDEX_PATH,index)};
  await json('revision.json',revision);
  const sync=async()=>{revision.compact_statistics_window_sha256=await json(WINDOW_PATH,window);revision.candidate_profit_index_sha256=await json(INDEX_PATH,index);await json('revision.json',revision);};
  return {root,window,revision,index,days,write,json,sync,dailyPath,limitPath,build:()=>build({root}),
    updateDay:async i=>{index.days[i].sha256=await json(index.days[i].path,days[i]);await sync();},
    updateMarket:async(p,content)=>{window.source_files.find(e=>e.path===p).sha256=await write(p,Buffer.from(content));await sync();}};
}
test('small published result preserves exact browser math, pending samples and legacy independence',async t=>{
  const f=await fixture(t),result=await f.build();
  assert.equal(result.promotion.status,'READY');assert.equal(result.profit.status,'READY');
  const p=result.promotion.cohorts,profit=result.profit.cohorts;
  assert.equal(p[0].count,2);assert.equal(p[0].valid,1);assert.equal(p[0].pending,1);assert.equal(p[0].missing,0);
  assert.equal(p[0].upRate,1);assert.ok(Math.abs(p[0].mean-0.1)<1e-12);
  assert.equal(p[1].upRate,0);assert.ok(Math.abs(p[1].mean+0.05)<1e-12);
  assert.equal(p[2].count,0);assert.equal(p[2].mean,null);assert.equal(p[3].valid,2);
  assert.ok(Math.abs(p[3].mean-0.025)<1e-12);assert.ok(Math.abs(p[3].median-0.025)<1e-12);
  for(let i=0;i<2;i++)for(const k of ['count','valid','up','pending','missing','upRate','mean','median'])assert.equal(p[i][k],profit[i][k]);
  assert.equal(profit[0].limitRate,1);assert.equal(profit[1].limitRate,0);
  assert.match(result.engine_sha256,/^[0-9a-f]{64}$/);assert.ok(raw(result).length<15000);
});
test('materialize binds output SHA and is deterministic without mutating source ledgers',async t=>{
  const f=await fixture(t),before=await fs.readFile(path.join(f.root,INDEX_PATH));
  await materialize({root:f.root});const out=await fs.readFile(path.join(f.root,OUTPUT_PATH));
  const rev=JSON.parse(await fs.readFile(path.join(f.root,'revision.json')));
  assert.equal(rev.next_day_statistics_url,OUTPUT_PATH);assert.equal(rev.next_day_statistics_sha256,hash(out));
  await materialize({root:f.root});assert.deepEqual(await fs.readFile(path.join(f.root,OUTPUT_PATH)),out);
  assert.deepEqual(await fs.readFile(path.join(f.root,INDEX_PATH)),before);
});
test('bad core SHA and mismatched report date fail before creating output',async t=>{
  const f=await fixture(t);await f.write(WINDOW_PATH,Buffer.from('{}'));
  await assert.rejects(materialize({root:f.root}),/SHA256 mismatch/);
  await assert.rejects(fs.readFile(path.join(f.root,OUTPUT_PATH)),{code:'ENOENT'});
  f.window.report_signal_date='20260914';await f.sync();await assert.rejects(f.build(),/report D mismatch/);
});
test('bad market SHA isolates panels as unavailable rather than showing zero or partial results',async t=>{
  const f=await fixture(t);await f.write(f.dailyPath,Buffer.from('corrupt'));
  const result=await f.build();for(const s of [result.promotion,result.profit]){assert.equal(s.status,'UNAVAILABLE');assert.match(s.reason,/SHA256 mismatch/);assert.equal(s.cohorts,undefined);}
});
test('missing unbound quotes retain missing counts and null statistics, while future T stays pending',async t=>{
  const f=await fixture(t);f.window.source_files=f.window.source_files.filter(e=>e.path!==f.dailyPath);await f.sync();
  const r=await f.build();for(const section of [r.promotion,r.profit])for(const c of section.cohorts.slice(0,2)){
    assert.equal(c.valid,0);assert.equal(c.missing,1);assert.equal(c.pending,1);assert.equal(c.mean,null);assert.equal(c.upRate,null);
  }
});
test('duplicate or wrong-date market rows are rejected even with a valid new SHA',async t=>{
  const f=await fixture(t);
  await f.updateMarket(f.dailyPath,'ts_code,trade_date,close,pre_close,vol\n000001.SZ,20260915,11,10,100\n000001.SZ,20260915,11,10,100\n');
  assert.match((await f.build()).promotion.reason,/duplicate/);
  await f.updateMarket(f.dailyPath,'ts_code,trade_date,close,pre_close,vol\n000001.SZ,20260916,11,10,100\n');
  assert.match((await f.build()).promotion.reason,/date\/code mismatch/);
});
test('non-finite, blank and suspended quotes do not become valid zero-return samples',async t=>{
  const f=await fixture(t);
  await f.updateMarket(f.dailyPath,'ts_code,trade_date,close,pre_close,vol\n000001.SZ,20260915,NaN,10,100\n000002.SZ,20260915,10,10,0\n');
  const r=await f.build();assert.equal(r.promotion.cohorts[3].missing,2);assert.equal(r.profit.cohorts[0].mean,null);assert.equal(r.profit.cohorts[1].limitVerified,0);
});
test('damaged candidate binding does not suppress valid promotion statistics',async t=>{
  const f=await fixture(t);await f.write(f.index.days[0].path,Buffer.from('{}'));
  const r=await f.build();assert.equal(r.promotion.status,'READY');assert.equal(r.profit.status,'UNAVAILABLE');assert.match(r.profit.reason,/SHA256 mismatch/);
});
test('missing candidate publication bindings preserve promotion and expose no invented profit values',async t=>{
  const f=await fixture(t);
  for(const key of ['candidate_profit_activation_url','candidate_profit_activation_sha256','candidate_profit_index_url','candidate_profit_index_sha256'])delete f.revision[key];
  await f.json('revision.json',f.revision);
  const r=await f.build();assert.equal(r.promotion.status,'READY');assert.equal(r.profit.status,'UNAVAILABLE');
  assert.equal(r.bindings.candidate_index_sha256,null);assert.equal(r.bindings.candidate_activation_sha256,null);assert.equal(r.profit.cohorts,undefined);
});
test('unavailable promotion with retained row manifest preserves the binding and independent profit section',async t=>{
  const f=await fixture(t),rowSha=f.window.source_files.find(e=>e.path===ROWS_PATH).sha256;
  f.window.promotion={status:'UNAVAILABLE',reason:'isolated promotion problem'};await f.sync();
  const r=await f.build();assert.equal(r.promotion.status,'UNAVAILABLE');assert.equal(r.profit.status,'READY');
  assert.equal(r.bindings.primary_rows_sha256,rowSha);assert.equal(r.as_of_date,f.revision.primary_d_signal_date);
});
test('wrong candidate D/T, repeated index date and altered original rank fail formal evidence checks',async t=>{
  const f=await fixture(t);f.days[0].exec_date='20260916';await f.updateDay(0);
  assert.match((await f.build()).profit.reason,/D\/T/);
  f.days[0].exec_date='20260915';f.days[0].rows[0].promotion_rank=9;await f.updateDay(0);
  assert.match((await f.build()).profit.reason,/rank\/order/);
  f.index.days.push(f.index.days[0]);await f.sync();assert.match((await f.build()).profit.reason,/duplicate key/);
});
test('explicit ineligible/absent second seat is never substituted with promotion rank 2',async t=>{
  const f=await fixture(t),d=f.days[0];d.schema_version='dc20_candidate_formal_profit_day_v2_eligible';
  d.original_rows=d.rows.map(r=>({ts_code:r.ts_code,name:r.name,industry:r.industry,promotion_rank:r.promotion_rank,board_stage:2,candidate_rank:null,candidate_score:null}));
  d.original_candidate_count=2;d.eligibility_policy_id='dc20_individual_observed_window_eligibility_20260922_v2';
  d.eligibility=d.rows.map((r,i)=>({ts_code:r.ts_code,promotion_rank:r.promotion_rank,eligible:i===0,reason:i===0?null:'INSUFFICIENT_OBSERVED_HISTORY',observed_bar_count:i===0?21:15,required_observed_bar_count:21}));
  d.rows=d.rows.slice(0,1);d.candidate_count=1;
  d.candidate_slots[1]={slot:2,status:'MISSING_CANDIDATE',ts_code:null,candidate_rank:null,candidate_score:null,promotion_rank:null,proxy_fill:null,net_return:null,slot_net_return:null};
  await f.updateDay(0);const r=await f.build();assert.equal(r.profit.status,'READY');
  assert.equal(r.profit.cohorts[1].valid,0);assert.equal(r.profit.cohorts[1].missing,1);assert.equal(r.profit.cohorts[1].pending,1);assert.equal(r.profit.cohorts[1].mean,null);
});
test('late/replaced frozen projections do not enter the fast aggregate',async t=>{
  const f=await fixture(t);f.days[0].projection_generated_at_utc='2026-09-15T09:25:00+08:00';await f.updateDay(0);
  const r=await f.build();assert.equal(r.promotion.status,'READY');assert.match(r.profit.reason,/freeze time invalid/);
});
