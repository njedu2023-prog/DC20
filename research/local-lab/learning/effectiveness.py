"""Local frozen-forecast outcome report. Descriptive quotes, never simulated fills."""
import json,hashlib
from pathlib import Path
from collections import defaultdict
import lab,benchmark
from learning import quote_ledger,store,pipeline

VERSION='LOCAL_EFFECTIVENESS_V1'

def refresh_context(c,query=None):
 results=[]
 for p in quote_ledger.canonical(c):
  if lab.stamp(p['exit_at'])>lab.stamp(lab.now()):continue
  params=dict(ts_code=p['code'],start_date=p['entry_at'][:10].replace('-',''),end_date=p['exit_at'][:10].replace('-',''))
  for api in ('adj_factor','stk_limit','daily'):
   try:
    z=pipeline.fetch(c,api,params,**({'query':query} if query else {}));results.append(dict(api=api,code=p['code'],state=z['state'],raw_id=z['id']))
   except Exception as e:results.append(dict(api=api,code=p['code'],state='FAILED',reason=type(e).__name__))
 return results

def build(c):
 ledger=quote_ledger.build(c);groups=defaultdict(list)
 for row in ledger['rows']:
  if lab.stamp(row['exit_at'])>lab.stamp(ledger['as_of']):continue
  groups[row['day']].append(dict(row,status=row['quote_status']))
 comparison=benchmark.compare(groups,'QUOTE_RETURN')
 experiments=store.get(c,'ranking_experiment')
 return dict(version=VERSION,as_of=ledger['as_of'],quote_ledger=ledger,comparison=comparison,experiment=experiments[-1] if experiments else None,conclusion='DESCRIPTIVE_ONLY_INSUFFICIENT_DATES',notes=['首次事前版本按D/股票去重，其余版本保留原档。','报价核验不等于能买能卖；可执行/真实结算另列。','原0–3%条件概率不因全跳空复盘而追认适用。','本地自身评价不依赖官方排名；官方缺失只影响对应比较。'])

def markdown(r):
 s=r['quote_ledger']['summary'];lines=['# 本地盈利量化：预测结果核验',f"生成：{r['as_of']}",'',f"本地首次事前预测 {s['local_unique']} 只次；已到期 {s['due']}；报价核验 {s['verified']}；待补审查 {s['needs_review']}；未到期 {s['waiting']}。",'','**以下为报价路径扣费复盘，未证明买入/退出可执行性，不是实际成交收益。**','成本：0.20% / 0.45% / 0.80%；10:00使用该时间标记的1分钟收盘价，不是精确时刻成交承诺。','', '| D | 股票 | 入场 | 10:00 | 0.45%扣费报价收益 | 原0–3%条件 | 执行风险 |','|---|---|---:|---:|---:|---|---|']
 risk_names={'ENTRY_AT_UP_LIMIT_QUEUE_UNKNOWN':'竞价涨停排队未知','EXIT_AT_DOWN_LIMIT_QUEUE_UNKNOWN':'退出跌停排队未知','ORDER_QUANTITY_QUEUE_AND_1000_FILL_NOT_VERIFIED':'订单规模/精确成交未验证'}
 for x in r['quote_ledger']['rows']:
  if x['quote_status']=='VERIFIED_PRICE_PAIR':
   lines.append(f"| {x['day']} | {x['code']} {x['name']} | {x['entry_price']} | {x['exit_price']} | {x['net_return']:+.2%} | {'内' if x['original_scenario_applicable'] else '外/未知'} | {'；'.join(risk_names.get(k,k) for k in x['execution_risks'])} |")
  else:lines.append(f"| {x['day']} | {x['code']} {x['name']} | {x['entry_price'] or '—'} | — | {'未到期' if x['quote_status'] in ('WAIT_ENTRY','WAIT_EXIT') else '待审查：'+','.join(x['missing'])} | — | 未假设成交 |")
 lines+=['','## 同日、同池对照','历史本地前二按已冻结盈利概率选取；边界并列分摊两个名额。不是预期收益模型的新预测。','', '| D | 成本 | 本地前二 | 全池 | 官方前二 |','|---|---:|---:|---:|---:|']
 for d in r['comparison']['days']:
  for z in d['costs']:
   sels=z['selections'];vals=[]
   for k in ('independent_top2','equal_pool','official_top2'):
    v=sels.get(k,{}).get('conditional_mean_net');vals.append('—' if v is None else f'{v:+.2%}')
   lines.append(f"| {d['day']} | {z['cost']:.2%} | "+' | '.join(vals)+' |')
 lines+=['','只有完整同池才能计算当日对照；逐股报价即使特征缺失仍保留。未对小样本输出显著性或盈利能力结论。','原概率附带可执行条件尚未确认，因此本报告不计算其Brier或校准率。']
 if r.get('experiment'):
  experiment=r['experiment']
  lines+=['','## 改进实验','候选：冻结20日乖离较低者选前二；与原冻结概率前二及同池等权比较。',experiment.get('summary_text') or '详见本报告 JSON 的 experiment 字段。','事后探索与未来确认分开；本轮不采用新方法，不能据两天结果断言普遍无效。']
 return '\n'.join(lines)+'\n'

def publish(c,folder=None):
 ledger=quote_ledger.run(c);report=build(c);report['quote_ledger']['record_id']=ledger['record_id']
 root=folder or lab.ROOT/'data'/'reports';root=Path(root);root.mkdir(parents=True,exist_ok=True)
 payload={k:v for k,v in report.items() if k!='as_of'};payload['quote_ledger']={k:v for k,v in payload['quote_ledger'].items() if k!='as_of'}
 key=store.digest(payload)
 old=next((x for x in store.get(c,'effectiveness') if x['key']==key),None)
 rid=old['id'] if old else store.put(c,'effectiveness',key,report)
 for name,content in [('local-effectiveness-latest.json',json.dumps(report,ensure_ascii=False,indent=2)),('local-effectiveness-latest.md',markdown(report))]:
  dest=root/name;tmp=root/(name+'.tmp');tmp.write_text(content);tmp.replace(dest)
 return dict(record_id=rid,summary=report['quote_ledger']['summary'],report=str(root/'local-effectiveness-latest.md'))

def latest(c):
 rows=store.get(c,'effectiveness');return rows[-1] if rows else None
