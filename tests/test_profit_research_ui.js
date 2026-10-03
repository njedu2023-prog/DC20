// Dependency-free DOM smoke/regression tests for the published research page.
// Run with: node tests/test_profit_research_ui.js
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const html = fs.readFileSync(path.join(__dirname, '../outputs/decision/profit_research/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/\binit\(\);\s*$/, '');

function page(dataset) {
  const elements = new Map();
  const document = {getElementById(id) {
    if (!elements.has(id)) elements.set(id, {innerHTML: '', textContent: '', value: '', disabled: false,
      setAttribute() {}, querySelectorAll() {return [];}});
    return elements.get(id);
  }};
  const context = vm.createContext({document});
  vm.runInContext(script, context);
  context.fixture = dataset;
  vm.runInContext('data=fixture', context);
  return {elements, context, run: code => vm.runInContext(code, context), get: id => document.getElementById(id)};
}
function record(date, code, profitRank, promotionRank=1) {
  return {signal_date:date, ts_code:code, name:code, profit_rank:profitRank, promotion_rank:promotionRank,
    score:0.01, profit_eligible:true, feature_snapshot_present:true, training_pair_ready:true,
    provenance:'FORMAL_FROZEN_REFERENCE', features:{ret_2d:0.21},
    outcome:{status:'SETTLED_1000_LIMIT_HOLD_MINUTE_PROXY',proxy_fill:1,net_return:0.02},
    research_extensions:{values:{minute_realized_vol:null},missing_reasons:{minute_realized_vol:'NOT_COLLECTED'}}};
}
function fixture() {
  return {days:[{date:'20260929',records:[record('20260929','older',1)]},
    {date:'20260930',records:[record('20260930','profit-second',2,1),record('20260930','profit-first',1,2),record('20260930','unranked',null,3)]}],
    training_gate:{mature_days:9}, issues:[], research_data_quality:{complete_core_extension_days:11,
      mature_extended_training_days:9,fields:[{key:'minute_realized_vol',definition:'分钟波动',unit:'比例'}]}};
}

test('legacy datasets render with honest unknown admission, not assumed zero', () => {
  const p=page(fixture());p.run('render()');
  assert.match(p.get('quality').innerHTML,/训练准入尚未登记/);
  assert.match(p.get('comparisonContent').innerHTML,/尚未生成/);
  assert.match(p.get('extensionQuality').innerHTML,/尚未采集/);
});
test('default date is latest and each evidence page contains one D date', () => {
  const p=page(fixture());p.run('render()');
  assert.match(p.get('selected').textContent,/D 2026-09-30/);
  assert.doesNotMatch(p.get('rows').innerHTML,/older/);
  assert.equal(p.get('nextPage').disabled,true);
  p.run('moveDay(-1)');
  assert.match(p.get('rows').innerHTML,/older/);
  assert.doesNotMatch(p.get('rows').innerHTML,/profit-first/);
  assert.equal(p.get('prevPage').disabled,true);
});
test('profit ascending remains the default, with missing rank last', () => {
  const p=page(fixture());p.run('render()');const rows=p.get('rows').innerHTML;
  assert.ok(rows.indexOf('profit-first')<rows.indexOf('profit-second'));
  assert.ok(rows.indexOf('profit-second')<rows.indexOf('unranked'));
  assert.match(rows,/rank-badge promotion/);assert.match(rows,/rank-badge profit/);
});
test('global filters and evidence search keep their existing scopes', () => {
  const p=page(fixture());p.run('render()');const ranks=p.get('ranks').innerHTML;
  p.get('stockSearch').value='profit-first';p.run('renderRows(choose())');
  assert.match(p.get('selected').textContent,/匹配 1 \/ 3/);
  assert.equal(p.get('ranks').innerHTML,ranks);
  p.get('to').value='2026-09-29';p.run('render()');
  assert.match(p.get('selected').textContent,/D 2026-09-29/);
});
test('all three milestones remain horizontal and have progress semantics', () => {
  const p=page(fixture());p.run('render()');const gate=p.get('gate').innerHTML;
  assert.equal((gate.match(/role="progressbar"/g)||[]).length,3);
  for(const n of [60,120,250]) assert.match(gate,new RegExp('aria-valuemax="'+n+'"'));
  assert.match(html,/#gate\{display:grid;grid-template-columns:repeat\(3,minmax\(0,1fr\)\)/);
});
test('readiness and enrichment show separate explicit scopes', () => {
  const f=fixture();f.research_data_quality.readiness={feature_complete_days:8,label_complete_days:7,training_admitted_days:0,reason:'时点核验未完成'};
  f.research_data_quality.enrichment={records_enriched:3,minute_complete_records:2,last_seal_recovered_records:1,historical_backfill_records:3,independently_verified_records:0};
  const p=page(f);p.run('render()');const q=p.get('quality').innerHTML;
  assert.match(q,/所选区间/);assert.match(q,/全历史/);assert.match(q,/训练准入<\/small><strong>0/);
  assert.match(q,/分钟完整 2/);assert.match(q,/历史补采不冒充盘前冻结/);
});
test('missing reasons are grouped and untrusted text is escaped', () => {
  const p=page(fixture());p.context.entries=[{value:null,reason:'INCOMPLETE_MINUTE_SESSION'},{value:null,reason:'INCOMPLETE_MINUTE_SESSION'},{value:null,reason:'<img src=x>'},{value:0,reason:null}];
  const h=p.run('reasonDetails(entries,0)');assert.match(h,/分钟交易时段不完整 · 2条/);
  assert.match(h,/&lt;img src=x&gt;/);assert.doesNotMatch(h,/<img/);
});
test('individual evidence distinguishes backfills and preserves provenance', () => {
  const f=fixture();f.days[1].records[0].research_enrichment={payload:{rule_version:'minutes-v1',field_sources:{minute_realized_vol:{collection_kind:'HISTORICAL_BACKFILL',observed_at_utc:'2026-10-04T00:00:00Z'}}}};
  const p=page(f);p.run('render()');const rows=p.get('rows').innerHTML;
  assert.match(rows,/包含历史补采/);assert.match(rows,/minutes-v1/);assert.match(rows,/2026-10-04T00:00:00Z/);
  assert.match(rows,/不改变原冻结输入/);
});
test('comparison is collapsed, all-history and explicitly exploratory', () => {
  const f=fixture();f.research_comparison={status:'EXPLORATORY_INSUFFICIENT_DAYS',counts:{eligible_days:9,excluded_days:3,eligible_candidates:75},strategies:[{label:'盈利前2',observed_days:9,mean_daily_slot_return:0.02,filled_trades:14,selected_slots:18,mean_filled_trade_net_return:0.03,trade_win_rate:0.6}],comparisons:[{label:'盈利前2 vs 晋级前2',paired_days:9,mean_daily_difference:0.01,positive_difference_days:6,bootstrap:{status:'INSUFFICIENT_DAYS',lower:null,upper:null}}]};
  const p=page(f);p.run('render()');const c=p.get('comparisonContent').innerHTML;
  assert.match(html,/<section id="comparison"><details class="fold">/);
  assert.match(c,/样本不足/);assert.match(c,/\+1\.00 个百分点/);
  assert.match(c,/不随上方筛选变化/);assert.match(c,/未成交不补位/);assert.match(c,/暂不展示/);
});
test('no-fill stays non-trade in evidence, never masquerades as zero return', () => {
  const f=fixture();f.days[1].records[0].outcome={status:'NO_FILL_CAPACITY',proxy_fill:0,net_return:null};
  const p=page(f);p.get('stockSearch').value='profit-second';p.run('render()');const rows=p.get('rows').innerHTML;
  assert.match(rows,/未买入，不计成交收益/);assert.doesNotMatch(rows,/0\.00%/);
});
