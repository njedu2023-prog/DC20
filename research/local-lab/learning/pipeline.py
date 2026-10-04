"""Dynamic full-pool intake and Tushare archival; credentials never enter records."""
import argparse,json,re,time,ssl,urllib.request,hashlib
from datetime import datetime,timedelta,timezone
from pathlib import Path
import lab,tushare_access
from learning import store,features
BASE='https://njedu2023-prog.github.io/DC20/'

def public(path):
 if not re.fullmatch(r'outputs/decision/candidate_profit_v1/(index|day_\d{8})\.json',path):raise ValueError('UNTRUSTED_PUBLIC_PATH')
 with urllib.request.urlopen(BASE+path,context=ssl.create_default_context(cafile='/etc/ssl/cert.pem'),timeout=20) as r:raw=r.read()
 return json.loads(raw),hashlib.sha256(raw).hexdigest()
def discover(c,day):
 index,sha=public('outputs/decision/candidate_profit_v1/index.json')
 if index.get('enabled') is not True or not index.get('activation_id'):raise ValueError('FORMAL_ACTIVATION_NOT_READY')
 found=[x for x in index.get('days',[]) if x['signal_date']==day]
 if len(found)!=1:raise ValueError('EXACT_D_NOT_PUBLISHED')
 daydoc,h=public(found[0]['path'])
 if h!=found[0]['sha256']:raise ValueError('PUBLIC_SHA_MISMATCH')
 if daydoc.get('signal_date')!=day or daydoc.get('status')!='FROZEN_FORMAL_PROFIT_FORWARD_VALIDATION' or daydoc.get('formal_rank_allowed') is False or daydoc.get('activation_id')!=index['activation_id']:raise ValueError('FORMAL_DAY_NOT_READY')
 old=[r for r in store.get(c,'source') if r['key']==h]
 src=old[0]['id'] if old else store.put(c,'source',h,dict(source=BASE+found[0]['path'],body=daydoc,sha256=h,captured_at=lab.now(),index_sha=sha))
 original=daydoc.get('original_rows',[]);n=daydoc.get('original_candidate_count');ranks={x['ts_code']:x['candidate_rank'] for x in daydoc['rows']}
 pool=dict(signal_date=day,expected_count=n,source_id=src,candidates=[dict(code=x['ts_code'],name=x['name'],board_stage=x['board_stage'],official_rank=ranks.get(x['ts_code'])) for x in original])
 validate_pool(pool,day);return pool

def validate_pool(pool,day):
 rows=pool['candidates'];codes=[x['code'] for x in rows]
 if pool['signal_date']!=day or len(rows)!=pool['expected_count'] or len(set(codes))!=len(codes):raise ValueError('POOL_MISMATCH')
 if any(not re.fullmatch(r'\d{6}\.(SZ|SH|BJ)',x['code']) or x['board_stage'] not in (2,3) for x in rows):raise ValueError('BAD_POOL_IDENTITY')

def fetch(c,api,params,query=tushare_access.query,refresh=False,fields=''):
 key=store.digest([api,params,fields] if fields else [api,params]);cached=[x for x in store.get(c,'raw') if x.get('request_key')==key and x['state']=='OK']
 if cached and not refresh:
  x=cached[-1];store.raw(c,x['id']);return x
 for attempt in range(3):
  try:
   data=query(api,params,fields) if fields else query(api,params);rows=features.records(data)
   # Bounded per-code windows avoid pagination ambiguity. Fail rather than truncate.
   if len(rows)>={'limit_list_ths':4000,'ths_daily':3000}.get(api,5000):raise ValueError('POSSIBLE_TRUNCATION_SPLIT_WINDOW_REQUIRED')
   body=dict(request_key=key,api=api,params=params,state='OK' if rows else 'EMPTY',data=data,captured_at=lab.now())
   i=store.put(c,'raw',store.digest(body),body);return dict(id=i,**body)
  except tushare_access.AccessError as e:
   if attempt==2 or any(tag in str(e) for tag in ('API_CODE','LOCAL_TOKEN','CREDENTIAL','INVALID_LOCAL')):raise
   time.sleep(.3*(attempt+1))
 raise RuntimeError('UNREACHABLE')

def run(c,day,pool=None,query=tushare_access.query,refresh=False):
 policy=store.register_policy(c);start=lab.now();runid=store.digest([day,start]);report=dict(day=day,run_id=runid,started_at=start,method=policy['method'],state='RUNNING',errors=[])
 try:
  pool=pool or discover(c,day);validate_pool(pool,day)
  pool_id=store.put(c,'pool',store.digest(pool),pool)
  d=datetime.strptime(day,'%Y%m%d');lo=(d-timedelta(days=400)).strftime('%Y%m%d');end=(d+timedelta(days=40)).strftime('%Y%m%d')
  cal=fetch(c,'trade_cal',{'exchange':'SSE','start_date':lo,'end_date':end},query,refresh)
  ca=features.records(cal['data']);dates=[r['cal_date'] for r in ca]
  expected=[(datetime.strptime(lo,'%Y%m%d')+timedelta(days=i)).strftime('%Y%m%d') for i in range((datetime.strptime(end,'%Y%m%d')-datetime.strptime(lo,'%Y%m%d')).days+1)]
  if sorted(dates)!=expected or any(r['exchange']!='SSE' or r['is_open'] not in (0,1) for r in ca):raise ValueError('CALENDAR_INCOMPLETE')
  sessions=sorted(r['cal_date'] for r in ca if r['is_open']==1);ix=sessions.index(day);t,ex=sessions[ix+1:ix+3]
  def at(v,h):return f'{v[:4]}-{v[4:6]}-{v[6:]}T{h}+08:00'
  refs=[cal['id']];market=[]
  try:
   z=fetch(c,'index_daily',{'ts_code':'000001.SH','start_date':lo,'end_date':day},query,refresh);refs.append(z['id']);market=features.records(z['data']);features.bars(market,'000001.SH',day,sessions)
  except (ValueError,tushare_access.AccessError):market=[];report['errors'].append({'scope':'market','reason':'MARKET_UNAVAILABLE'})
  rows=[];capture_times=[cal['captured_at']]
  for member in pool['candidates']:
   code=member['code'];row=dict(code=code,name=member['name'],official_rank=member.get('official_rank'),features={},raw_ids=[],quality={},state='MISSING')
   try:
    packs={}
    for api in ('daily','adj_factor','daily_basic'):
     p={'ts_code':code,'start_date':lo,'end_date':day} if api!='daily_basic' else {'ts_code':code,'trade_date':day}
     try:
      z=fetch(c,api,p,query,refresh);packs[api]=features.records(z['data']);row['raw_ids'].append(z['id']);capture_times.append(z['captured_at']);time.sleep(.08)
     except tushare_access.AccessError:
      if api!='daily_basic':raise
      packs[api]=[]
    row['features'],row['quality']=features.compute(packs['daily'],packs['adj_factor'],packs['daily_basic'],market,code,day,sessions);row['state']='VALID_NUMERIC_BASELINE'
   except (ValueError,KeyError,TypeError,tushare_access.AccessError) as e:
    row['quality']={'error':str(e) if isinstance(e,(ValueError,tushare_access.AccessError)) else type(e).__name__};report['errors'].append({'scope':code,'reason':row['quality']['error']})
   rows.append(row)
  cutoff=lab.now();snapshot=dict(entry_policy=policy['scenario'],policy_version=policy['version'],method=policy['method'],feature_schema=features.VERSION,day=day,pool_id=pool_id,expected_count=pool['expected_count'],entry_at=at(t,'09:25:00'),exit_at=at(ex,'10:00:00'),cutoff=cutoff,captured_at=cutoff,source_available_at=max(capture_times),provenance='PROSPECTIVE_LOCAL' if lab.stamp(cutoff)<lab.stamp(at(t,'09:25:00')) else 'RETROSPECTIVE',state='COMPLETE' if rows and all(r['state']=='VALID_NUMERIC_BASELINE' for r in rows) else ('NO_CANDIDATES' if not rows else 'INCOMPLETE'),rows=rows,shared_raw_ids=refs,scope='numeric baseline, not six-dimensional full coverage',feature_code_sha=hashlib.sha256(Path(features.__file__).read_bytes()).hexdigest())
  # Exact same acquired data does not create more samples on retries.
  data_key=store.digest([pool_id,features.VERSION,refs,[(r['code'],r['raw_ids'],r['state']) for r in rows]])
  existing=[x for x in store.get(c,'snapshot') if x['key']==data_key]
  sid=existing[0]['id'] if existing else store.put(c,'snapshot',data_key,snapshot)
  report.update(state=snapshot['state'],snapshot_id=sid,expected=len(rows),valid=sum(r['state']=='VALID_NUMERIC_BASELINE' for r in rows),provenance=snapshot['provenance'])
 except (ValueError,KeyError,TypeError,tushare_access.AccessError, __import__('urllib').error.URLError) as e:
  report.update(state='BLOCKED',reason=type(e).__name__+':'+(str(e) if isinstance(e,ValueError) else 'SOURCE_UNAVAILABLE'))
 report['finished_at']=lab.now();store.put(c,'run',runid,report);return report

def capture_due(c,query=tushare_access.query,refresh=False):
 """Collect price observations only. Labels require separately audited execution review."""
 jobs={}
 for s in store.get(c,'snapshot'):
  if s['state'] not in ('COMPLETE','INCOMPLETE'):continue
  for r in s['rows']:
   for leg,at in [('ENTRY',s['entry_at']),('EXIT',s['exit_at'])]:
    if lab.stamp(at)>lab.stamp(lab.now()):continue
    jobs[(r['code'],at,leg)]=r
 results=[]
 for (code,at,leg),r in jobs.items():
  day=at[:10].replace('-','');params={'ts_code':code,'trade_date':day} if leg=='ENTRY' else {'ts_code':code,'freq':'1min','start_date':at[:10]+' 09:59:00','end_date':at[:10]+' 10:01:00'}
  try:
   z=fetch(c,'stk_auction' if leg=='ENTRY' else 'stk_mins',params,query,refresh);rs=features.records(z['data'])
   if any(x.get('ts_code')!=code for x in rs):raise ValueError('WRONG_SYMBOL')
   if leg=='ENTRY':valid=len(rs)==1 and rs[0].get('trade_date')==day and all(isinstance(rs[0].get(k),(int,float)) and rs[0][k]>0 for k in ('price','vol','pre_close'))
   else:valid=sum(x.get('trade_time')==at[:10]+' 10:00:00' for x in rs)==1 and all(str(x.get('trade_time','')).startswith(at[:10]) for x in rs)
   results.append(dict(code=code,at=at,leg=leg,raw_id=z['id'],state='CAPTURED_UNREVIEWED' if valid else 'MISSING_OR_INVALID',note='No automatic fill or settlement; minute/auction semantics and corporate actions require review'))
  except (ValueError,tushare_access.AccessError) as e:results.append(dict(code=code,at=at,leg=leg,state='FAILED',reason=type(e).__name__))
 obj={'at':lab.now(),'jobs':results,'settlement':'NO_AUTOMATIC_EXECUTION_CLAIM'};store.put(c,'capture',store.digest(obj),obj);return obj

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--day',required=True);p.add_argument('--pool',type=Path);p.add_argument('--refresh',action='store_true');p.add_argument('--capture',action='store_true');a=p.parse_args()
 with lab.connect() as c:
  result=run(c,a.day,json.loads(a.pool.read_text()) if a.pool else None,refresh=a.refresh)
  if a.capture:result['capture']=capture_due(c,refresh=a.refresh)
  print(json.dumps(result,ensure_ascii=False))
