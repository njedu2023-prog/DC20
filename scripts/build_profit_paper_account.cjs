#!/usr/bin/env node
'use strict';

// The main Pages job replays exactly the engine and byte-bound loaders shipped
// in decision.html. This builder does not implement a second financial model.
const fs = require('node:fs/promises');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const {TextEncoder, TextDecoder} = require('node:util');

const ACCOUNT_PATH = 'outputs/decision/profit_paper_account/account.json';
const SUMMARY_PATH = 'outputs/decision/candidate_profit_v1/summary.json';
const WINDOW_PATH = 'outputs/decision/compact_statistics_window.json';
const MAX_FILE_BYTES = 32 * 1024 * 1024;
const MAX_TOTAL_BYTES = 512 * 1024 * 1024;
const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const check = (condition, message) => { if (!condition) throw new Error(message); };
const jsonBytes = value => Buffer.from(JSON.stringify(value, null, 2) + '\n', 'utf8');
function validDate(value) {
  if (typeof value !== 'string' || !/^\d{8}$/.test(value)) return false;
  const iso = `${value.slice(0,4)}-${value.slice(4,6)}-${value.slice(6,8)}`;
  const stamp = Date.parse(iso + 'T00:00:00Z');
  return Number.isFinite(stamp) && new Date(stamp).toISOString().slice(0,10) === iso;
}
function strictJson(bytes, label) {
  let value;
  try { value = JSON.parse(new TextDecoder('utf-8', {fatal:true}).decode(bytes)); }
  catch (_) { throw new Error(`${label}: invalid UTF-8 JSON`); }
  check(value && typeof value === 'object' && !Array.isArray(value), `${label}: object required`);
  return value;
}
function safeRelative(relative) {
  check(typeof relative === 'string' && relative.length > 0 && relative.length <= 512
    && !/[\\%?#:\x00-\x20\x7f]/.test(relative) && !path.isAbsolute(relative)
    && relative.split('/').every(part => part && part !== '.' && part !== '..'),
  `unsafe relative path: ${String(relative)}`);
  return relative;
}
async function regularRoot(root) {
  const absolute = path.resolve(root);
  let current = path.parse(absolute).root;
  for (const part of absolute.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    const stat = await fs.lstat(current);
    check(!stat.isSymbolicLink() && stat.isDirectory(), `regular directory required: ${current}`);
  }
  return absolute;
}
async function safeTarget(root, relative, createParents = false) {
  const parts = safeRelative(relative).split('/');
  let current = root;
  for (let index = 0; index < parts.length; index++) {
    current = path.join(current, parts[index]);
    let stat;
    try { stat = await fs.lstat(current); }
    catch (error) {
      if (error.code !== 'ENOENT') throw error;
      if (index === parts.length - 1) return current;
      check(createParents, `missing source directory: ${current}`);
      await fs.mkdir(current);
      stat = await fs.lstat(current);
    }
    check(!stat.isSymbolicLink(), `symlink refused: ${current}`);
    check(index === parts.length - 1 ? stat.isFile() : stat.isDirectory(), `regular path required: ${current}`);
  }
  return current;
}
function reader(root) {
  let total = 0;
  const cache = new Map();
  return async relative => {
    safeRelative(relative);
    if (!cache.has(relative)) cache.set(relative, (async () => {
      const filename = await safeTarget(root, relative);
      const stat = await fs.lstat(filename);
      check(stat.isFile() && !stat.isSymbolicLink() && stat.size > 0 && stat.size <= MAX_FILE_BYTES,
        `source size/type invalid: ${relative}`);
      total += stat.size;
      check(total <= MAX_TOTAL_BYTES, 'paper account source byte budget exceeded');
      const handle = await fs.open(filename, require('node:fs').constants.O_RDONLY | require('node:fs').constants.O_NOFOLLOW);
      try {
        const opened = await handle.stat();
        check(opened.ino === stat.ino && opened.dev === stat.dev && opened.size === stat.size, `source changed while opening: ${relative}`);
        const bytes = await handle.readFile();
        check(bytes.length === stat.size, `source changed while reading: ${relative}`);
        return bytes;
      } finally { await handle.close(); }
    })());
    return cache.get(relative);
  };
}
function extractFunction(html, name) {
  check(/^[A-Za-z][A-Za-z0-9]*$/.test(name), 'invalid function name');
  const match = new RegExp('^    (?:async )?function ' + name + '\\(', 'm').exec(html);
  check(match, `required shared HTML function missing: ${name}`);
  const end = html.indexOf('\n    }', match.index);
  check(end !== -1, `shared HTML function not terminated: ${name}`);
  return html.slice(match.index, end + '\n    }'.length);
}
function contextFromHtml(html, read) {
  const names = ['executableProfitExpect', 'isSha256', 'canonicalJson', 'dateText', 'parseStrictCsvBytes',
    'ledgerCanonicalSource', 'buildProfitPaperAccount', 'loadProfitPaperAccountIdentities', 'loadProfitPaperAccountInputs'];
  const source = names.map(name => extractFunction(html, name)).join('\n');
  const context = vm.createContext({TextEncoder, TextDecoder,
    sha256Hex: async bytes => sha256(bytes),
    fetchPagesOnlyPath: async (relative, kind) => {
      check(kind === 'bytes', 'shared account loader requested a non-byte source');
      return read(relative);
    }
  }, {name:'DC20 published paper-account builder', codeGeneration:{strings:false, wasm:false}});
  vm.runInContext(source, context, {timeout:10000, filename:'decision.html:shared-paper-account'});
  return context;
}
function sharedSources(html) {
  return ['executableProfitExpect', 'isSha256', 'canonicalJson', 'dateText', 'parseStrictCsvBytes',
    'ledgerCanonicalSource', 'buildProfitPaperAccount', 'loadProfitPaperAccountIdentities', 'loadProfitPaperAccountInputs']
    .map(name => extractFunction(html, name)).join('\n');
}
async function checkedBound(read, relative, expected, label) {
  safeRelative(relative);
  check(typeof expected === 'string' && /^[0-9a-f]{64}$/.test(expected), `${label}: invalid SHA256 binding`);
  const bytes = await read(relative);
  check(sha256(bytes) === expected, `${label}: SHA256 mismatch`);
  return {bytes, value:strictJson(bytes, label)};
}
async function atomicWrite(root, relative, bytes) {
  const target = await safeTarget(root, relative, true);
  const temporary = target + '.' + process.pid + '.' + crypto.randomBytes(8).toString('hex') + '.tmp';
  let handle;
  try {
    handle = await fs.open(temporary, 'wx', 0o644);
    await handle.writeFile(bytes);
    await handle.sync();
    await handle.close(); handle = null;
    // Recheck the target immediately before replacement; never follow a symlink.
    await safeTarget(root, relative);
    await fs.rename(temporary, target);
  } finally {
    if (handle) await handle.close();
    await fs.unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; });
  }
}
async function materialize({siteRoot, sourceHtml}) {
  const root = await regularRoot(siteRoot);
  const read = reader(root);
  const revisionRaw = await read('revision.json');
  const revision = strictJson(revisionRaw, 'Pages revision');
  check(revision.repository === 'njedu2023-prog/DC20' && revision.branch === 'main'
    && /^[0-9a-f]{40}$/.test(revision.head_sha || '') && /^\d+$/.test(String(revision.run_id || '')),
  'invalid Pages repository/head/run identity');
  check(typeof revision.published_at_utc === 'string' && Number.isFinite(Date.parse(revision.published_at_utc)), 'invalid Pages publication time');
  check(revision.candidate_profit_summary_url === SUMMARY_PATH && revision.compact_statistics_window_url === WINDOW_PATH,
    'unexpected Pages paper-account input paths');
  const summary = await checkedBound(read, SUMMARY_PATH, revision.candidate_profit_summary_sha256, 'frozen summary');
  const windowPayload = await checkedBound(read, WINDOW_PATH, revision.compact_statistics_window_sha256, 'statistics window');
  check(validDate(revision.primary_d_signal_date) && validDate(summary.value.as_of_date)
    && summary.value.as_of_date >= '20260914' && summary.value.as_of_date <= revision.primary_d_signal_date
    && windowPayload.value.report_signal_date === revision.primary_d_signal_date,
  'paper account summary/window/revision dates differ or contain a future ledger');
  check(Array.isArray(windowPayload.value.source_files) && windowPayload.value.source_files.length <= 10000, 'invalid source manifest');
  for (const entry of windowPayload.value.source_files) {
    safeRelative(entry.path);
    check(/^[0-9a-f]{64}$/.test(entry.sha256 || ''), 'invalid source manifest SHA256');
  }
  check(new Set(windowPayload.value.source_files.map(entry => entry.path)).size === windowPayload.value.source_files.length,
    'duplicate source manifest paths');
  const htmlPath = path.resolve(sourceHtml);
  const htmlRoot = await regularRoot(path.dirname(htmlPath));
  const htmlBytes = await reader(htmlRoot)(path.basename(htmlPath));
  // Pages adds a dated truthfulness banner to HTML. Require the same published
  // pages and exact engine/loaders, without treating that expected banner as code drift.
  const deployedIndex = await read('index.html'), deployedDecision = await read('decision.html');
  check(deployedIndex.equals(deployedDecision), 'published index.html differs from decision.html');
  const sourceText = new TextDecoder('utf-8', {fatal:true}).decode(htmlBytes);
  const deployedText = new TextDecoder('utf-8', {fatal:true}).decode(deployedIndex);
  check(sharedSources(deployedText) === sharedSources(sourceText), 'published account implementation differs from --source-html');
  const context = contextFromHtml(sourceText, read);
  // JSON is created inside the context, preserving the VM Map identity expected by the engine.
  context.inputJson = JSON.stringify({summary:summary.value, windowPayload:windowPayload.value, revision});
  vm.runInContext('var input = JSON.parse(inputJson);', context, {timeout:1000});
  context.inputs = await vm.runInContext('loadProfitPaperAccountInputs(input.summary, input.windowPayload, input.revision)', context, {timeout:10000});
  const result = vm.runInContext('buildProfitPaperAccount(input.summary, inputs.quotes, inputs.identities)', context, {timeout:30000});
  check(result.start_signal_date === '20261019' && result.initial_capital === 735000 && result.per_trade_budget === 100000
    && result.waiting_for_start === (result.as_of_date < result.start_signal_date), 'paper account reset configuration mismatch');
  check(result.trades.every(t => t.signal_date >= result.start_signal_date)
    && result.days.every(d => d.date >= result.start_signal_date)
    && result.missing_signal_dates.every(d => d >= result.start_signal_date), 'paper account contains pre-reset records');
  result.data_bindings = {summary_sha256:revision.candidate_profit_summary_sha256,
    window_sha256:revision.compact_statistics_window_sha256,
    engine_sha256:sha256(Buffer.from(extractFunction(new TextDecoder('utf-8', {fatal:true}).decode(htmlBytes), 'buildProfitPaperAccount'))),
    source_files:context.inputs.source_files, missing_market_dates:context.inputs.missing_market_dates};
  result.publication = {mode:'main_pipeline', source_head_sha:revision.head_sha, run_id:revision.run_id,
    published_at_utc:revision.published_at_utc, source_html_sha256:sha256(htmlBytes),
    latest_signal_date:revision.primary_d_signal_date,
    ledger_lagging:summary.value.as_of_date < revision.primary_d_signal_date};
  const artifactRaw = jsonBytes(result);
  const binding = {profit_paper_account_url:ACCOUNT_PATH, profit_paper_account_sha256:sha256(artifactRaw),
    profit_paper_account_as_of_date:result.as_of_date, profit_paper_account_last_valid_date:result.last_valid_date,
    profit_paper_account_updated_at:revision.published_at_utc, profit_paper_account_producer_run_id:revision.run_id};
  const nextRevision = jsonBytes({...revision, ...binding});
  // Inputs never change. Both replacements are atomic; revision is the final
  // publication marker. The Pages artifact itself is uploaded only afterward.
  let oldArtifact = null;
  try { oldArtifact = await reader(root)(ACCOUNT_PATH); }
  catch (error) { if (error.code !== 'ENOENT' && !String(error.message).startsWith('missing source directory:')) throw error; }
  await atomicWrite(root, ACCOUNT_PATH, artifactRaw);
  try { await atomicWrite(root, 'revision.json', nextRevision); }
  catch (error) {
    if (oldArtifact) await atomicWrite(root, ACCOUNT_PATH, oldArtifact);
    else await fs.unlink(await safeTarget(root, ACCOUNT_PATH));
    throw error;
  }
  return {status:'built', ...binding, complete:result.complete, blocked:result.blocked,
    trades:result.trades.length, daily_points:result.days.length};
}
async function verifyPublic({siteRoot, baseUrl, fetchImpl = globalThis.fetch}) {
  const root = await regularRoot(siteRoot), read = reader(root);
  const revisionRaw = await read('revision.json'), revision = strictJson(revisionRaw, 'local Pages revision');
  check(revision.profit_paper_account_url === ACCOUNT_PATH, 'account publication missing from revision');
  const account = await checkedBound(read, ACCOUNT_PATH, revision.profit_paper_account_sha256, 'published account');
  check(account.value.publication?.mode === 'main_pipeline'
    && account.value.publication.source_head_sha === revision.head_sha
    && account.value.publication.run_id === revision.run_id
    && account.value.data_bindings?.summary_sha256 === revision.candidate_profit_summary_sha256
    && account.value.data_bindings?.window_sha256 === revision.compact_statistics_window_sha256,
  'local account/revision identity mismatch');
  const base = new URL(baseUrl);
  check(base.protocol === 'https:' && !base.username && !base.password && !base.search && !base.hash, 'public verification requires plain HTTPS base URL');
  if (!base.pathname.endsWith('/')) base.pathname += '/';
  for (const [relative, expected] of [['revision.json', revisionRaw], [ACCOUNT_PATH, account.bytes]]) {
    const target = new URL(relative, base);
    target.searchParams.set('paper_account_run_id', String(revision.run_id));
    const response = await fetchImpl(target, {redirect:'error', signal:AbortSignal.timeout(30000), cache:'no-store'});
    check(response.ok, `public account GET failed (${response.status}): ${relative}`);
    const observed = Buffer.from(await response.arrayBuffer());
    check(observed.length <= MAX_FILE_BYTES && observed.equals(expected), `public account bytes have not converged: ${relative}`);
  }
  return {status:'verified', head_sha:revision.head_sha, run_id:revision.run_id,
    sha256:revision.profit_paper_account_sha256, verified_files:2};
}
function parseArgs(args) {
  const options = {};
  const keys = new Map([['--site-root','siteRoot'],['--source-html','sourceHtml'],['--verify-public','baseUrl']]);
  for (let index = 0; index < args.length; index += 2) {
    const key = keys.get(args[index]);
    check(key && !Object.hasOwn(options,key) && args[index + 1] && !args[index + 1].startsWith('--'), 'invalid or duplicate command arguments');
    options[key] = args[index + 1];
  }
  check(options.siteRoot && (options.baseUrl || options.sourceHtml),
    'usage: node scripts/build_profit_paper_account.cjs --site-root _site --source-html decision.html [or --verify-public https://.../]');
  return options;
}
if (require.main === module) {
  Promise.resolve().then(() => parseArgs(process.argv.slice(2)))
    .then(options => options.baseUrl ? verifyPublic(options) : materialize(options))
    .then(result => process.stdout.write(JSON.stringify(result) + '\n'))
    .catch(error => { process.stderr.write(`paper-account build failed: ${error.message}\n`); process.exitCode = 1; });
}
module.exports = {materialize, verifyPublic, safeRelative, extractFunction, parseArgs, ACCOUNT_PATH, SUMMARY_PATH, WINDOW_PATH};
