const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '..', 'decision.html'), 'utf8');
const code = html.match(/\/\/ BEGIN DC20 PAPER ACCOUNT ENGINE[\s\S]*?\/\/ END DC20 PAPER ACCOUNT ENGINE/)[0];
const context = {module:{exports:{}}, Map};
vm.runInNewContext(code, context);
const replay = context.module.exports.buildProfitPaperAccount;
const FEE = 0.0045;
test('entry must be the next trading day, not a selected later price', () => {
  const s = summary([row('20260914',1,{exec_date:'20260916',exit_date:'20260917',actual_exit_date:'20260917',actual_exit_time:'2026-09-17T10:00:00+08:00'})], '20260917');
  const out=replay(s,quotesFor(s));
  assert.equal(out.blockers[0].code,'INVALID_ENTRY_DATE');
  assert.equal(out.last_valid_date,'20260915');
});
const CAL = ['20260914', '20260915', '20260916', '20260917', '20260918', '20260921', '20260922', '20260923', '20260924', '20260928', '20260929', '20260930'];
function row(signal, rank = 1, options = {}) {
  const entry = CAL[CAL.indexOf(signal) + 1] || '20261008';
  const exit = CAL[CAL.indexOf(entry) + 1] || '20261009';
  const result = {signal_date: signal, slot: rank, candidate_rank: rank, ts_code: `${String(rank).padStart(6, '0')}.SZ`,
    exec_date: entry, exit_date: exit, actual_exit_date: exit, actual_exit_time: `${exit.slice(0,4)}-${exit.slice(4,6)}-${exit.slice(6,8)}T10:00:00+08:00`,
    status: 'SETTLED_1000_LIMIT_HOLD_MINUTE_PROXY', proxy_fill: 1, entry_price: 10, exit_price: 11, ...options};
  if (!Object.hasOwn(options, 'slot_net_return')) result.slot_net_return = result.exit_price / result.entry_price - 1 - FEE;
  return result;
}
function noFill(signal, rank) {
  return row(signal, rank, {status: 'NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED', proxy_fill: 0, actual_exit_date: null,
    actual_exit_time: null, exit_price: null, slot_net_return: 0});
}
function summary(rows, asOf = '20260916') {
  const dates = CAL.filter(d => d <= asOf);
  const groups = {};
  for (const rank of [1, 2]) groups[`candidate_top${rank}`] = {daily_sequence: dates.map(d => rows.find(r => r.signal_date === d && r.slot === rank) || noFill(d, rank))};
  return {as_of_date: asOf, signal_dates: dates, groups, round_trip_cost_rate: FEE, coverage_complete: true, missing_signal_dates: []};
}
function quotesFor(source, overrides = {}) {
  const codes = new Set(Object.values(source.groups).flatMap(g => g.daily_sequence.map(r => r.ts_code)).filter(Boolean));
  return new Map(source.signal_dates.map(d => [d, [...codes].map(code => ({ts_code: code, trade_date: d, close: 10, pre_close: 10,
    ...(overrides[d]?.[code] || {})}))]));
}
function near(actual, expected, tolerance = 0.011) { assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} != ${expected}`); }
function settled(source, rank = 1, signal = '20260914') { return source.groups[`candidate_top${rank}`].daily_sequence.find(r => r.signal_date === signal); }

test('integer lots, total fee reserve, gain, exact cash/P&L identity', () => {
  const s = summary([row('20260914')]); const out = replay(s, quotesFor(s));
  const trade = out.trades.find(t => t.id === '20260914:R1');
  assert.equal(trade.shares, 9900); assert.equal(trade.notional, 99000); assert.equal(trade.costs, 445.5);
  assert.equal(trade.status, 'CLOSED'); near(trade.net_pnl, 9454.5); near(out.metrics.nav, 1009454.5);
  assert.equal(out.complete, true); assert.equal(out.blocked, false); assert.equal(out.metrics.win_rate, 1);
  for (const d of out.days) near(d.nav, 1000000 + d.realized_pnl + d.unrealized_pnl);
  near(out.rank_contributions.reduce((sum, r) => sum + r.total_pnl, 0), out.metrics.total_pnl);
});
test('MTM losses affect daily drawdown before sale; cost is not deducted twice', () => {
  const s = summary([row('20260914', 1, {exit_price: 8})]);
  const q = quotesFor(s, {'20260915': {'000001.SZ': {close: 9}}, '20260916': {'000001.SZ': {pre_close: 9, close: 8}}});
  const out = replay(s, q); near(out.days[1].nav, 989654.5); near(out.metrics.nav, 979754.5);
  near(out.metrics.max_drawdown, 0.0202455, 1e-12); near(out.metrics.max_drawdown_amount, 20245.5);
  assert.equal(out.metrics.longest_loss_streak, 1); assert.equal(out.metrics.total_costs, 445.5);
});
test('no fill is excluded from filled-trade denominator, no fees or synthetic zero trade', () => {
  const s = summary([]); const out = replay(s, new Map());
  assert.equal(out.metrics.nav, 1000000); assert.equal(out.metrics.filled_trades, 0);
  assert.equal(out.metrics.closed_trades, 0); assert.equal(out.metrics.win_rate, null); assert.equal(out.metrics.total_costs, 0);
  assert.equal(out.trades[0].status, 'NO_FILL'); assert.equal(out.trades[0].net_pnl, null);
});
test('known entry awaiting T+1 is marked to market, not zeroed or prematurely sold', () => {
  const s = summary([row('20260914', 1, {status: 'PENDING_T1', actual_exit_date: null, actual_exit_time: null, exit_price: null, slot_net_return: null})], '20260915');
  const out = replay(s, quotesFor(s, {'20260915': {'000001.SZ': {close: 11}}}));
  assert.equal(out.blocked, false); assert.equal(out.metrics.open_trades, 1); near(out.metrics.unrealized_pnl, 9454.5);
  assert.equal(out.metrics.realized_pnl, 0); assert.equal(out.metrics.closed_trades, 0);
});
test('due missing exit stops entire day, leaving the last verified holding intact', () => {
  const s = summary([row('20260914', 1, {status: 'PENDING_EXIT_MISSING_MINUTES', actual_exit_date: null, actual_exit_time: null, exit_price: null, slot_net_return: null})]);
  const out = replay(s, quotesFor(s)); assert.equal(out.blocked, true); assert.equal(out.last_valid_date, '20260915');
  assert.equal(out.blockers[0].code, 'UNRESOLVED_EXIT'); assert.equal(out.metrics.open_trades, 1); assert.equal(out.metrics.closed_trades, 0);
  assert.equal(out.trades.find(t => t.id === '20260915:R1').status, 'DEFERRED_AFTER_BLOCKER');
});
test('missing frozen day is explicitly a subset, never silently a no-fill', () => {
  const s = summary([row('20260914')]); const missing = settled(s, 2);
  Object.assign(missing, {status: 'MISSING_D_PUBLICATION', proxy_fill: null, exec_date: null, ts_code: null});
  s.missing_signal_dates = ['20260914']; s.coverage_complete = false;
  const out = replay(s, quotesFor(s)); assert.equal(out.complete, false); assert.equal(out.blocked, false);
  assert.equal(out.metrics.missing_trades, 1); assert.equal(out.metrics.closed_trades, 1);
  assert.deepEqual(Array.from(out.missing_signal_dates), ['20260914']);
});
test('cash is checked before same-day exits; no financing or partial stake', () => {
  const exit = '20260922';
  const rows = [];
  for (const signal of CAL.slice(0, 6)) for (const rank of [1,2]) rows.push(row(signal, rank, {
    ts_code: `${String(CAL.indexOf(signal) * 2 + rank).padStart(6, '0')}.SZ`,
    actual_exit_date: exit, actual_exit_time: '2026-09-22T10:00:00+08:00', exit_price: 10,
    ...(signal === '20260921' ? {actual_exit_date: '20260923', actual_exit_time: '2026-09-23T10:00:00+08:00'} : {})
  }));
  const s = summary(rows, exit); const out = replay(s, quotesFor(s));
  assert.equal(out.metrics.insufficient_cash_trades, 2); assert.equal(out.metrics.closed_trades, 10);
  assert.equal(out.metrics.max_open_positions, 10); assert.equal(out.blocked, false);
  near(out.metrics.nav, 995545); assert.ok(out.days.every(d => d.cash >= 0));
});
test('same stock bought on multiple dates remains separate lots and T+1 exits', () => {
  const s = summary([row('20260914'), row('20260915', 1)], '20260917');
  const out = replay(s, quotesFor(s)); assert.equal(out.metrics.closed_trades, 2);
  assert.equal(out.trades.filter(t => t.status === 'CLOSED' && t.ts_code === '000001.SZ').length, 2);
  near(out.metrics.total_pnl, 2 * 9454.5);
  assert.equal(out.metrics.max_open_positions, 1);
  assert.equal(out.metrics.max_open_positions_intraday, 2);
  near(out.metrics.max_committed_cost_intraday, 198891);
});
test('unknown fill is not converted into zero return or assumed entry', () => {
  const s = summary([row('20260914', 1, {proxy_fill: null, status: 'PENDING_ENTRY', slot_net_return: null})]);
  const out = replay(s, quotesFor(s)); assert.equal(out.blockers[0].code, 'UNRESOLVED_ENTRY');
  assert.equal(out.last_valid_date, '20260914'); assert.equal(out.metrics.filled_trades, 0);
});
test('missing close and duplicate quote rows stop curve, no stale-price carry forward', () => {
  const s = summary([row('20260914')]); const q = quotesFor(s); q.set('20260915', []);
  let out = replay(s, q); assert.equal(out.blockers[0].code, 'MISSING_QUOTE'); assert.equal(out.last_valid_date, '20260914');
  const d = quotesFor(s); d.get('20260915').push({...d.get('20260915')[0]});
  out = replay(s, d); assert.equal(out.blockers[0].code, 'MISSING_QUOTE');
});
test('unverified corporate action blocks before exit, even when settled return exists', () => {
  const s = summary([row('20260914')]); const q = quotesFor(s, {'20260916': {'000001.SZ': {pre_close: 9.99}}});
  const out = replay(s, q); assert.equal(out.blockers[0].code, 'CORPORATE_ACTION_UNVERIFIED');
  assert.equal(out.last_valid_date, '20260915'); assert.equal(out.metrics.closed_trades, 0);
});
test('invalid same-day entry/exit cannot bypass A-share T+1', () => {
  const s = summary([row('20260914', 1, {exit_date: '20260915', actual_exit_date: '20260915', actual_exit_time: '2026-09-15T10:00:00+08:00'})]);
  const out = replay(s, quotesFor(s)); assert.equal(out.blockers[0].code, 'INVALID_T1'); assert.equal(out.last_valid_date, '20260914');
});
test('scheduled T+1 is the next trading session, while verified actual exit may defer', () => {
  const s = summary([row('20260914', 1, {exit_date: '20260917', actual_exit_date: '20260917', actual_exit_time: '2026-09-17T10:00:00+08:00'})], '20260917');
  const out = replay(s, quotesFor(s)); assert.equal(out.blockers[0].code, 'INVALID_T1'); assert.equal(out.last_valid_date, '20260914');
  const valid = summary([row('20260914', 1, {actual_exit_date: '20260917', actual_exit_time: '2026-09-17T10:00:00+08:00'})], '20260917');
  const result = replay(valid, quotesFor(valid)); assert.equal(result.blocked, false); assert.equal(result.metrics.closed_trades, 1);
});
test('null settled return is not treated as zero; price-vs-return mismatch fails closed', () => {
  for (const value of [null, 0]) {
    const s = summary([row('20260914', 1, {slot_net_return: value})]); const out = replay(s, quotesFor(s));
    assert.equal(out.last_valid_date, '20260915'); assert.equal(out.blockers[0].code, value === null ? 'UNRESOLVED_EXIT' : 'RETURN_PRICE_MISMATCH');
  }
});
test('future entry never consumes current cash or uses future quotes', () => {
  const s = summary([row('20260916', 1)], '20260916'); const q = quotesFor(s);
  q.set('20260917', [{ts_code: '000001.SZ', trade_date: '20260917', close: 999, pre_close: 10}]);
  const out = replay(s, q); assert.equal(out.metrics.nav, 1000000); assert.equal(out.metrics.filled_trades, 0);
  assert.equal(out.trades.find(t => t.id === '20260916:R1').status, 'FUTURE_ENTRY');
});
test('input objects remain unchanged and repeated replay is deterministic', () => {
  const s = summary([row('20260914')]); const q = quotesFor(s); const before = JSON.stringify(s);
  const first = replay(s, q); const second = replay(s, q); assert.deepEqual(first, second); assert.equal(JSON.stringify(s), before);
});
