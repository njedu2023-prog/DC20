"""Date/pool-bound review work queue, never equate tag coverage with verification."""
import lab
from learning import store

def queue(c,snapshot_id,enrichment_id):
 s=store.raw(c,snapshot_id);e=store.raw(c,enrichment_id)
 if e['snapshot_id']!=snapshot_id:raise ValueError('ENRICHMENT_BINDING_MISMATCH')
 rows=[]
 for r in e['rows']:
  reviews=[x for x in store.get(c,'primary_news_review') if x['snapshot_id']==snapshot_id and x['code']==r['code']]
  last=reviews[-1] if reviews else None
  rows.append(dict(code=r['code'],vendor_tag=r.get('limit_reason'),review_id=last['id'] if last else None,state=last['state'] if last else 'NEEDS_PRIMARY_REVIEW',remaining=last['unverified'] if last else ['原始公告、消息日期与反证待核验']))
 body=dict(snapshot_id=snapshot_id,enrichment_id=enrichment_id,day=s['day'],at=lab.now(),rows=rows,automatic_probability_change=False)
 return store.put(c,'primary_news_queue',store.digest(body),body)
