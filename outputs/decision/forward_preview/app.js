'use strict';
const GROUPS = {
  promotion_top1:'晋级 Top1', promotion_top2:'晋级 Top2', promotion_top3:'晋级 Top3',
  promotion_top3_combined:'晋级 Top3 组合', profit_top1:'盈利 Top1',
  profit_top2:'盈利 Top2', profit_top2_combined:'盈利 Top2 组合'
};
function pct(value) { return typeof value === 'number' && Number.isFinite(value) ? `${(value*100).toFixed(2)}%` : '—'; }
function date(value) { return /^\d{8}$/.test(value || '') ? `${value.slice(0,4)}-${value.slice(4,6)}-${value.slice(6,8)}` : '—'; }
function ranked(rows, field) { return [...rows].sort((a,b)=>a[field]-b[field]); }
function validateDashboard(data) {
  if (data.schema_version !== 'dc20_forward_dashboard_v1') throw new Error('页面数据版本不匹配');
  if(data.production_enabled !== true) return false;
  const day=data.latest, epoch=data.epoch;
  if(!day || day.generation_mode!=='NATURAL' || !epoch?.activated_at_utc ||
     !/^\d{8}$/.test(epoch.start_signal_date||'') || day.signal_date<epoch.start_signal_date ||
     !/^[0-9a-f]{64}$/.test(day.freeze_sha256||'') || !day.admitted_at_utc)
    throw new Error('正式页面缺少同纪元的自然冻结证据');
  return true;
}
function visibleStatistics(data, active) { return active ? data.statistics?.groups||{} : {}; }
function truthRow(rows, code) { return rows.get(code)||{t_status:'MISSING',t1_status:'MISSING'}; }
function boot(data) {
  const active = validateDashboard(data);
  const $ = id => document.getElementById(id);
  const set = (id,text) => { $(id).textContent=text; };
  const el = (tag,text,className='') => { const n=document.createElement(tag); n.textContent=text; n.className=className; return n; };
  const day=data.latest || null, rows=day?.rows || [];
  const verification = new Map((data.verification?.rows || []).map(r=>[r.ts_code,r]));
  set('phase', active ? '新前向账本' : '迁移验收预览');
  set('notice', data.error ? `预览暂不可用：${data.error}。没有回退到旧名单或旧统计。` : active ? '本页仅统计新系统启用后的冻结记录。研究排序用于人工参考，不代表实际成交。' : '迁移验收预览：下面使用旧日冻结输入检查展示与模型兼容，不计入新前向统计；新系统尚未接管生产。');
  if(data.error) $('notice').classList.add('error');
  set('day-title', day ? `D ${date(day.signal_date)}` : '等待新系统首份名单');
  set('day-meta', day ? `${rows.length} 只真实候选 · 不足 10 不补足${active ? '' : ' · 迁移核对样本'}` : '没有生成记录时，不显示旧日替代数据。');
  set('d-date',date(day?.signal_date)); set('t-date',date(day?.exec_date)); set('exit-date',date(day?.exit_date));
  set('candidate-count',String(rows.length));
  const company = (row,kind) => {
    const n=el('td',''); const label=el('span',row.name,'company'); n.append(label);
    const rank=kind==='profit' ? row.profit_rank : row.promotion_rank;
    if((kind==='profit' && rank<=2)||(kind==='promotion' && rank<=3)) label.append(el('span',`Top${rank}`,'badge'));
    n.append(el('span',row.ts_code,'code')); return n;
  };
  const cell=(text,kind='')=>el('td',text,kind);
  const number=(value,format=pct)=>cell(format(value),'number'+(value>0?' up':value<0?' down':''));
  function add(id,children) {const tr=el('tr','');children.forEach(n=>tr.append(n));$(id).querySelector('tbody').append(tr);}
  for(const r of ranked(rows,'promotion_rank')) add('promotion-table',[
    cell(String(r.promotion_rank),'rank'),company(r,'promotion'),cell(r.industry||'—'),cell(r.stage_transition),
    cell(r.path_label||'路径数据不足','path'),number(r.path_change_pct),cell(pct(r.promotion_probability),'number')
  ]);
  for(const r of ranked(rows,'profit_rank')) add('profit-table',[
    cell(String(r.profit_rank),'rank'),company(r,'profit'),cell(r.industry||'—'),cell(r.stage_transition),
    cell(String(r.promotion_rank),'rank'),cell(Number.isFinite(r.profit_score)?r.profit_score.toFixed(4):'—','number')
  ]);
  const labels={PENDING:'未到验证时间',MISSING:'未取得验证数据',PROMOTED:'晋级成功',NOT_PROMOTED:'未晋级',NO_FILL:'未买入（代理）',EXIT_BLOCKED:'退出受阻，待顺延',SETTLED:'已结算（代理）'};
  set('verification-note',active ? `真值截至 ${date(data.verification?.as_of_date)} 收盘，未到期不提前验证。` : `回放截面为 D ${date(day?.signal_date)} 收盘，不加载后续真值；这里的“未到验证时间”仅对应回放时点，不是当前实时状态。`);
  const statusCell=status=>cell(labels[status]||'未验证','status '+(status==='MISSING'?'missing':status==='PENDING'||status==='EXIT_BLOCKED'?'pending':'resolved'));
  for(const r of ranked(rows,'promotion_rank')) {
    const v=truthRow(verification,r.ts_code);
    const slots=[];if(r.promotion_rank<=3)slots.push(`晋级 Top${r.promotion_rank}`);if(r.profit_rank<=2)slots.push(`盈利 Top${r.profit_rank}`);
    add('verification-table',[company(r,''),cell(slots.join(' / ')||'—'),cell(date(day.signal_date)),cell(date(day.exec_date)),cell(date(day.exit_date)),statusCell(v.t_status||'MISSING'),statusCell(v.t1_status||'MISSING'),number(v.net_return),cell(date(v.actual_exit_date))]);
  }
  for(const id of ['promotion-table','profit-table','verification-table']) if(!rows.length){const td=cell('暂无本期记录','empty');td.colSpan=$(id).querySelectorAll('thead th').length;add(id,[td]);}
  const epoch=data.epoch||{};
  set('epoch-note',epoch.start_signal_date ? `只统计 D ${date(epoch.start_signal_date)} 起的新冻结记录。` : '正式启用后，由首个合格 D 日确定统计起点；旧统计不迁入。');
  set('statistics-note',active ? '' : '新前向统计尚未启用。迁移样本不会计入席位、命中率或收益。');
  for(const [key,label] of Object.entries(GROUPS)){
    const s=visibleStatistics(data,active)[key]||{};
    const count=v=>active?String(v??0):'—';
    add('statistics-table',[cell(label),cell(count(s.frozen_slots),'number'),cell(count(s.t_validated),'number'),cell(pct(s.promotion_hit_rate),'number'),cell(count(s.conditional_trades),'number'),cell(count(s.unresolved_slots),'number'),cell(pct(s.conditional_positive_net_rate),'number'),number(s.mean_net_return),number(s.cumulative_return),number(s.max_drawdown)]);
  }
  set('cost-note',`收益验证采用日开盘价代理，不等同人工实际成交；研究费用假设为往返 ${data.costs_bps} 基点。企业行为未核对不结算，无法成交、缺数据与延期退出分别记录。`);
  set('version',`新框架 v1 · 源版本 ${(data.source_revision||'').slice(0,12)} · ${active?'已启用':'未切换生产'}`);
  const buttons=[...document.querySelectorAll('[data-tab]')];
  for(const button of buttons)button.addEventListener('click',()=>{for(const b of buttons){const selected=b===button;b.setAttribute('aria-selected',String(selected));$(b.dataset.tab).hidden=!selected;}});
}
if (typeof module !== 'undefined') module.exports={pct,date,ranked,GROUPS,validateDashboard,visibleStatistics,truthRow};
if (typeof document !== 'undefined') {
  try { boot(JSON.parse(document.getElementById('snapshot').textContent)); }
  catch(error){const n=document.getElementById('notice');n.textContent=`无法读取本版本数据：${error.message}。未显示缓存或旧统计。`;n.classList.add('error');}
}
