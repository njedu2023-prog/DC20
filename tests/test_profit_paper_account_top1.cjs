'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {buildTop1RealizedAccount} = require('../scripts/top1_realized_paper_account.cjs');

const dates = ['20260914','20260915','20260916','20260917','20260918','20260921','20260922'];
const fee = 0.0045;
const missing = date => ({signal_date:date,slot:1,status:'MISSING_D_PUBLICATION'});
function settled(i, price, extra = {}) {
  const exit = dates[i + 2];
  return {signal_date:dates[i],slot:1,candidate_rank:1,proxy_fill:1,ts_code:'600000.SH',
    status:'SETTLED_1000_LIMIT_HOLD_MINUTE_PROXY',exec_date:dates[i+1],exit_date:exit,
    actual_exit_date:exit,actual_exit_time:exit.replace(/^(....)(..)(..)$/, '$1-$2-$3T10:00:00+08:00'),
    entry_price:10,exit_price:price,slot_net_return:price/10-1-fee,...extra};
}
function fixture(orders) {
  const rows = dates.map(d => orders.find(row => row.signal_date === d) || missing(d));
  const summary = {as_of_date:dates.at(-1),signal_dates:dates,round_trip_cost_rate:fee,
    groups:{candidate_top1:{daily_sequence:rows},candidate_top2:{daily_sequence:[settled(0,1000)]}}};
  const quotes = new Map(dates.map(d=>[d,[{trade_date:d,ts_code:'600000.SH',pre_close:10,close:10}]]));
  return {summary,quotes};
}
test('only the first frozen slot is settled with fee-inclusive 100k lots and cent-rounded amounts', () => {
  const {summary,quotes} = fixture([settled(0,11),settled(1,8)]);
  summary.groups.candidate_top1.daily_sequence.unshift({...settled(0,90),signal_date:'20260911'});
  const result = buildTop1RealizedAccount(summary,quotes);
  assert.deepEqual(result.metrics,{cumulative_pnl:-10791,max_profit:9454.5,max_loss:-20245.5});
  assert.equal(result.trades[0].shares,9900);
  assert.equal(result.trades[0].notional,99000);
  assert.equal(result.trades[0].costs,445.5);
  assert.equal(result.start_signal_date,'20260914');
  assert.equal(result.trades.length,dates.length);
  assert.equal(result.closed_trades,2);
  assert.equal(result.last_exit_date,'20260917');
});
test('pending exits, frozen gaps and no-fills leave later trades independently collectible', () => {
  const {summary,quotes} = fixture([
    {...settled(0,11),status:'PENDING_EXIT_MISSING_DAILY',actual_exit_date:null,actual_exit_time:null,exit_price:null,slot_net_return:null},
    {signal_date:dates[1],slot:1,status:'NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED',proxy_fill:0,slot_net_return:0},
    settled(3,11)
  ]);
  assert.equal(summary.groups.candidate_top1.daily_sequence[2].status,'MISSING_D_PUBLICATION');
  const out = buildTop1RealizedAccount(summary,quotes);
  assert.deepEqual(out.metrics,{cumulative_pnl:9454.5,max_profit:9454.5,max_loss:null});
  assert.equal(out.closed_trades,1);
  assert.equal(out.trades[0].net_pnl,null);
  assert.equal(out.trades[1].status,'NO_FILL');
  assert.equal(out.trades[1].net_pnl,null);
  assert.ok(out.missing_signal_dates.includes(dates[2]));
});
test('independent trades on one stock honor their formal deferred exit and count each signal', () => {
  const untilFriday = {actual_exit_date:dates[4],actual_exit_time:'2026-09-18T10:00:00+08:00'};
  const {summary,quotes} = fixture([settled(0,11,untilFriday),settled(1,11,untilFriday)]);
  const out = buildTop1RealizedAccount(summary,quotes);
  assert.equal(out.closed_trades,2);
  assert.equal(out.last_exit_date,dates[4]);
  assert.deepEqual(out.metrics,{cumulative_pnl:18909,max_profit:9454.5,max_loss:null});
});
test('quote ambiguity or price discontinuity excludes only that holding period', () => {
  for (const issue of ['MISSING_QUOTE','CORPORATE_ACTION_UNVERIFIED']) {
    const {summary,quotes} = fixture([settled(0,11),settled(3,8)]);
    if (issue === 'MISSING_QUOTE') quotes.delete(dates[2]);
    else quotes.get(dates[2])[0].pre_close=9;
    const out = buildTop1RealizedAccount(summary,quotes);
    assert.equal(out.trades[0].status,issue);
    assert.equal(out.trades[0].net_pnl,null);
    assert.deepEqual(out.metrics,{cumulative_pnl:-20245.5,max_profit:null,max_loss:-20245.5});
  }
});
test('no closed trade has zero cumulative P&L and no best or worst trade', () => {
  const {summary,quotes} = fixture([settled(0,2100,{entry_price:2000,slot_net_return:2100/2000-1-fee})]);
  const out = buildTop1RealizedAccount(summary,quotes);
  assert.equal(out.trades[0].status,'BUDGET_BELOW_ONE_LOT');
  assert.equal(out.closed_trades,0);
  assert.equal(out.last_exit_date,null);
  assert.deepEqual(out.metrics,{cumulative_pnl:0,max_profit:null,max_loss:null});
});
test('contradictory source evidence fails verification before publishing amounts', () => {
  for (const delta of [{exec_date:dates[2]}, {candidate_rank:2}, {slot_net_return:0.5},
    {actual_exit_date:'20260923',actual_exit_time:'2026-09-23T10:00:00+08:00'}]) {
    const {summary,quotes} = fixture([settled(0,11,delta)]);
    assert.throws(()=>buildTop1RealizedAccount(summary,quotes),/top1/);
  }
  const {summary,quotes} = fixture([settled(0,11)]);
  summary.groups.candidate_top1.daily_sequence.push(settled(0,11));
  assert.throws(()=>buildTop1RealizedAccount(summary,quotes),/Repeated/);
});
