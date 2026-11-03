#!/usr/bin/env node
'use strict';

// Run: node tests/test_nov2_research_display.cjs [repo-root] [p0-json] [late-json]
// Only the DOM shell and network are replaced. The complete page script,
// validators, ranking renderers, event handlers and ledger join run unchanged.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto, createHash} = require('node:crypto');
const {test} = require('node:test');

const root = path.resolve(process.argv[2] || path.join(__dirname, '..'));
const p0Path = path.resolve(process.argv[3] || path.join(root, 'outputs/decision/three_rank_top10_20261102.json'));
const latePath = path.resolve(process.argv[4] || path.join(root, 'outputs/decision/late_candidate_research/day_20261102.json'));
const page = fs.readFileSync(path.join(root, 'decision.html'), 'utf8');
const p0Bytes = fs.readFileSync(p0Path);
const lateBytes = fs.readFileSync(latePath);
const p0 = JSON.parse(p0Bytes);
const late = JSON.parse(lateBytes);
const modelSha = '999666791b147e4d120ba9b7e10d9d1fc846ba171efbba73b2488ca56ce6f589';
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const clone = object => JSON.parse(JSON.stringify(object));
const script = page.match(/<script>([\s\S]*?)<\/script>/)?.[1];
assert(script, 'decision.html must contain the production inline script');
assert.equal((script.match(/initialize\(false\)\.catch\(showError\);/g) || []).length, 1);
const passiveScript = script.replace('initialize(false).catch(showError);', '/* startup disabled by VM test */');

class Element {
  constructor(id = '') {
    this.id = id;
    this.textContent = '';
    this.hidden = false;
    this.open = false;
    this.disabled = false;
    this.title = '';
    this.className = '';
    this.dataset = {};
    this.attributes = new Map();
    this.listeners = new Map();
    this.children = [];
    this._html = '';
    const classes = new Set();
    this.classList = {
      add: (...names) => names.forEach(name => classes.add(name)),
      remove: (...names) => names.forEach(name => classes.delete(name)),
      contains: name => classes.has(name),
      toggle: (name, force) => {
        const enabled = force === undefined ? !classes.has(name) : force;
        enabled ? classes.add(name) : classes.delete(name);
        return enabled;
      },
    };
  }
  set innerHTML(value) {
    this._html = String(value);
    this.children = [];
    // Retain real sort buttons and tbody as separate DOM nodes, so the actual
    // click listeners and renderRows updates are tested rather than emulated.
    for (const found of this._html.matchAll(/<button\b([^>]*)data-three-rank-sort="([^"]+)"([^>]*)>([\s\S]*?)<\/button>/g)) {
      const child = new Element();
      child.tag = 'button';
      child.dataset.threeRankSort = found[2];
      child.disabled = /\bdisabled\b/.test(found[1] + found[3]);
      child.textContent = found[4];
      this.children.push(child);
    }
    for (const found of this._html.matchAll(/<th\b[^>]*data-rank-field="([^"]+)"[^>]*>/g)) {
      const child = new Element();
      child.tag = 'th';
      child.dataset.rankField = found[1];
      this.children.push(child);
    }
    if (this._html.includes('<tbody data-three-rank-body>')) {
      const child = new Element();
      child.tag = 'tbody';
      child.dataset.threeRankBody = '';
      this.children.push(child);
    }
  }
  get innerHTML() { return this._html; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  removeAttribute(name) { this.attributes.delete(name); }
  addEventListener(name, listener) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(listener);
  }
  click() {
    if (this.disabled) return;
    for (const listener of this.listeners.get('click') || []) listener({target: this});
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) {
    if (selector === 'tbody[data-three-rank-body]') return this.children.filter(child => child.tag === 'tbody');
    if (selector === 'button[data-three-rank-sort]') return this.children.filter(child => child.tag === 'button');
    if (selector === 'th[data-rank-field]') return this.children.filter(child => child.tag === 'th');
    return [];
  }
  remove() { this.removed = true; }
  append(child) { this.children.push(child); }
}

function harness() {
  const nodes = new Map();
  const document = {
    getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, new Element(id));
      return nodes.get(id);
    },
    querySelectorAll: () => [],
    querySelector: () => null,
    createElement: () => new Element(),
    addEventListener() {},
  };
  const location = {search: '', protocol: 'https:', href: 'https://njedu2023-prog.github.io/DC20/', assign() {}, replace() {}};
  const requests = [];
  const sandbox = {
    document, location, window: {location, innerWidth: 1200, addEventListener() {}},
    console, URL, URLSearchParams, TextEncoder, TextDecoder, Uint8Array,
    crypto: webcrypto, setTimeout, clearTimeout,
    fetch: async url => { requests.push(String(url)); throw Error('Network forbidden in regression test'); },
    localStorage: {getItem() { return null; }, setItem() {}, removeItem() {}},
  };
  const context = vm.createContext(sandbox);
  vm.runInContext(passiveScript + `\nDate.now = () => Date.parse('2026-11-06T12:00:00+08:00');
    globalThis.api = {state, els, validatedThreeRankContract, candidateUnifiedProfitView,
      unifiedProfitView, loadCandidateProfitDay, ledgerJoinDay, renderThreeRankWatchlist, renderStatus};`, context);
  const api = context.api;
  const contract = clone(p0);
  const wrapper = {signal_date: contract.signal_date, exec_date: contract.exec_date,
    exit_date: contract.exit_date, three_rank: contract,
    three_rank_source_index: {json_path: 'outputs/decision/three_rank_top10_20261102.json', json_sha256: sha(p0Bytes)}};
  const activation = {status: 'ready', config: {enabled: true,
    activation_id: 'dc20_profit_ridge_999666_auction_exit1000_v1',
    effective_from_signal_date: '20260914', model_canonical_sha256: modelSha}, index: {days: []}};
  Object.assign(api.state, {index: 0, currentThreeRank: wrapper, candidateProfitActivation: activation,
    currentExecutableProfitResearch: {status: 'invalid', message: '新模型等待同D冻结；晋级名单保留'}});
  assert(api.validatedThreeRankContract(wrapper), 'real P0 must pass the unchanged production validator');
  return {api, context, nodes, requests, contract, wrapper, activation};
}

function viewRows(view) {
  return [...view.rows].map(([code, row]) => ({code, rank: row.executable_profit_research_rank,
    score: row.research_joint_proxy_score, tied: row.rank_tied}));
}
function renderedRows(h) {
  const tbody = h.api.els.stageContent.querySelector('tbody[data-three-rank-body]');
  assert(tbody, 'rendered table needs a real tbody node');
  return [...tbody.innerHTML.matchAll(/<tr data-code="([^"]+)"[^>]*>([\s\S]*?)<\/tr>/g)].map(found => ({
    code: found[1], html: found[2],
    cells: [...found[2].matchAll(/<td\b[^>]*>([\s\S]*?)<\/td>/g)].map(cell => cell[1]),
  }));
}

test('the nine display rows match the real SHA-bound late evidence at full precision', () => {
  assert.equal(sha(p0Bytes), late.expected_p0_sha256.three_rank_json);
  assert.equal(sha(lateBytes), '2e64196eaa154064c935cc7255544eb004ae6472311cae7a549c9f6ae773baf9');
  assert.equal(late.model_canonical_sha256, modelSha);
  assert.equal(late.status, 'LATE_RESEARCH_RECONSTRUCTION');
  assert.equal(late.natural_forward_admission_issued, false);
  assert.equal(late.formal_ledger_written, false);
  const h = harness();
  const before = JSON.stringify(h.contract);
  const view = h.api.unifiedProfitView(h.contract);
  assert(view.ready && view.candidate && view.supplement && view.researchOnly);
  assert.equal(view.rows.size, 9);
  assert.deepEqual(viewRows(view), late.prediction.rows.map(row => ({code: row.ts_code,
    rank: row.candidate_rank, score: row.candidate_score, tied: false})));
  assert.equal(JSON.stringify(h.contract), before, 'display must not mutate P0');
  assert.equal(h.requests.length, 0);
});

const mutations = [
  ['D', h => { h.contract.signal_date = '20261103'; }],
  ['T', h => { h.contract.exec_date = '20261104'; }],
  ['T+1', h => { h.contract.exit_date = '20261105'; }],
  ['model', h => { h.activation.config.model_canonical_sha256 = '0'.repeat(64); }],
  ['bundle hash', h => { h.contract.bundle_sha256 = '0'.repeat(64); }],
  ['feature hash', h => { h.contract.feature_snapshot_sha256 = '0'.repeat(64); }],
  ['member hash', h => { h.contract.top10_members_sha256 = '0'.repeat(64); }],
  ['company name', h => { h.contract.rows[0].name = '另一家公司'; }],
  ['stock code', h => { h.contract.rows[0].ts_code = '999999.SH'; }],
  ['promotion rank', h => { h.contract.rows[0].promotion_rank = 9; }],
  ['duplicate', h => { h.contract.rows[1] = clone(h.contract.rows[0]); }],
  ['missing row', h => { h.contract.rows.pop(); }],
  ['extra row', h => { h.contract.rows.push(clone(h.contract.rows[0])); }],
  ['activation invalid', h => { h.activation.status = 'invalid'; }],
];
for (const [name, mutate] of mutations) {
  test(`changed ${name} cannot display the D1102 supplement`, () => {
    const h = harness(); mutate(h);
    const view = h.api.candidateUnifiedProfitView(h.contract);
    assert.equal(view.ready, false);
    assert.equal(view.rows.size, 0);
    assert.equal(h.requests.length, 0);
  });
}

for (const status of ['invalid', 'missing', 'loading']) {
  test(`an indexed formal entry suppresses the supplement even when loaded ${status}`, () => {
    const h = harness();
    h.activation.index.days.push({signal_date: '20261102'});
    h.api.state.currentExecutableProfitResearch = {status, message: 'formal unavailable'};
    const view = h.api.unifiedProfitView(h.contract);
    assert.equal(view.ready, false);
    assert.equal(view.rows.size, 0);
    assert(!view.supplement);
  });
}

test('a valid loaded formal entry wins over the research supplement', () => {
  const h = harness();
  h.activation.index.days.push({signal_date: '20261102'});
  // Deliberately different formal ordering proves the supplement is not used.
  const rows = h.contract.rows.map((row, index) => ({...clone(row),
    candidate_rank: index + 1, candidate_score: 0.1 - index * 0.001}));
  h.api.state.currentExecutableProfitResearch = {status: 'ready', kind: 'candidate_fixed_ridge',
    activation: h.activation.config, projection: {schema_version: 'dc20_candidate_formal_profit_day_v1',
      signal_date: '20261102', exec_date: '20261103', exit_date: '20261104', rows}};
  const view = h.api.unifiedProfitView(h.contract);
  assert(view.ready && !view.supplement && !view.researchOnly);
  assert.equal(view.rows.get(rows[0].ts_code).research_joint_proxy_score, 0.1);
});

test('shared formal loader still throws for missing D instead of returning display rows', async () => {
  const h = harness();
  assert(h.api.unifiedProfitView(h.contract).ready);
  await assert.rejects(h.api.loadCandidateProfitDay(h.wrapper), /等待同D冻结/);
  assert.equal(h.requests.length, 0);
});

test('ledgerJoinDay preserves MISSING_D_PUBLICATION with no invented trade or return', async () => {
  const h = harness();
  const sequences = [1, 2].map(slot => [{signal_date: '20261102', slot,
    status: 'MISSING_D_PUBLICATION', ts_code: null, candidate_rank: null,
    entry_price: null, actual_exit_date: null, slot_net_return: null}]);
  const before = JSON.stringify(sequences);
  const joined = await h.api.ledgerJoinDay('20261102', sequences, [{signal_date: '20261102'}]);
  assert.deepEqual(clone(joined), sequences.map(rows => rows[0]));
  assert.equal(JSON.stringify(sequences), before);
  assert(joined.every(row => row.status === 'MISSING_D_PUBLICATION' && row.slot_net_return === null && row.ts_code === null));
  assert.equal(h.requests.length, 0);
});

test('real renderers show research labels, top-two badges and a warning header', () => {
  const h = harness();
  h.api.renderThreeRankWatchlist(h.wrapper, h.contract);
  h.api.renderStatus(h.wrapper);
  const table = h.api.els.stageContent.innerHTML;
  assert.match(table, /研究补算 · 非正式冻结/);
  assert.match(table, /非正式冻结，不计入正式收益或模拟账户/);
  assert.match(table, /day_20261102\.html/);
  assert.match(table, /补算排名 CSV/);
  const rows = renderedRows(h);
  const rowByCode = new Map(rows.map(row => [row.code, row.html]));
  assert.match(rowByCode.get('002086.SZ'), /东方海洋[\s\S]*aria-label="盈利排序第1名">盈1/);
  assert.match(rowByCode.get('600261.SH'), /阳光照明[\s\S]*aria-label="盈利排序第2名">盈2/);
  assert.equal(h.api.els.statusTitle.textContent, '晋级榜已生成 · 盈利排序为研究补算');
  assert.equal(h.api.els.statusBand.className, 'status-band warn');
  assert.equal(h.api.els.signalDate.textContent, '2026-11-02');
  assert.equal(h.api.els.execDate.textContent, '2026-11-03');
  assert.equal(h.api.els.exitDate.textContent, '2026-11-04');
});

test('actual sort clicks reorder nine rows without changing promotion, truth, returns or aggregates', () => {
  const h = harness();
  const metadata = {signal_date: h.contract.signal_date, exec_date: h.contract.exec_date,
    exit_date: h.contract.exit_date, bundle_sha256: h.contract.bundle_sha256,
    top10_members_sha256: h.contract.top10_members_sha256, status: 'READY'};
  // Controlled truth fixtures make accidental return replacement detectable.
  h.api.state.currentThreeRankTTruth = {...metadata, rows: h.contract.rows.map((row, i) => ({
    ts_code: row.ts_code, continuation_limit_up_hit: i % 2, t_close_change: (i - 4) / 100}))};
  h.api.state.currentThreeRankObservationTruth = {...metadata, rows: h.contract.rows.map((row, i) => ({
    ts_code: row.ts_code, actual_net_return: (i - 3) / 100,
    validation_status: 'FINAL_VERIFIED_PROXY', actual_exit_date: '20261104'}))};
  h.api.els.compactLedgerContent.innerHTML = 'unchanged formal profit summary';
  h.api.els.compactStatisticsContent.innerHTML = 'unchanged promotion summary';
  h.api.state.candidateProfitSummary = {sentinel: 'formal results'};
  const before = JSON.stringify([h.contract, h.api.state.currentThreeRankTTruth,
    h.api.state.currentThreeRankObservationTruth, h.api.state.candidateProfitSummary]);
  // Baseline has the same P0/truth but no research display.
  h.activation.index.days.push({signal_date: '20261102'});
  h.api.renderThreeRankWatchlist(h.wrapper, h.contract);
  const protectedCells = rows => Object.fromEntries(rows.map(row => [row.code,
    [6, 7, 10, 11, 12].map(index => row.cells[index])]));
  const baseline = protectedCells(renderedRows(h));
  h.activation.index.days = [];
  h.api.renderThreeRankWatchlist(h.wrapper, h.contract);
  assert.deepEqual(renderedRows(h).map(row => row.code), h.contract.rows.map(row => row.ts_code));
  const buttons = h.api.els.stageContent.querySelectorAll('button[data-three-rank-sort]');
  const profitButton = buttons.find(button => button.dataset.threeRankSort === 'mixed_profit_rank');
  const promotionButton = buttons.find(button => button.dataset.threeRankSort === 'promotion_rank');
  assert.equal(profitButton.disabled, false);
  profitButton.click();
  assert.deepEqual(renderedRows(h).map(row => row.code), late.prediction.rows.map(row => row.ts_code));
  assert.equal(profitButton.getAttribute('aria-pressed'), 'true');
  assert.equal(profitButton.classList.contains('active'), true);
  assert.deepEqual(protectedCells(renderedRows(h)), baseline);
  promotionButton.click();
  assert.deepEqual(renderedRows(h).map(row => row.code), h.contract.rows.map(row => row.ts_code));
  assert.equal(promotionButton.getAttribute('aria-pressed'), 'true');
  assert.deepEqual(protectedCells(renderedRows(h)), baseline);
  assert.equal(JSON.stringify([h.contract, h.api.state.currentThreeRankTTruth,
    h.api.state.currentThreeRankObservationTruth, h.api.state.candidateProfitSummary]), before);
  assert.equal(h.api.els.compactLedgerContent.innerHTML, 'unchanged formal profit summary');
  assert.equal(h.api.els.compactStatisticsContent.innerHTML, 'unchanged promotion summary');
});
