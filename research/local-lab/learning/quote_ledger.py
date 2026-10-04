"""Auditable per-prediction quote outcomes; never asserts fills or training readiness.

Canonical report uses earliest frozen local forecast per signal day and code.
Quote outcome != executable simulation != broker outcome.
"""
import json,math,hashlib
from pathlib import Path
from collections import Counter,defaultdict
import lab
from learning import store,features

VERSION='LOCAL_QUOTE_LEDGER_V1'
COSTS=(.002,.0045,.008)

def finite(x):return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) and x>0

def canonical(c):
 out={}
 for r in c.execute("select * from predictions where provenance='PROSPECTIVE_LOCAL' order by recorded_at,id"):
  p=dict(r)
  if not p.get('entry_at') or not p.get('exit_at'):continue
  if not (lab.stamp(p['cutoff'])<=lab.stamp(p['recorded_at'])<lab.stamp(p['entry_at'])):continue
  out.setdefault((p['signal_date'],p['code']),p)
 return list(out.values())

def archive_doc(c,aid):
 h=c.execute('select sha256 from artifacts where id=?',(aid,)).fetchone()[0]
 b=(Path(c.execute('pragma database_list').fetchone()[2]).parent/'blobs'/h).read_bytes()
 if hashlib.sha256(b).hexdigest()!=h:raise ValueError('CORRUPT_FORECAST')
 return json.loads(b),h

def raw_index(c):
 out=defaultdict(list);errors=[]
 for raw in store.get(c,'raw'):
  if raw.get('api') not in ('stk_auction','stk_mins','adj_factor','stk_limit','daily'):continue
  try:
   d=store.raw(c,raw['id'])
   if d.get('api')=='stk_mins' and d.get('params',{}).get('freq')!='1min':continue
   for row in features.records(d.get('data',{})):
    t=row.get('trade_time') or row.get('trade_date')
    out[(d['api'],row.get('ts_code'),t)].append((raw['id'],row))
  except (ValueError,OSError,KeyError,TypeError):errors.append(raw['id'])
 return out,errors

def observation(index,api,code,t,fields):
 candidates=index.get((api,code,t),[])
 valid=[(i,r) for i,r in candidates if all(finite(r.get(k)) for k in fields)]
 if not valid:return None,'MISSING_'+api
 values={tuple(r[k] for k in fields) for _,r in valid}
 if len(values)>1:return None,'CONFLICT_'+api
 return max(valid,key=lambda x:x[0]),None

def frozen_feature(c,p,snapshots):
 d=json.loads(p['payload']);v=d.get('metrics',{}).get('bias20_pct')
 if isinstance(v,(int,float)) and math.isfinite(v):
  return dict(bias20=v/100,feature_cutoff=p['cutoff'],feature_captured_at=p['cutoff'],feature_source='FROZEN_PREDICTION_PAYLOAD',feature_evidence_id=p['artifact_id'])
 eligible=[s for s in snapshots if s['day']==p['signal_date'] and s.get('provenance')=='PROSPECTIVE_LOCAL' and lab.stamp(s['captured_at'])<=lab.stamp(s['cutoff'])<=lab.stamp(s['_recorded_at'])<=lab.stamp(p['cutoff'])]
 for s in sorted(eligible,key=lambda s:(s['cutoff'],s['id'])):
  store.raw(c,s['id'])
  for r in s['rows']:
   if r['code']==p['code'] and isinstance(r.get('features',{}).get('bias20'),(int,float)):
    return dict(bias20=r['features']['bias20'],feature_cutoff=s['cutoff'],feature_captured_at=s['captured_at'],feature_source='PIT_LEARNING_SNAPSHOT',feature_evidence_id=s['id'])
 return dict(bias20=None,feature_cutoff=None,feature_captured_at=None,feature_source='MISSING')

def scenario(payload):
 if payload.get('entry_scenario_id'):return payload['entry_scenario_id']
 s=payload.get('entry_scenario','')
 if any(x in s for x in ('0至3','0–3','0—3','0-3')):return 'GAP_0_3'
 return 'UNKNOWN'

def reviewed_counts(c,rows):
 keys={(r['code'],r['entry_at'],r['exit_at']) for r in rows};counts={'QUOTE_PROXY':set(),'ACTUAL':set()}
 labels=store.get(c,'label');sup={l.get('supersedes') for l in labels}
 for l in labels:
  key=(l['code'],l['entry_at'],l['exit_at'])
  if l['id'] not in sup and l['status']=='SETTLED' and l.get('execution_reviewed') and key in keys:
   try:store.raw(c,l['id']);counts[l['basis']].add(key)
   except (OSError,ValueError):pass
 if c is not None:
  for r in c.execute("select p.code,p.entry_at,p.exit_at,o.basis from outcomes o join predictions p on p.id=o.prediction_id where o.status='SETTLED' and not exists(select 1 from outcomes n where n.supersedes=o.id)"):
   key=(r['code'],r['entry_at'],r['exit_at'])
   if key in keys:counts[r['basis']].add(key)
 return {k:len(v) for k,v in counts.items()}

def build(c,at=None):
 at=at or lab.now();index,errors=raw_index(c);snaps=store.get(c,'snapshot');rows=[]
 for p in canonical(c):
  d=json.loads(p['payload']);t=p['entry_at'][:10].replace('-','');ex=p['exit_at'][:10].replace('-','');stamp=p['exit_at'][:19].replace('T',' ')
  r=dict(day=p['signal_date'],code=p['code'],name=p['name'],prediction_id=p['id'],model=p['model'],p=p['p'],prediction_cutoff=p['cutoff'],recorded_at=p['recorded_at'],entry_at=p['entry_at'],exit_at=p['exit_at'],rank_eligible=d.get('independent_rank_eligible') is not False and d.get('probability_method')!='UNINFORMED' and p['p'] is not None,official_rank=d.get('comparison_official_rank'),original_scenario=scenario(d),basis='QUOTE_RETURN',quote_status='WAIT_ENTRY',entry_price=None,exit_price=None,gross_return=None,net_return=None,probability_eligible=False,original_scenario_applicable=None,evidence_ids=[],missing=[],execution_state='NOT_VERIFIED',execution_risks=[],costs=[],price_semantics='AUCTION_MATCH_PRICE / 10:00_ONE_MINUTE_CLOSE; NOT EXACT_EXECUTION',probability_note='成交条件未验证，报价结果不校准原可执行盈利概率')
  try:
   _,h=archive_doc(c,p['artifact_id']);r['forecast_sha256']=h;r.update(frozen_feature(c,p,snaps))
  except (ValueError,OSError,TypeError,KeyError):r.update(quote_status='FORECAST_EVIDENCE_INVALID');rows.append(r);continue
  if lab.stamp(at)<lab.stamp(p['entry_at']):rows.append(r);continue
  e,err=observation(index,'stk_auction',p['code'],t,('price','pre_close','vol'))
  if err:r['missing'].append(err)
  if e:
   rid,er=e;r.update(entry_price=er['price'],previous_close=er['pre_close'],entry_volume=er['vol'],entry_gap=er['price']/er['pre_close']-1);r['evidence_ids'].append(rid)
   r['original_scenario_applicable']=True if r['original_scenario']=='ANY_EXECUTABLE_AUCTION' else (-1e-9<=r['entry_gap']<=.03+1e-9 if r['original_scenario']=='GAP_0_3' else None)
  if lab.stamp(at)<lab.stamp(p['exit_at']):r['quote_status']='WAIT_EXIT';rows.append(r);continue
  x,err=observation(index,'stk_mins',p['code'],stamp,('close','vol'))
  if err:r['missing'].append(err)
  if x:
   rid,xr=x;r.update(exit_price=xr['close'],exit_volume=xr['vol']);r['evidence_ids'].append(rid)
  # Do not silently treat raw-price returns over a corporate action as total return.
  factors=[]
  for day in (t,ex):
   a,err=observation(index,'adj_factor',p['code'],day,('adj_factor',))
   if err:r['missing'].append(err+'_'+day)
   if a:r['evidence_ids'].append(a[0]);factors.append(a[1]['adj_factor'])
  if len(factors)==2 and abs(factors[0]-factors[1])>1e-10:r['missing'].append('CORPORATE_ACTION_REVIEW_REQUIRED')
  for day,price,leg in [(t,r['entry_price'],'ENTRY'),(ex,r['exit_price'],'EXIT')]:
   lim,err=observation(index,'stk_limit',p['code'],day,('up_limit','down_limit'))
   if lim and price:
    r['evidence_ids'].append(lim[0]);l=lim[1]
    if leg=='ENTRY' and price>=l['up_limit']-.001:r['execution_risks'].append('ENTRY_AT_UP_LIMIT_QUEUE_UNKNOWN')
    if leg=='EXIT' and price<=l['down_limit']+.001:r['execution_risks'].append('EXIT_AT_DOWN_LIMIT_QUEUE_UNKNOWN')
   elif err:r['execution_risks'].append('LIMIT_STATUS_UNKNOWN_'+leg)
   daily,derr=observation(index,'daily',p['code'],day,('open','high','low','close','vol'))
   if daily and price:
    r['evidence_ids'].append(daily[0]);dr=daily[1]
    if price<dr['low']-.011 or price>dr['high']+.011:r['missing'].append('PRICE_OUTSIDE_DAILY_RANGE_'+leg)
    if leg=='ENTRY' and abs(price-dr['open'])>.011:r['missing'].append('AUCTION_DAILY_OPEN_CONFLICT')
   elif derr:r['missing'].append('DAILY_CROSSCHECK_MISSING_'+leg)
  if e and x:r['raw_price_return']=r['exit_price']/r['entry_price']-1
  r['quote_status']='VERIFIED_PRICE_PAIR' if e and x and not r['missing'] else 'NEEDS_DATA_REVIEW'
  if r['quote_status']=='VERIFIED_PRICE_PAIR':
   r['gross_return']=r['raw_price_return'];r['net_return']=r['gross_return']-.0045
   r['costs']=[dict(cost=k,net=r['gross_return']-k) for k in COSTS]
  r['execution_risks'].append('ORDER_QUANTITY_QUEUE_AND_1000_FILL_NOT_VERIFIED')
  r['evidence_ids']=sorted(set(r['evidence_ids']));rows.append(r)
 due=[r for r in rows if lab.stamp(r['exit_at'])<=lab.stamp(at)]
 completed=reviewed_counts(c,rows)
 return dict(version=VERSION,as_of=at,selection='FIRST_RECORDED_LOCAL_PROSPECTIVE_PER_DAY_STOCK',rows=rows,summary=dict(local_unique=len(rows),due=len(due),verified=sum(r['quote_status']=='VERIFIED_PRICE_PAIR' for r in due),needs_review=sum(r['quote_status']=='NEEDS_DATA_REVIEW' for r in due),waiting=len(rows)-len(due),execution_settled=completed['QUOTE_PROXY'],actual_settled=completed['ACTUAL']),raw_integrity_errors=errors,note='报价核验与收益复盘，不是实际成交或已证明可执行收益；不写旧预测、不充当合格训练标签。')

def run(c):
 report=build(c);key=store.digest({k:v for k,v in report.items() if k!='as_of'})
 report['record_id']=store.put(c,'quote_ledger',key,report) if not any(r['key']==key for r in store.get(c,'quote_ledger')) else next(r['id'] for r in store.get(c,'quote_ledger') if r['key']==key)
 return report
