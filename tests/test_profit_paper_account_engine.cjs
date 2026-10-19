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
  const s = summary([row('20261019',1,{exec_date:'20261021',exit_date:'20261022',actual_exit_date:'20261022',actual_exit_time:'2026-10-22T10:00:00+08:00'})], '20261022');
  const out=replay(s,quotesFor(s));
  assert.equal(out.blockers[0].code,'INVALID_ENTRY_DATE');
  assert.equal(out.last_valid_date,'20261020');
});
const CAL = ['20261019', '20261020', '20261021', '20261022', '20261023', '20261026', '20261027', '20261028', '20261029', '20261030', '20261102', '20261103'];
function row(signal, rank = 1, options = {}) {
  const entry = CAL[CAL.indexOf(signal) + 1] || '20261104';
  const exit = CAL[CAL.indexOf(entry) + 1] || '20261105';
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
function summary(rows, asOf = '20261021') {
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
function settled(source, rank = 1, signal = '20261019') { return source.groups[`candidate_top${rank}`].daily_sequence.find(r => r.signal_date === signal); }

test('integer lots, total fee reserve, gain, exact cash/P&L identity', () => {
  const s = summary([row('20261019')]); const out = replay(s, quotesFor(s));
  const trade = out.trades.find(t => t.id === '20261019:R1');
  assert.equal(out.initial_capital, 735000); assert.equal(out.per_trade_budget, 100000);
  assert.equal(trade.shares, 9900); assert.equal(trade.notional, 99000); assert.equal(trade.costs, 445.5);
  assert.equal(trade.status, 'CLOSED'); near(trade.net_pnl, 9454.5); near(out.metrics.nav, 744454.5);
  assert.equal(out.complete, true); assert.equal(out.blocked, false); assert.equal(out.metrics.win_rate, 1);
  for (const d of out.days) near(d.nav, 735000 + d.realized_pnl + d.unrealized_pnl);
  near(out.rank_contributions.reduce((sum, r) => sum + r.total_pnl, 0), out.metrics.total_pnl);
});
test('both ranks receive their own fixed 100k budget, within the reset 735k account', () => {
  const s = summary([row('20261019', 1), row('20261019', 2)]);
  const out = replay(s, quotesFor(s));
  const pair = out.trades.filter(t => t.signal_date === '20261019');
  assert.equal(pair.length, 2);
  for (const trade of pair) {
    assert.equal(trade.shares, 9900);
    assert.equal(trade.notional + trade.costs, 99445.5);
    assert.ok(trade.notional + trade.costs <= 100000);
  }
  near(out.days[1].cash, 536109);
  near(out.metrics.nav, 753909);
  assert.equal(out.initial_capital, 735000);
  assert.ok(out.assumptions.some(value => value.includes('盈1、盈2各10万元')));
});
test('a single lot exceeding the 100k fee-inclusive budget is skipped without cost', () => {
  const s = summary([row('20261019', 1, {entry_price: 2000, exit_price: 2100})]);
  const out = replay(s, quotesFor(s));
  const trade = out.trades.find(t => t.id === '20261019:R1');
  assert.equal(trade.status, 'BUDGET_BELOW_ONE_LOT');
  assert.match(trade.reason, /10万元/);
  assert.equal(trade.costs, 0); assert.equal(trade.shares, 0);
  assert.equal(out.metrics.nav, 735000); assert.equal(out.metrics.filled_trades, 0);
});
test('MTM losses affect daily drawdown before sale; cost is not deducted twice', () => {
  const s = summary([row('20261019', 1, {exit_price: 8})]);
  const q = quotesFor(s, {'20261020': {'000001.SZ': {close: 9}}, '20261021': {'000001.SZ': {pre_close: 9, close: 8}}});
  const out = replay(s, q); near(out.days[1].nav, 724654.5); near(out.metrics.nav, 714754.5);
  near(out.metrics.max_drawdown, 20245.5 / 735000, 1e-12); near(out.metrics.max_drawdown_amount, 20245.5);
  assert.equal(out.metrics.longest_loss_streak, 1); assert.equal(out.metrics.total_costs, 445.5);
});
test('no fill is excluded from filled-trade denominator, no fees or synthetic zero trade', () => {
  const s = summary([]); const out = replay(s, new Map());
  assert.equal(out.metrics.nav, 735000); assert.equal(out.metrics.filled_trades, 0);
  assert.equal(out.metrics.closed_trades, 0); assert.equal(out.metrics.win_rate, null); assert.equal(out.metrics.total_costs, 0);
  assert.equal(out.trades[0].status, 'NO_FILL'); assert.equal(out.trades[0].net_pnl, null);
});
test('known entry awaiting T+1 is marked to market, not zeroed or prematurely sold', () => {
  const s = summary([row('20261019', 1, {status: 'PENDING_T1', actual_exit_date: null, actual_exit_time: null, exit_price: null, slot_net_return: null})], '20261020');
  const out = replay(s, quotesFor(s, {'20261020': {'000001.SZ': {close: 11}}}));
  assert.equal(out.blocked, false); assert.equal(out.metrics.open_trades, 1); near(out.metrics.unrealized_pnl, 9454.5);
  assert.equal(out.metrics.realized_pnl, 0); assert.equal(out.metrics.closed_trades, 0);
});
test('due missing exit stops entire day, leaving the last verified holding intact', () => {
  const s = summary([row('20261019', 1, {status: 'PENDING_EXIT_MISSING_MINUTES', actual_exit_date: null, actual_exit_time: null, exit_price: null, slot_net_return: null})]);
  const out = replay(s, quotesFor(s)); assert.equal(out.blocked, true); assert.equal(out.last_valid_date, '20261020');
  assert.equal(out.blockers[0].code, 'UNRESOLVED_EXIT'); assert.equal(out.metrics.open_trades, 1); assert.equal(out.metrics.closed_trades, 0);
  assert.equal(out.trades.find(t => t.id === '20261020:R1').status, 'DEFERRED_AFTER_BLOCKER');
});
test('missing frozen day is explicitly a subset, never silently a no-fill', () => {
  const s = summary([row('20261019')]); const missing = settled(s, 2);
  Object.assign(missing, {status: 'MISSING_D_PUBLICATION', proxy_fill: null, exec_date: null, ts_code: null});
  s.missing_signal_dates = ['20261019']; s.coverage_complete = false;
  const out = replay(s, quotesFor(s)); assert.equal(out.complete, false); assert.equal(out.blocked, false);
  assert.equal(out.metrics.missing_trades, 1); assert.equal(out.metrics.closed_trades, 1);
  assert.deepEqual(Array.from(out.missing_signal_dates), ['20261019']);
});
test('cash is checked before same-day exits; no financing or partial stake', () => {
  const exit = '20261027';
  const rows = [];
  for (const signal of CAL.slice(0, 6)) for (const rank of [1,2]) rows.push(row(signal, rank, {
    ts_code: `${String(CAL.indexOf(signal) * 2 + rank).padStart(6, '0')}.SZ`,
    actual_exit_date: exit, actual_exit_time: '2026-10-27T10:00:00+08:00', exit_price: 10,
    ...(signal === '20261026' ? {actual_exit_date: '20261028', actual_exit_time: '2026-10-28T10:00:00+08:00'} : {})
  }));
  const s = summary(rows, exit); const out = replay(s, quotesFor(s));
  assert.equal(out.metrics.insufficient_cash_trades, 5); assert.equal(out.metrics.closed_trades, 7);
  assert.equal(out.metrics.max_open_positions, 7); assert.equal(out.blocked, false);
  near(out.metrics.nav, 731881.5); assert.ok(out.days.every(d => d.cash >= 0));
  assert.equal(out.trades.find(t => t.id === '20261022:R1').status, 'CLOSED');
  assert.equal(out.trades.find(t => t.id === '20261022:R2').status, 'INSUFFICIENT_CASH');
  assert.match(out.trades.find(t => t.id === '20261022:R2').reason, /10 万元/);
});
test('same stock bought on multiple dates remains separate lots and T+1 exits', () => {
  const s = summary([row('20261019'), row('20261020', 1)], '20261022');
  const out = replay(s, quotesFor(s)); assert.equal(out.metrics.closed_trades, 2);
  assert.equal(out.trades.filter(t => t.status === 'CLOSED' && t.ts_code === '000001.SZ').length, 2);
  near(out.metrics.total_pnl, 2 * 9454.5);
  assert.equal(out.metrics.max_open_positions, 1);
  assert.equal(out.metrics.max_open_positions_intraday, 2);
  near(out.metrics.max_committed_cost_intraday, 198891);
});
test('unknown fill is not converted into zero return or assumed entry', () => {
  const s = summary([row('20261019', 1, {proxy_fill: null, status: 'PENDING_ENTRY', slot_net_return: null})]);
  const out = replay(s, quotesFor(s)); assert.equal(out.blockers[0].code, 'UNRESOLVED_ENTRY');
  assert.equal(out.last_valid_date, '20261019'); assert.equal(out.metrics.filled_trades, 0);
});
test('missing close and duplicate quote rows stop curve, no stale-price carry forward', () => {
  const s = summary([row('20261019')]); const q = quotesFor(s); q.set('20261020', []);
  let out = replay(s, q); assert.equal(out.blockers[0].code, 'MISSING_QUOTE'); assert.equal(out.last_valid_date, '20261019');
  const d = quotesFor(s); d.get('20261020').push({...d.get('20261020')[0]});
  out = replay(s, d); assert.equal(out.blockers[0].code, 'MISSING_QUOTE');
});
test('unverified corporate action blocks before exit, even when settled return exists', () => {
  const s = summary([row('20261019')]); const q = quotesFor(s, {'20261021': {'000001.SZ': {pre_close: 9.99}}});
  const out = replay(s, q); assert.equal(out.blockers[0].code, 'CORPORATE_ACTION_UNVERIFIED');
  assert.equal(out.last_valid_date, '20261020'); assert.equal(out.metrics.closed_trades, 0);
});
test('invalid same-day entry/exit cannot bypass A-share T+1', () => {
  const s = summary([row('20261019', 1, {exit_date: '20261020', actual_exit_date: '20261020', actual_exit_time: '2026-10-20T10:00:00+08:00'})]);
  const out = replay(s, quotesFor(s)); assert.equal(out.blockers[0].code, 'INVALID_T1'); assert.equal(out.last_valid_date, '20261019');
});
test('scheduled T+1 is the next trading session, while verified actual exit may defer', () => {
  const s = summary([row('20261019', 1, {exit_date: '20261022', actual_exit_date: '20261022', actual_exit_time: '2026-10-22T10:00:00+08:00'})], '20261022');
  const out = replay(s, quotesFor(s)); assert.equal(out.blockers[0].code, 'INVALID_T1'); assert.equal(out.last_valid_date, '20261019');
  const valid = summary([row('20261019', 1, {actual_exit_date: '20261022', actual_exit_time: '2026-10-22T10:00:00+08:00'})], '20261022');
  const result = replay(valid, quotesFor(valid)); assert.equal(result.blocked, false); assert.equal(result.metrics.closed_trades, 1);
});
test('null settled return is not treated as zero; price-vs-return mismatch fails closed', () => {
  for (const value of [null, 0]) {
    const s = summary([row('20261019', 1, {slot_net_return: value})]); const out = replay(s, quotesFor(s));
    assert.equal(out.last_valid_date, '20261020'); assert.equal(out.blockers[0].code, value === null ? 'UNRESOLVED_EXIT' : 'RETURN_PRICE_MISMATCH');
  }
});
test('future entry never consumes current cash or uses future quotes', () => {
  const s = summary([row('20261021', 1)], '20261021'); const q = quotesFor(s);
  q.set('20261022', [{ts_code: '000001.SZ', trade_date: '20261022', close: 999, pre_close: 10}]);
  const out = replay(s, q); assert.equal(out.metrics.nav, 735000); assert.equal(out.metrics.filled_trades, 0);
  assert.equal(out.trades.find(t => t.id === '20261021:R1').status, 'FUTURE_ENTRY');
});
test('input objects remain unchanged and repeated replay is deterministic', () => {
  const s = summary([row('20261019')]); const q = quotesFor(s); const before = JSON.stringify(s);
  const first = replay(s, q); const second = replay(s, q); assert.deepEqual(first, second); assert.equal(JSON.stringify(s), before);
});
test('company names use same-D rank/code identities and stay distinct across renamed lots', () => {
  const s = summary([row('20261019'), row('20261020', 1)], '20261022');
  const identities = new Map(['20261019','20261020'].map((date, i) => [`${date}:R1`, {
    signal_date:date,rank:1,ts_code:'000001.SZ',name:i ? ' 新名称 ' : '旧名称',
    source_path:`outputs/decision/candidate_profit_v1/day_${date}.json`,source_sha256:'a'.repeat(64)
  }]));
  const before = JSON.stringify([...identities]);
  const out = replay(s,quotesFor(s),identities);
  const old = out.trades.find(t=>t.id === '20261019:R1'), renamed = out.trades.find(t=>t.id === '20261020:R1');
  assert.equal(old.name,'旧名称'); assert.equal(renamed.name,'新名称');
  assert.equal(old.name_source.signal_date,'20261019');
  assert.equal(renamed.name_source.path,'outputs/decision/candidate_profit_v1/day_20261020.json');
  assert.equal(out.trades.find(t=>t.id === '20261019:R2').name,null);
  assert.equal(JSON.stringify([...identities]),before);
  near(out.metrics.nav,replay(s,quotesFor(s)).metrics.nav);
});
test('wrong-day, rank or ticker company identity fails without cross-day name substitution', () => {
  const s=summary([row('20261019')]);
  for (const change of [{signal_date:'20261020'},{rank:2},{ts_code:'000002.SZ'}]) {
    const identities=new Map([['20261019:R1',{signal_date:'20261019',rank:1,ts_code:'000001.SZ',name:'不能错配',...change}]]);
    assert.throws(()=>replay(s,quotesFor(s),identities),/公司名称与同日冻结股票身份不一致/);
  }
});

function withLegacyHistory(source) {
  // Deliberately include old profits, losses, a missing freeze, and an unresolved
  // position whose entry/exit would otherwise consume cash inside the new period.
  const legacyDates = ['20260914', '20260915', '20260921', '20261016'];
  source.signal_dates = [...legacyDates, ...source.signal_dates];
  for (const rank of [1, 2]) {
    source.groups[`candidate_top${rank}`].daily_sequence.unshift(
      row('20260914', rank, {exec_date:'20260915', exit_date:'20260916', actual_exit_date:'20260916',
        actual_exit_time:'2026-09-16T10:00:00+08:00', exit_price:30}),
      row('20260915', rank, {exec_date:'20260916', exit_date:'20260917', actual_exit_date:'20260917',
        actual_exit_time:'2026-09-17T10:00:00+08:00', exit_price:1}),
      row('20260921', rank, {status:'MISSING_D_PUBLICATION', proxy_fill:null, exec_date:null, ts_code:null}),
      row('20261016', rank, {exec_date:'20261019', exit_date:'20261020', status:'PENDING_EXIT_MISSING_MINUTES',
        actual_exit_date:null, actual_exit_time:null, exit_price:null, slot_net_return:null})
    );
  }
  source.missing_signal_dates = ['20260921'];
  source.coverage_complete = false;
  return source;
}

test('reset ignores pre-start gains, losses, missing freezes and unresolved positions', () => {
  const clean = summary([row('20261019',1), row('20261019',2)]);
  const legacy = withLegacyHistory(summary([row('20261019',1), row('20261019',2)]));
  const baseline = replay(clean, quotesFor(clean));
  const out = replay(legacy, quotesFor(legacy));
  assert.equal(out.start_signal_date, '20261019');
  assert.deepEqual(out.days, baseline.days);
  assert.deepEqual(out.metrics, baseline.metrics);
  assert.deepEqual(out.trades, baseline.trades);
  assert.equal(out.blocked, false);
  assert.equal(out.complete, true);
  assert.deepEqual(Array.from(out.missing_signal_dates), []);
  assert.ok(out.trades.every(trade => trade.signal_date >= '20261019'));
});

test('legacy global coverage failure does not invalidate a complete reset cohort', () => {
  const s = summary([row('20261019')]);
  s.coverage_complete = false;
  s.missing_signal_dates = ['20260921', '20261016'];
  const out = replay(s, quotesFor(s));
  assert.equal(out.complete, true);
  assert.equal(out.blocked, false);
  assert.equal(out.start_signal_date, '20261019');
  assert.equal(out.metrics.missing_trades, 0);
  assert.deepEqual(Array.from(out.missing_signal_dates), []);
});

test('D October 19 freezes only; first entry is T October 20 and start NAV is 735k', () => {
  const frozen = summary([row('20261019',1), row('20261019',2)], '20261019');
  const atD = replay(frozen, quotesFor(frozen));
  assert.equal(atD.start_signal_date, '20261019');
  assert.equal(atD.waiting_for_start, false);
  assert.equal(atD.days.length, 1);
  assert.equal(atD.days[0].date, '20261019');
  assert.equal(atD.days[0].nav, 735000);
  assert.equal(atD.days[0].cash, 735000);
  assert.equal(atD.metrics.filled_trades, 0);
  assert.ok(atD.trades.every(trade => trade.status === 'FUTURE_ENTRY' && trade.entry_date === '20261020'));
  const entered = summary([row('20261019',1), row('20261019',2)], '20261020');
  const atT = replay(entered, quotesFor(entered));
  assert.equal(atT.metrics.filled_trades, 2);
  assert.equal(atT.metrics.open_trades, 2);
  assert.equal(atT.metrics.closed_trades, 0);
  assert.equal(atT.days[0].nav, 735000);
  assert.equal(atT.days[1].date, '20261020');
  assert.ok(atT.trades.filter(trade => trade.status === 'OPEN').every(trade => trade.signal_date === '20261019'));
});

test('fixed 100k budgets do not compound or increase after realized gains', () => {
  const rows = [1,2].flatMap(rank => [row('20261019',rank), row('20261021',rank)]);
  const s = summary(rows, '20261023');
  const out = replay(s, quotesFor(s));
  const closed = out.trades.filter(trade => trade.status === 'CLOSED');
  assert.equal(closed.length, 4);
  for (const trade of closed) {
    assert.equal(trade.shares, 9900);
    assert.equal(trade.notional + trade.costs, 99445.5);
    assert.ok(trade.notional + trade.costs <= 100000);
  }
  near(out.metrics.nav, 735000 + 4 * 9454.5);
  assert.equal(out.per_trade_budget, 100000);
});

test('before reset date returns waiting baseline with no fabricated days or carried trades', () => {
  const s = withLegacyHistory(summary([], '20261016'));
  const out = replay(s, quotesFor(s));
  assert.equal(out.start_signal_date, '20261019');
  assert.equal(out.as_of_date, '20261016');
  assert.equal(out.waiting_for_start, true);
  assert.equal(out.initial_capital, 735000);
  assert.equal(out.per_trade_budget, 100000);
  assert.equal(out.days.length, 0);
  assert.equal(out.trades.length, 0);
  assert.equal(out.last_valid_date, null);
  assert.equal(out.blocked, false);
  assert.equal(out.complete, false);
  assert.equal(out.metrics.nav, 735000);
  assert.equal(out.metrics.cash, 735000);
  assert.equal(out.metrics.market_value, 0);
  assert.equal(out.metrics.filled_trades, 0);
  assert.equal(out.metrics.open_trades, 0);
  assert.equal(out.metrics.closed_trades, 0);
  assert.equal(out.metrics.missing_trades, 0);
  assert.deepEqual(Array.from(out.missing_signal_dates), []);
});

