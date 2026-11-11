'use strict';

const START = '20260914';
const BUDGET = 100000;
const LOT = 100;
const SETTLED = 'SETTLED_1000_LIMIT_HOLD_MINUTE_PROXY';
const cents = amount => Math.round((amount + Number.EPSILON) * 100);
const positive = value => Number.isFinite(value) && value > 0;
const day = value => typeof value === 'string' && /^\d{8}$/.test(value);

function requireEvidence(ok, message) {
  if (!ok) throw new Error(message);
}

// Closing a trade requires continuous unadjusted prices throughout that position.
// The exit day's pre-close is sufficient once the formal exit price is known.
function holdingPriceCheck(dates, code, quoteFor) {
  let lastClose;
  for (const date of dates) {
    const quote = quoteFor(date, code);
    if (!quote || !positive(Number(quote.pre_close))) return 'MISSING_QUOTE';
    if (lastClose !== undefined && cents(Number(quote.pre_close)) !== cents(lastClose)) {
      return 'CORPORATE_ACTION_UNVERIFIED';
    }
    if (date !== dates.at(-1)) {
      if (!positive(Number(quote.close))) return 'MISSING_QUOTE';
      lastClose = Number(quote.close);
    }
  }
  return null;
}

function quoteLookup(dailyQuotes) {
  const cache = new Map();
  return (date, code) => {
    if (!cache.has(date)) {
      const byCode = new Map();
      for (const quote of dailyQuotes.get(date)?.values() || []) {
        if (quote.trade_date !== date) continue;
        byCode.set(quote.ts_code, byCode.has(quote.ts_code) ? null : quote);
      }
      cache.set(date, byCode);
    }
    return cache.get(date).get(code);
  };
}

function settlement(row, dates, index, quoteFor, fee) {
  const i = index.get(row.signal_date);
  const exit = index.get(row.actual_exit_date);
  const iso = row.actual_exit_date?.replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3');
  const stamp = /^(\d{4}-\d\d-\d\d)T(\d\d:\d\d:\d\d)(?:\.\d+)?\+08:00$/.exec(row.actual_exit_time || '');
  requireEvidence(row.candidate_rank === 1 && row.proxy_fill === 1 && row.ts_code
    && positive(row.entry_price) && positive(row.exit_price) && Number.isFinite(row.slot_net_return),
  `Invalid settled top1 fill or price: ${row.signal_date}`);
  requireEvidence(exit >= i + 2 && row.exec_date === dates[i + 1] && row.exit_date === dates[i + 2]
    && stamp && stamp[1] === iso && stamp[2] >= '09:30:00' && stamp[2] <= '15:00:00',
  `Invalid settled top1 chronology: ${row.signal_date}`);
  requireEvidence(Math.abs(row.exit_price / row.entry_price - 1 - fee - row.slot_net_return) <= 1e-8,
    `Settled top1 prices and net return disagree: ${row.signal_date}`);

  let shares = Math.floor(BUDGET / (row.entry_price * LOT * (1 + fee))) * LOT;
  let notional = cents(shares * row.entry_price);
  let costs = cents(notional / 100 * fee);
  while (shares > 0 && notional + costs > BUDGET * 100) {
    shares -= LOT;
    notional = cents(shares * row.entry_price);
    costs = cents(notional / 100 * fee);
  }
  if (!shares) return {status:'BUDGET_BELOW_ONE_LOT'};
  const priceIssue = holdingPriceCheck(dates.slice(i + 1, exit + 1), row.ts_code, quoteFor);
  if (priceIssue) return {status:priceIssue};
  const net = cents(shares * row.exit_price) - notional - costs;
  return {status:'CLOSED', entry_date:row.exec_date, exit_date:row.actual_exit_date,
    actual_exit_time:row.actual_exit_time, entry_price:row.entry_price, exit_price:row.exit_price,
    shares, notional:notional / 100, costs:costs / 100, net_pnl:net / 100};
}

// Inputs are the revision-bound formal summary, strict trading calendar/quotes
// and same-D frozen identities from the existing publication loader.
function buildTop1RealizedAccount(summary, dailyQuotes, identities = new Map()) {
  const asOf = summary?.as_of_date;
  const fee = summary?.round_trip_cost_rate;
  requireEvidence(day(asOf) && Number.isFinite(fee) && fee >= 0 && fee <= 0.1,
    'Invalid top1 ledger date or round-trip fee');
  const sequence = summary.groups?.candidate_top1?.daily_sequence;
  requireEvidence(Array.isArray(sequence) && Array.isArray(summary.signal_dates), 'Missing formal top1 ledger');
  const dates = summary.signal_dates.filter(date => date >= START && date <= asOf);
  requireEvidence(dates.every((date, i) => day(date) && (!i || dates[i-1] < date))
    && (asOf < START || dates.at(-1) === asOf), 'Invalid top1 trading calendar');
  const index = new Map(dates.map((date, i) => [date, i]));
  const rows = new Map();
  for (const row of sequence) {
    requireEvidence(day(row.signal_date), 'Invalid top1 signal date');
    if (row.signal_date < START || row.signal_date > asOf) continue;
    requireEvidence(row.slot === 1 && index.has(row.signal_date) && !rows.has(row.signal_date),
      'Repeated or mismatched top1 frozen slot');
    rows.set(row.signal_date, row);
  }
  const quoteFor = quoteLookup(dailyQuotes);
  const trades = [];
  const missing = [];
  const declaredMissing = new Set(summary.missing_signal_dates || []);
  let total = 0, best = null, worst = null, lastExit = null, count = 0;
  for (const date of dates) {
    const row = rows.get(date);
    const sourceStatus = row?.status || 'MISSING_D_PUBLICATION';
    const identity = identities.get(`${date}:R1`);
    if (identity) requireEvidence(identity.signal_date === date && identity.rank === 1 && identity.ts_code === row?.ts_code,
      `Top1 identity differs from frozen slot: ${date}`);
    const trade = {id:`${date}:R1`, signal_date:date, rank:1, ts_code:row?.ts_code || null,
      name:identity?.name || null, source_status:sourceStatus, status:sourceStatus, net_pnl:null};
    if (sourceStatus === 'MISSING_D_PUBLICATION') missing.push(date);
    else requireEvidence(!declaredMissing.has(date), `Conflicting top1 publication coverage: ${date}`);
    if (sourceStatus === SETTLED) {
      Object.assign(trade, settlement(row, dates, index, quoteFor, fee));
      if (trade.status === 'CLOSED') {
        const net = cents(trade.net_pnl);
        total += net;
        if (net > 0 && (best === null || net > best)) best = net;
        if (net < 0 && (worst === null || net < worst)) worst = net;
        if (!lastExit || trade.exit_date > lastExit) lastExit = trade.exit_date;
        count++;
      }
    } else if (sourceStatus.startsWith('NO_FILL_')) {
      requireEvidence(row.proxy_fill === 0 && [null,0].includes(row.slot_net_return), 'Conflicting top1 no-fill evidence');
      trade.status = 'NO_FILL';
    } else {
      requireEvidence(!sourceStatus.startsWith('SETTLED_'), 'Unsupported top1 exit policy');
    }
    trades.push(trade);
  }
  return {schema_version:'dc20_top1_realized_paper_account_v1', start_signal_date:START, as_of_date:asOf,
    per_trade_budget:BUDGET, cost_rate:fee, last_exit_date:lastExit, closed_trades:count,
    metrics:{cumulative_pnl:total / 100, max_profit:best === null ? null : best / 100,
      max_loss:worst === null ? null : worst / 100}, missing_signal_dates:missing, trades};
}

module.exports = {buildTop1RealizedAccount};
