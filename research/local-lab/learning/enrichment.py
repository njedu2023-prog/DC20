"""Versioned THS-sourced evidence sidecar. No rewriting prior predictions or weights."""
import argparse,json,math
from datetime import datetime,timedelta
import lab
from learning import store,pipeline,features
VERSION='THS_EVIDENCE_V2'

def number(x):return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)
def series(rows,code,sessions,field):
 a=sorted(rows,key=lambda r:r['trade_date'])
 if any(r.get('ts_code')!=code for r in a) or [r['trade_date'] for r in a]!=sessions or any(not number(r.get(field)) for r in a):raise ValueError('SERIES_IDENTITY_DATE_OR_VALUE')
 return a

def flow_metrics(rows,code,sessions):
 a=series(rows,code,sessions,'net_amount');out={}
 for n in (1,3,5,10,20):
  out['flow_net_cny_'+str(n)]=sum(r['net_amount'] for r in a[-n:])*10000
  out['flow_positive_ratio_'+str(n)]=sum(r['net_amount']>0 for r in a[-n:])/n
 return out

def index_metrics(rows,code,sessions):
 a=series(rows,code,sessions,'close');out={}
 if any(r['close']<=0 for r in a):raise ValueError('BAD_INDEX_PRICE')
 # Dynamic constituents: context, not a tradable strategy return or true member breadth.
 for n in (1,3,5,10,20):out[code[:6]+'_return_'+str(n)]=a[-1]['close']/a[-n-1]['close']-1
 return out

def sector_summary(rows,sessions):
 groups={};result=[];missing=[]
 for r in rows:groups.setdefault(r['ts_code'],[]).append(r)
 for code,rs in groups.items():
  try:a=series(rs,code,sessions,'net_amount')
  except ValueError:missing.append(code);continue
  m={str(n):{'net_cny':sum(r['net_amount'] for r in a[-n:])*1e8,'positive_ratio':sum(r['net_amount']>0 for r in a[-n:])/n} for n in (1,3,5,10,20)}
  result.append({'code':code,'name':a[-1].get('name',a[-1].get('industry')),'windows':m})
 for n in ('1','3','5','10','20'):
  for r in result:r['windows'][n]['rank']=1+sum(x['windows'][n]['net_cny']>r['windows'][n]['net_cny'] for x in result)
 return {'complete_series':result,'excluded_codes':missing,'scope':'rank among complete source series, not proof of full market membership; overlapping concepts must not be summed'}

def collect(c,snapshot_id,query=None,refresh=False):
 s=store.raw(c,snapshot_id);day=s['day'];q=query or pipeline.tushare_access.query
 cal=store.raw(c,s['shared_raw_ids'][0]);sessions=sorted(r['cal_date'] for r in features.records(cal['data']) if r['is_open']==1 and r['cal_date']<=day)
 needed=sessions[-20:];refs=[];issues=[];shared={};pool={r['code'] for r in s['rows']}
 def get(api,params,fields=''):
  try:
   z=pipeline.fetch(c,api,params,q,refresh,fields);refs.append(z['id']);return features.records(z['data'])
  except Exception as e:
   issues.append({'api':api,'params':params,'reason':str(e) if isinstance(e,(ValueError,pipeline.tushare_access.AccessError)) else type(e).__name__});return []
 # Same source, separate list categories. Empty is unknown, not a zero market count.
 limit={};limitfields='trade_date,ts_code,name,price,pct_chg,open_num,lu_desc,limit_type,tag,status,first_lu_time,last_lu_time,limit_amount,turnover'
 for d in sessions[-3:]:
  for category in (['涨停池','炸板池','跌停池'] if d==day else ['涨停池']):
   rs=get('limit_list_ths',{'trade_date':d,'limit_type':category},limitfields)
   if any(r.get('trade_date')!=d for r in rs) or len({r.get('ts_code') for r in rs})!=len(rs):issues.append({'api':'limit_list_ths','reason':'DUPLICATE_OR_WRONG_DATE'});rs=[]
   limit[d,category]=rs
 shared['market_pool_counts']={k:len(limit[day,k]) if limit[day,k] else None for k in ('涨停池','炸板池','跌停池')}
 shared['indices']={}
 for code in ('883900.TI','883958.TI'):
  rs=get('ths_daily',{'ts_code':code,'start_date':sessions[-21],'end_date':day})
  try:shared['indices'].update(index_metrics(rs,code,sessions[-21:]))
  except ValueError as e:issues.append({'api':'ths_daily','code':code,'reason':str(e)})
 # Market-wide industry flow series is archived independently from stock membership.
 sectors=[]
 for d in needed:
  rs=get('moneyflow_ind_ths',{'trade_date':d})
  if rs and all(r.get('trade_date')==d for r in rs):sectors+=rs
  else:issues.append({'api':'moneyflow_ind_ths','day':d,'reason':'EMPTY_OR_WRONG_DATE'})
 shared['industry_summary']=sector_summary(sectors,needed)
 concepts=[]
 for d in needed:
  rs=get('moneyflow_cnt_ths',{'trade_date':d})
  if rs and all(r.get('trade_date')==d for r in rs):concepts+=rs
  else:issues.append({'api':'moneyflow_cnt_ths','day':d,'reason':'EMPTY_OR_WRONG_DATE'})
 shared['concept_summary']=sector_summary(concepts,needed)
 shared['concept_flow']={'rows':concepts,'unit':'CNY_100M','complete_days':len({r['trade_date'] for r in concepts})}
 shared['industry_flow']={'rows':sectors,'unit':'CNY_100M','stock_membership':'UNVERIFIED_NOT_ASSIGNED','complete_days':len({r['trade_date'] for r in sectors})}
 tops=[]
 for d in needed:
  tops+=get('top_list',{'trade_date':d})
 rows=[]
 for member in s['rows']:
  code=member['code'];values={};missing=[]
  flow=get('moneyflow_ths',{'ts_code':code,'start_date':needed[0],'end_date':day})
  try:values.update(flow_metrics(flow,code,needed))
  except ValueError:missing.append('20_DAY_STOCK_FLOW')
  path=[]
  for d in sessions[-3:]:
   matches=[r for r in limit[d,'涨停池'] if r['ts_code']==code]
   if len(matches)==1:path.append(matches[0])
  today=[r for r in path if r['trade_date']==day];reason=None
  if today:
   r=today[0];reason=r.get('lu_desc')
   if number(r.get('open_num')):values['limit_open_count']=r['open_num']
   # Monetary unit labels ambiguous in provider docs: do not compute seal/turnover ratio.
   if not reason:missing.append('LIMIT_REASON')
  else:missing+=['LIMIT_REASON','LIMIT_PATH']
  margin=get('margin_detail',{'ts_code':code,'start_date':needed[0],'end_date':day})
  top=[r for r in tops if r.get('ts_code')==code]
  rows.append({'code':code,'features':values,'limit_reason':reason,'reason_status':'VENDOR_TAG_NOT_PRIMARY_NEWS_VERIFIED','limit_path':path,'margin_rows':margin,'dragon_tiger_rows':top,'missing':missing+['PRIMARY_NEWS_TIME','SECTOR_MEMBERSHIP','LEVEL2','SEAL_AMOUNT_UNIT_VERIFICATION'],'absence_note':'Empty event/margin response is not evidence of no event or no financing eligibility'})
 cutoff=lab.now();body={'version':VERSION,'snapshot_id':snapshot_id,'day':day,'cutoff':cutoff,'entry_at':s['entry_at'],'state':'COLLECTED_WITH_EXPLICIT_GAPS','provenance':'PROSPECTIVE_LOCAL' if lab.stamp(cutoff)<lab.stamp(s['entry_at']) else 'RETROSPECTIVE','raw_ids':sorted(set(refs)),'shared':shared,'rows':rows,'issues':issues,'automatic_probability_change':False}
 key=store.digest([VERSION,snapshot_id,body['raw_ids'],issues]);old=[r for r in store.get(c,'enrichment') if r['key']==key]
 i=old[-1]['id'] if old else store.put(c,'enrichment',key,body)
 return dict(id=i,day=day,candidates=len(rows),reasons=sum(bool(r['limit_reason']) for r in rows),flow_complete=sum('20_DAY_STOCK_FLOW' not in r['missing'] for r in rows),indices=len(shared['indices']),industry_days=shared['industry_flow']['complete_days'],concept_days=shared['concept_flow']['complete_days'],issues=len(issues),state=body['state'])

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--snapshot',type=int,required=True);p.add_argument('--refresh',action='store_true');a=p.parse_args()
 with lab.connect() as c:r=collect(c,a.snapshot,refresh=a.refresh)
 print(json.dumps(r,ensure_ascii=False))
