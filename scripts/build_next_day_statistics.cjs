#!/usr/bin/env node
'use strict';
// Published descriptive statistics only. Reuse the existing browser math; never
// rerank, alter a frozen selection, infer an execution, or settle a trade.
const fs = require('node:fs/promises');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const {TextDecoder} = require('node:util');
const MAX_FILE_BYTES = 32 * 1024 * 1024, MAX_TOTAL_BYTES = 512 * 1024 * 1024;
const OUTPUT_PATH = 'outputs/decision/next_day_statistics.json';
const WINDOW_PATH = 'outputs/decision/compact_statistics_window.json';
const INDEX_PATH = 'outputs/decision/candidate_profit_v1/index.json';
const ACTIVATION_PATH = 'models/decision_candidate_profit_activation_v1.json';
const ROWS_PATH = 'outputs/decision/primary_observation/rows.csv';
const SUMMARY_PATH = 'outputs/decision/primary_observation/summary.json';
const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const check = (ok, message) => { if (!ok) throw new Error(message); };
const jsonBytes = value => Buffer.from(JSON.stringify(value, null, 2) + '\n');
const shaOK = value => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
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
      check(total <= MAX_TOTAL_BYTES, 'next-day source byte budget exceeded');
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

function contextFromHtml(html) {
  const names = ['executableProfitExpect', 'exactObjectKeys', 'parseStrictCsvBytes', 'candidatePublicOriginalRows',
    'nextDayMarketStatistics', 'profitNextDayStatistics'];
  const source = names.map(name => extractFunction(html, name)).join('\n');
  const context = vm.createContext({TextDecoder}, {codeGeneration:{strings:false, wasm:false}});
  vm.runInContext(source, context, {timeout:10000});
  return {context, engineSha:sha256(Buffer.from(source))};
}
function checkDates(value, label) {
  check(['signal_date','exec_date','exit_date'].every(k=>validDate(value[k]))
    && value.signal_date < value.exec_date && value.exec_date < value.exit_date, `${label}: invalid D/T/T+1`);
}
function uniqueRows(rows, key, label) {
  check(new Set(rows.map(key)).size === rows.length, `${label}: duplicate key`);
}
async function build({root:siteRoot}) {
  const root = await regularRoot(siteRoot), read = reader(root), sources = new Map();
  const bound = async (relative, hash, label, json = true) => {
    check(shaOK(hash), `${label}: SHA256 binding missing`);
    const bytes = await read(relative);
    check(sha256(bytes) === hash, `${label}: SHA256 mismatch`);
    sources.set(relative,hash);
    return json ? strictJson(bytes,label) : bytes;
  };
  const revision = strictJson(await read('revision.json'),'revision');
  check(revision.repository === 'njedu2023-prog/DC20' && revision.branch === 'main'
    && /^[0-9a-f]{40}$/.test(revision.head_sha || '') && validDate(revision.primary_d_signal_date), 'invalid revision identity');
  const report = revision.primary_d_signal_date;
  check(revision.compact_statistics_window_url === WINDOW_PATH,'unexpected window URL');
  const window = await bound(WINDOW_PATH,revision.compact_statistics_window_sha256,'window');
  check(window.schema_version === 'dc20_compact_statistics_window_v1' && window.start_signal_date === '20260910'
    && window.report_signal_date === report, 'window report D mismatch');
  check(Array.isArray(window.source_files) && window.source_files.length <= 10000, 'invalid window sources');
  const manifest = new Map();
  for (const entry of window.source_files) {
    safeRelative(entry.path);check(shaOK(entry.sha256) && !manifest.has(entry.path),'invalid or duplicate source binding');
    manifest.set(entry.path,entry.sha256);
  }
  const asOf = window.promotion?.status === 'READY' ? window.promotion.as_of_date : report;
  const published = Date.parse(revision.published_at_utc);
  check(Number.isFinite(published),'publication timestamp missing');
  const closed = new Date(published + 8*3600000 - (new Date(published + 8*3600000).getUTCHours()<15 ? 86400000:0))
    .toISOString().slice(0,10).replaceAll('-','');
  check(validDate(asOf) && asOf >= '20260910' && asOf <= report && asOf <= closed,'invalid/future market as-of date');
  const html = new TextDecoder('utf-8',{fatal:true}).decode(await read('decision.html'));
  const {context,engineSha} = contextFromHtml(html);
  const csv = (bytes,fields,label) => context.parseStrictCsvBytes(bytes,fields,label);
  const marketCache = new Map();
  const market = async (day,kind) => {
    check(validDate(day),'invalid market date');
    const relative=`data/market/raw/${day.slice(0,4)}/${day}/${kind}.csv`;
    if (!manifest.has(relative)) return [];
    if (!marketCache.has(relative)) marketCache.set(relative,(async()=>{
      const fields=kind==='daily' ? ['close','pre_close','vol'] : ['up_limit'];
      const rows=csv(await bound(relative,manifest.get(relative),relative,false),['ts_code','trade_date',...fields],relative);
      check(rows.every(row=>typeof row.ts_code==='string' && /^\d{6}\.(SH|SZ|BJ)$/.test(row.ts_code)
        && row.trade_date===day),`${relative}: market date/code mismatch`);
      uniqueRows(rows,row=>row.ts_code,relative);
      // Blank, nonpositive, suspended or non-finite quotes remain missing in
      // the shared calculation; they must never be converted to zero returns.
      return rows;
    })());
    return marketCache.get(relative);
  };
  const tables = async (rows,withLimits) => {
    const daily=new Map(),limits=new Map();
    for (const day of [...new Set(rows.filter(r=>r.exec_date<=asOf).map(r=>r.exec_date))]) {
      daily.set(day,await market(day,'daily'));
      if(withLimits) limits.set(day,await market(day,'stk_limit'));
    }
    return {daily,limits};
  };
  const isolate = async fn => {
    try { return {status:'READY',as_of_date:asOf,cohorts:await fn()}; }
    catch(error) { return {status:'UNAVAILABLE',reason:String(error.message || error)}; }
  };
  const promotion=await isolate(async()=>{
    check(window.promotion?.status==='READY','promotion source unavailable');
    check(window.promotion.source_summary?.path===SUMMARY_PATH
      && manifest.get(SUMMARY_PATH)===window.promotion.source_summary.sha256
      && manifest.get(ROWS_PATH)===window.promotion.rows_sha256,'promotion source bindings differ');
    const summary=await bound(SUMMARY_PATH,manifest.get(SUMMARY_PATH),'promotion summary');
    check(summary.as_of_date===asOf && summary.rows_path===ROWS_PATH
      && summary.rows_sha256===window.promotion.rows_sha256,'promotion summary dates/bindings differ');
    const rows=csv(await bound(ROWS_PATH,manifest.get(ROWS_PATH),'promotion rows',false),
      ['signal_date','exec_date','exit_date','ts_code','promotion_rank'], 'promotion rows');
    for(const row of rows) {
      checkDates(row,'promotion row');
      check(/^\d{6}\.(SH|SZ|BJ)$/.test(row.ts_code) && /^[1-9]\d*$/.test(String(row.promotion_rank))
        && Number(row.promotion_rank)<=10,'invalid promotion rank/code');
    }
    uniqueRows(rows,r=>r.signal_date+':'+r.ts_code,'promotion rows');
    uniqueRows(rows,r=>r.signal_date+':'+r.promotion_rank,'promotion ranks');
    const selected=rows.filter(r=>r.signal_date>='20260910' && r.signal_date<=report && r.signal_date<=asOf);
    const {daily}=await tables(selected,false);
    return context.nextDayMarketStatistics(selected,'20260910',report,asOf,daily);
  });
  const profit=await isolate(async()=>{
    check(revision.candidate_profit_activation_url===ACTIVATION_PATH && revision.candidate_profit_index_url===INDEX_PATH,'unexpected candidate source URLs');
    const active=await bound(ACTIVATION_PATH,revision.candidate_profit_activation_sha256,'activation');
    check(active.enabled===true && active.effective_from_signal_date==='20260914'
      && active.model_canonical_sha256==='999666791b147e4d120ba9b7e10d9d1fc846ba171efbba73b2488ca56ce6f589','unexpected active fixed Ridge model');
    const index=await bound(INDEX_PATH,revision.candidate_profit_index_sha256,'candidate index');
    check(index.schema_version==='dc20_candidate_formal_profit_index_v1' && index.activation_id===active.activation_id
      && index.model_canonical_sha256===active.model_canonical_sha256 && Array.isArray(index.days),'candidate index model mismatch');
    uniqueRows(index.days,r=>r.signal_date,'candidate index');
    check(index.days.every(e=>validDate(e.signal_date) && e.path===`outputs/decision/candidate_profit_v1/day_${e.signal_date}.json`
      && shaOK(e.sha256) && shaOK(e.p0_file_sha256) && shaOK(e.snapshot_file_sha256)),'candidate index entry invalid');
    const rows=[];
    for(const entry of index.days.filter(e=>e.signal_date>='20260914' && e.signal_date<=report && e.signal_date<=asOf)) {
      const p0Path=`outputs/decision/three_rank_top10_${entry.signal_date}.json`;
      const p0=await bound(p0Path,entry.p0_file_sha256,'candidate P0');
      const day=await bound(entry.path,entry.sha256,'candidate day');
      checkDates(p0,'P0');checkDates(day,'candidate day');
      check(p0.signal_date===entry.signal_date && ['signal_date','exec_date','exit_date'].every(k=>day[k]===p0[k]),'candidate/P0 D/T/T+1 mismatch');
      check(['dc20_candidate_formal_profit_day_v1','dc20_candidate_formal_profit_day_v2_eligible'].includes(day.schema_version)
        && day.status==='FROZEN_FORMAL_PROFIT_FORWARD_VALIDATION' && day.activation_id===active.activation_id
        && day.model_canonical_sha256===active.model_canonical_sha256 && day.effective_from_signal_date===active.effective_from_signal_date
        && day.p0_file_sha256===entry.p0_file_sha256 && day.snapshot_file_sha256===entry.snapshot_file_sha256
        && day.p0_source?.path===p0Path && day.p0_source.sha256===entry.p0_file_sha256
        && day.snapshot_source?.path===`work/profit_1000_upgrade/candidate_natural_forward/day_${entry.signal_date}.json`
        && day.snapshot_source.sha256===entry.snapshot_file_sha256,'formal candidate identity/source mismatch');
      check(Array.isArray(p0.rows) && p0.rows.length<=10 && Array.isArray(day.rows)
        && day.candidate_count===day.rows.length && day.rows.length<=p0.rows.length,'candidate pool/count mismatch');
      check(p0.rows.every((r,i)=>r.promotion_rank===i+1),'P0 promotion ranks invalid');
      context.candidatePublicOriginalRows(day,p0);
      check(day.schema_version!=='dc20_candidate_formal_profit_day_v1' || day.rows.length===p0.rows.length,
        'v1 candidate pool is incomplete');
      check(day.entry_policy_id==='research_canonical_price_capacity_split_no_cap_v3'
        && day.exit_policy_id==='dc20_exit_1000_limit_hold_20260912_v1' && day.round_trip_cost_rate===0.0045
        && day.promotion_ranking_unchanged===true && day.historical_reranking_performed===false
        && day.model_retrained===false && day.actual_execution_claimed===false,'candidate policy changed');
      const stamp=(date,time)=>Date.parse(`${date.slice(0,4)}-${date.slice(4,6)}-${date.slice(6)}T${time}+08:00`);
      const times=[day.prediction_generated_at_utc,day.pre_cas_freeze_at_utc,day.projection_generated_at_utc].map(Date.parse);
      const deadline=stamp(day.exec_date,'09:20:00');
      check(times.every(Number.isFinite) && times[0]>=stamp(day.signal_date,'15:00:00')
        && times[0]<=times[1] && times[1]<=times[2] && times[2]<deadline
        && Date.parse(day.formal_publication_deadline_utc)===deadline,'candidate freeze time invalid');
      uniqueRows(p0.rows,r=>r.ts_code,'P0 rows');uniqueRows(p0.rows,r=>r.promotion_rank,'P0 ranks');
      uniqueRows(day.rows,r=>r.ts_code,'candidate rows');
      check(day.rows.every((r,i)=>r.candidate_rank===i+1 && typeof r.candidate_score==='number' && Number.isFinite(r.candidate_score)
        && p0.rows.some(p=>p.ts_code===r.ts_code && p.promotion_rank===r.promotion_rank && p.name===r.name)
        && (i===0 || day.rows[i-1].candidate_score>r.candidate_score
          || day.rows[i-1].candidate_score===r.candidate_score && day.rows[i-1].ts_code<r.ts_code)), 'candidate rank/order/P0 membership mismatch');
      check(Array.isArray(day.candidate_slots) && day.candidate_slots.length===2,'candidate slots missing');
      for(const [i,slot] of day.candidate_slots.entries()) {
        const original=day.rows[i];
        check(slot.slot===i+1 && (original ? slot.status==='PENDING_T' && slot.ts_code===original.ts_code
          && slot.candidate_rank===original.candidate_rank && slot.promotion_rank===original.promotion_rank
          && slot.candidate_score===original.candidate_score
          : slot.status==='MISSING_CANDIDATE' && slot.ts_code===null && slot.candidate_rank===null
            && slot.candidate_score===null && slot.promotion_rank===null), 'candidate slot mismatch');
        check(slot.proxy_fill===null && slot.net_return===null && slot.slot_net_return===null,'candidate slot claims settlement');
        // Preserve explicit absent slots as missing observations, just as the
        // existing browser algorithm does; never substitute a lower-ranked stock.
        rows.push({signal_date:day.signal_date,exec_date:day.exec_date,ts_code:slot.ts_code,promotion_rank:slot.slot});
      }
    }
    const {daily,limits}=await tables(rows,true);
    return context.profitNextDayStatistics(rows,report,asOf,daily,limits);
  });
  return {schema_version:'dc20_next_day_statistics_v1',report_signal_date:report,as_of_date:asOf,
    promotion_start_signal_date:'20260910',profit_start_signal_date:'20260914',
    bindings:{window_sha256:revision.compact_statistics_window_sha256,
      candidate_index_sha256:revision.candidate_profit_index_sha256 || null,
      candidate_activation_sha256:revision.candidate_profit_activation_sha256 || null,
      primary_rows_sha256:manifest.get(ROWS_PATH) || null},
    promotion,profit,engine_sha256:engineSha,
    source_files:[...sources].sort(([a],[b])=>a.localeCompare(b)).map(([path,sha256])=>({path,sha256}))};
}
async function materialize(options) {
  const root=await regularRoot(options.root), revisionRaw=await reader(root)('revision.json');
  const result=await build({root}), revision=strictJson(revisionRaw,'revision');
  check((await fs.readFile(await safeTarget(root,'revision.json'))).equals(revisionRaw),'revision changed during next-day build');
  const bytes=jsonBytes(result), binding={next_day_statistics_url:OUTPUT_PATH,next_day_statistics_sha256:sha256(bytes)};
  await atomicWrite(root,OUTPUT_PATH,bytes);
  await atomicWrite(root,'revision.json',jsonBytes({...revision,...binding}));
  return {status:'built',...binding,report_signal_date:result.report_signal_date,promotion:result.promotion.status,profit:result.profit.status,bytes:bytes.length};
}
function parseArgs(args) {
  check(args.length===2 && args[0]==='--root' && args[1] && !args[1].startsWith('--'),
    'usage: node scripts/build_next_day_statistics.cjs --root <staged Pages site>');
  return {root:args[1]};
}
if(require.main===module) Promise.resolve().then(()=>materialize(parseArgs(process.argv.slice(2))))
  .then(result=>process.stdout.write(JSON.stringify(result)+'\n'))
  .catch(error=>{process.stderr.write(`next-day statistics build failed: ${error.message}\n`);process.exitCode=1;});
module.exports={build,materialize,parseArgs,OUTPUT_PATH,WINDOW_PATH,INDEX_PATH,ACTIVATION_PATH,ROWS_PATH,SUMMARY_PATH};
