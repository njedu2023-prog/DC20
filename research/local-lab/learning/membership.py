"""Observed THS memberships. Never infer historical membership from today's response."""
import lab
from learning import store,pipeline,features
VERSION='OBSERVED_MEMBERSHIP_V1'

def join(code,rows,sectors):
 if not rows:raise ValueError('EMPTY_MEMBERSHIP')
 if any(r.get('con_code')!=code or not isinstance(r.get('ts_code'),str) for r in rows):raise ValueError('MEMBERSHIP_IDENTITY_MISMATCH')
 codes=[r['ts_code'] for r in rows]
 if len(codes)!=len(set(codes)):raise ValueError('DUPLICATE_MEMBERSHIP')
 return [dict(sectors[k],sector_code=k) for k in sorted(set(codes)&set(sectors))]

def collect(c,snapshot_id,enrichment_id,query=None):
 s=store.raw(c,snapshot_id);e=store.raw(c,enrichment_id)
 if e['snapshot_id']!=snapshot_id:raise ValueError('ENRICHMENT_BINDING_MISMATCH')
 sectors={}
 for kind in ('industry','concept'):
  for r in e['shared'][kind+'_summary']['complete_series']:
   if r['code'] in sectors:raise ValueError('AMBIGUOUS_SECTOR_CODE')
   sectors[r['code']]=dict(r,kind=kind)
 rows=[];refs=[];issues=[]
 for member in s['rows']:
  code=member['code']
  try:
   z=pipeline.fetch(c,'ths_member',{'con_code':code},query or pipeline.tushare_access.query,refresh=True)
   refs.append(z['id']);rs=features.records(z['data']);links=join(code,rs,sectors)
   rows.append(dict(code=code,name=member.get('name'),observed_at=z['captured_at'],raw_id=z['id'],member_count=len(rs),linked_sectors=links,state='CURRENT_OBSERVED_ONLY',historical_membership_verified=False))
  except (ValueError,pipeline.tushare_access.AccessError) as ex:
   issues.append(dict(code=code,reason=str(ex)));rows.append(dict(code=code,state='MISSING',linked_sectors=[]))
 body=dict(version=VERSION,snapshot_id=snapshot_id,enrichment_id=enrichment_id,day=s['day'],observed_at=lab.now(),rows=rows,raw_ids=refs,issues=issues,training_eligible=False,automatic_probability_change=False,semantics='Current observed membership links to past flow windows for context only. No historical membership dates supplied; no backfill into frozen forecasts. Overlapping concepts must not be summed.')
 i=store.put(c,'membership',store.digest(body),body)
 return dict(id=i,candidates=len(rows),mapped=sum(bool(r['linked_sectors']) for r in rows),issues=len(issues))
