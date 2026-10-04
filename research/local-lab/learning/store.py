"""Append-only learning snapshots, dataset lineage and model lifecycle."""
import hashlib,json,sqlite3
from pathlib import Path
import lab
ROOT=Path(__file__).resolve().parent

def digest(x):return hashlib.sha256(json.dumps(x,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()
def schema(c):
 c.executescript('''
 CREATE TABLE IF NOT EXISTS learning_records(
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL, key TEXT NOT NULL,
 sha TEXT NOT NULL, at TEXT NOT NULL, artifact_id INTEGER NOT NULL REFERENCES artifacts(id),
 payload TEXT NOT NULL, UNIQUE(kind,key));
 ''')
 for op in ('UPDATE','DELETE'):
  c.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_learning_{op} BEFORE {op} ON learning_records BEGIN SELECT RAISE(ABORT,'append-only learning'); END")
 c.commit()
def put(c,kind,key,obj):
 schema(c);h=digest(obj);old=c.execute('select sha,id from learning_records where kind=? and key=?',(kind,key)).fetchone()
 if old:
  if old['sha']!=h:raise ValueError('IMMUTABLE_KEY_CONFLICT: '+kind)
  return old['id']
 folder=Path(c.execute('pragma database_list').fetchone()[2]).parent/'learning';folder.mkdir(exist_ok=True)
 p=folder/(h+'.json')
 if not p.exists():p.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False))
 aid=lab.artifact(c,p);cur=c.execute('insert into learning_records(kind,key,sha,at,artifact_id,payload) values(?,?,?,?,?,?)',(kind,key,h,lab.now(),aid,json.dumps(obj,ensure_ascii=False,allow_nan=False)));c.commit();return cur.lastrowid
def get(c,kind):
 schema(c);return [dict(id=r['id'],key=r['key'],_recorded_at=r['at'],**json.loads(r['payload'])) for r in c.execute('select * from learning_records where kind=? order by id',(kind,))]
def raw(c,record):
 r=c.execute('select a.sha256 from learning_records l join artifacts a on a.id=l.artifact_id where l.id=?',(record,)).fetchone()
 if not r:raise ValueError('UNKNOWN_RECORD')
 p=Path(c.execute('pragma database_list').fetchone()[2]).parent/'blobs'/r[0]
 if hashlib.sha256(p.read_bytes()).hexdigest()!=r[0]:raise ValueError('CORRUPT_EVIDENCE')
 return json.loads(p.read_text())
def register_policy(c):
 policy=json.loads((ROOT/'policy.json').read_text());put(c,'policy',policy['version'],policy);return policy

def dataset(c,method,basis):
 """Earliest complete pre-entry snapshot per D. Whole-cohort exclusions, never best revision."""
 if basis not in ('ACTUAL','QUOTE_PROXY'):raise ValueError('INVALID_BASIS')
 snapshots=[x for x in get(c,'snapshot') if x['method']==method];by={};excluded=[];rows=[]
 for s in snapshots:by.setdefault(s['day'],[]).append(s)
 labels=get(c,'label')
 for day,group in sorted(by.items()):
  good=[s for s in group if s['state']=='COMPLETE' and s['provenance']=='PROSPECTIVE_LOCAL']
  if not good:excluded.append({'day':day,'reason':'NO_COMPLETE_PROSPECTIVE_SNAPSHOT'});continue
  s=min(good,key=lambda a:(a['cutoff'],a['id']));raw(c,s['id'])
  if not (lab.stamp(s['captured_at'])<=lab.stamp(s['cutoff'])<=lab.stamp(s['_recorded_at'])<lab.stamp(s['entry_at'])):raise ValueError('FUTURE_SNAPSHOT')
  for rid in s.get('shared_raw_ids',[])+[i for row in s['rows'] for i in row.get('raw_ids',[])]:
   evidence=raw(c,rid)
   if lab.stamp(evidence['captured_at'])>lab.stamp(s['cutoff']):raise ValueError('POST_CUTOFF_RAW')
  cohort=[];reason=None
  for x in s['rows']:
   candidates=[l for l in labels if l['snapshot_id']==s['id'] and l['code']==x['code'] and l['basis']==basis]
   if not candidates:reason='MISSING_LABEL';break
   superseded={l.get('supersedes') for l in candidates};latest=[l for l in candidates if l['id'] not in superseded]
   if len(latest)!=1:reason='AMBIGUOUS_LABEL';break
   l=latest[0];raw(c,l['id'])
   if l['status'] not in ('SETTLED','UNFILLED'):reason=l['status'];break
   if l['status']=='SETTLED' and (not l.get('execution_reviewed') or lab.stamp(l['exit_at'])>lab.stamp(lab.now())):reason='UNREVIEWED_OR_FUTURE';break
   cohort.append(dict(day=day,code=x['code'],x=x['features'],status=l['status'],gross=l.get('gross_return'),entry_at=s['entry_at'],exit_at=s['exit_at'],official_rank=x.get('official_rank'),snapshot_id=s['id'],label_id=l['id']))
  if reason:excluded.append({'day':day,'reason':reason});continue
  if len(cohort)!=s['expected_count']:raise ValueError('COHORT_COUNT_MISMATCH')
  rows.extend(cohort)
 return {'method':method,'basis':basis,'rows':rows,'excluded':excluded,'selection':'earliest complete prospective snapshot per day; no revision cherry-picking','fingerprint':digest(rows)}

def label(c,snapshot_id,code,basis,status,review):
 s=raw(c,snapshot_id)
 if c.execute('select kind from learning_records where id=?',(snapshot_id,)).fetchone()[0]!='snapshot':raise ValueError('NOT_SNAPSHOT')
 if code not in {x['code'] for x in s['rows']} or basis not in ('ACTUAL','QUOTE_PROXY'):raise ValueError('INVALID_LABEL_IDENTITY')
 if status not in ('SETTLED','UNFILLED','UNEXITED','MISSING','OUT_OF_SCENARIO'):raise ValueError('INVALID_STATUS')
 if not review.get('reviewer') or not review.get('reason'):raise ValueError('REVIEW_REQUIRED')
 refs=review.get('evidence_ids',[])
 if not refs:raise ValueError('EVIDENCE_REQUIRED')
 for i in refs:raw(c,i)
 if lab.stamp(lab.now())<lab.stamp(s['entry_at']):raise ValueError('FUTURE_RESULT')
 data=dict(snapshot_id=snapshot_id,code=code,basis=basis,status=status,entry_at=s['entry_at'],exit_at=s['exit_at'],review=review,execution_reviewed=False)
 if status=='OUT_OF_SCENARIO':
  if basis!='QUOTE_PROXY':raise ValueError('SCENARIO_AUDIT_IS_NOT_ACTUAL_FILL')
  from learning.quote_review import auction
  observations=[]
  for rid in refs:
   try:observations.append(auction(c,rid,code,s['entry_at']))
   except (ValueError,KeyError):continue
  if not observations or any(-1e-9<=gap<=.03+1e-9 for _,gap in observations):raise ValueError('OUT_OF_SCENARIO_RAW_REQUIRED')
 if status=='SETTLED':
  import math
  for key in ('entry_price','exit_price','previous_close'):
   if not isinstance(review.get(key),(int,float)) or not math.isfinite(review[key]) or review[key]<=0:raise ValueError('INVALID_PRICE')
  if lab.stamp(lab.now())<lab.stamp(s['exit_at']):raise ValueError('FUTURE_RESULT')
  if review.get('entry_at')!=s['entry_at'] or review.get('exit_at')!=s['exit_at']:raise ValueError('TIME_DEVIATION_SEPARATE')
  data['entry_policy']='ANY_EXECUTABLE_AUCTION'
  data['entry_gap']=review['entry_price']/review['previous_close']-1
  kind='ORDER_FILL' if basis=='ACTUAL' else 'REVIEWED_EXECUTABLE_QUOTE'
  if review.get('kind')!=kind or review.get('entry_executable') is not True or review.get('exit_executable') is not True:raise ValueError('NO_EXECUTION_REVIEW')
  # Raw price observations are necessary, but never sufficient proof of execution.
  if review.get('corporate_action_checked') is not True:raise ValueError('CORPORATE_ACTION_REVIEW_REQUIRED')
  if basis=='QUOTE_PROXY':
   from learning.features import records
   observed=[]
   for rid in refs:
    rawdoc=raw(c,rid)
    for row in records(rawdoc.get('data',{})):observed.append((rawdoc.get('api'),row))
   entryday=s['entry_at'][:10].replace('-','');exitstamp=s['exit_at'][:19].replace('T',' ')
   entry=any(api=='stk_auction' and r.get('ts_code')==code and r.get('trade_date')==entryday and r.get('price')==review['entry_price'] and r.get('pre_close')==review['previous_close'] for api,r in observed)
   exit=any(api=='stk_mins' and r.get('ts_code')==code and r.get('trade_time')==exitstamp and r.get('close')==review['exit_price'] for api,r in observed)
   if not entry or not exit:raise ValueError('RAW_PRICE_BINDING_MISMATCH')
   if review.get('price_semantics')!='AUCTION_PRICE_AND_1000_MINUTE_CLOSE_PROXY':raise ValueError('MINUTE_PROXY_SEMANTICS_REQUIRED')
  else:
   fills=[raw(c,rid) for rid in refs]
   for leg in ('entry','exit'):
    if not any(f.get('kind')=='BROKER_FILL' and f.get('code')==code and f.get('leg')==leg and f.get('at')==s[leg+'_at'] and f.get('price')==review[leg+'_price'] and f.get('quantity',0)>0 and f.get('broker_reference') for f in fills):raise ValueError('BROKER_FILL_BINDING_REQUIRED')
  data.update(gross_return=review['exit_price']/review['entry_price']-1,execution_reviewed=True)
 previous=[l for l in get(c,'label') if l['snapshot_id']==snapshot_id and l['code']==code and l['basis']==basis]
 if previous:
  last=max(previous,key=lambda l:l['id'])
  if review.get('supersedes')!=last['id']:raise ValueError('EXPLICIT_CORRECTION_REQUIRED')
  data['supersedes']=last['id']
 return put(c,'label',digest(data),data)

def lifecycle(c,model_id,action,reason):
 models=[x for x in get(c,'model') if x['id']==model_id]
 if not models or action not in ('SHADOW','REJECT','ROLLBACK'):raise ValueError('UNSUPPORTED_LIFECYCLE_ACTION')
 if not reason:raise ValueError('REASON_REQUIRED')
 m=models[0]
 if action=='SHADOW' and m['state']!='TRAINED_RESEARCH_ONLY':raise ValueError('MODEL_NOT_TRAINED')
 events=[e for e in get(c,'lifecycle') if e['model_id']==model_id]
 last=events[-1] if events else None
 if last and last['action']==action and last['reason']==reason:return last['id']
 return put(c,'lifecycle',digest([model_id,action,reason,last['id'] if last else None]),dict(model_id=model_id,action=action,reason=reason,at=lab.now(),production_promotion=False))

def dashboard(c):
 if not c.execute("select 1 from sqlite_master where name='learning_records'").fetchone():return {'state':'NOT_RUN'}
 snaps=get(c,'snapshot');runs=get(c,'run');models=get(c,'model');labels=get(c,'label')
 enrich=get(c,'enrichment');e=enrich[-1] if enrich else None
 summary=None if not e else dict(day=e['day'],id=e['id'],candidates=len(e['rows']),reason_count=sum(bool(r['limit_reason']) for r in e['rows']),flow_count=sum('20_DAY_STOCK_FLOW' not in r['missing'] for r in e['rows']),index_metrics=e['shared']['indices'],industry_days=e['shared']['industry_flow']['complete_days'],concept_days=e['shared'].get('concept_flow',{}).get('complete_days',0),industry_complete_series=len(e['shared'].get('industry_summary',{}).get('complete_series',[])),concept_complete_series=len(e['shared'].get('concept_summary',{}).get('complete_series',[])),issues=e['issues'],rows=[{k:r[k] for k in ('code','limit_reason','reason_status','missing')} for r in e['rows']])
 from learning import settlement_queue
 queue=settlement_queue.build(c);states={}
 for item in queue['items']:states[item['state']]=states.get(item['state'],0)+1

 from learning import research_agent
 return {'quote_learning':__import__('learning.quote_learning',fromlist=['latest']).latest(c),'active_research':research_agent.dashboard(c),'membership':(get(c,'membership') or [None])[-1],'primary_news_queue':(get(c,'primary_news_queue') or [None])[-1],'readiness':(get(c,'readiness') or [None])[-1],'state':'FOUNDATION_RESEARCH_ONLY','snapshots':len(snaps),'days':len({s['day'] for s in snaps}),'settled':sum(l['status']=='SETTLED' for l in labels),'last_run':runs[-1] if runs else None,'last_model':{k:models[-1].get(k) for k in ('id','state','reason','eligible_days','eligible_rows','basis')} if models else None,'enrichment':summary,'history_quarantined':len(get(c,'history')),'result_queue':states,'experiment_runs':len(get(c,'experiment')),'promotion':'NO_AUTOMATIC_PROMOTION','method':'daily_net_return_v1','objective':'预期扣费收益排序；盈利概率辅助；非资金净值'}
