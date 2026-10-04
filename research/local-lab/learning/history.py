"""Historical reconstruction quarantine. Never inserts retrospective training labels."""
import argparse,json
import lab
from learning import pipeline,store

def reconstruct(c,day):
 run=pipeline.run(c,day)
 if not run.get('snapshot_id'):return run
 s=store.raw(c,run['snapshot_id'])
 if s['provenance']!='RETROSPECTIVE':raise ValueError('NOT_HISTORICAL_USE_LIVE_PIPELINE')
 body={'day':day,'snapshot_id':run['snapshot_id'],'captured_at':lab.now(),'state':'QUARANTINED_RECONSTRUCTION','candidate_count':s['expected_count'],'coverage_state':s['state'],'valid':run.get('valid',0),'feature_asof':day,'original_availability':'UNPROVEN','survivorship':'uses archived full pool, not todays winners','training_eligible':False,'promotion_eligible':False,'reason':'Fetched now; point-in-time revision history and execution labels require independent audit'}
 key=store.digest([day,run['snapshot_id']]);old=[r for r in store.get(c,'history') if r['key']==key]
 i=old[-1]['id'] if old else store.put(c,'history',key,body)
 return dict(id=i,**body)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--day',required=True);a=p.parse_args()
 with lab.connect() as c:r=reconstruct(c,a.day)
 print(json.dumps(r,ensure_ascii=False))
