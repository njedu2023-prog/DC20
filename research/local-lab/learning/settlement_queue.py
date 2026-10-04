"""Per-member evidence queue. Data quality gates training, never evidence collection."""
import json
import lab
from learning import store

TERMINAL={'SETTLED','UNFILLED'}

def latest_labels(labels):
 superseded={x.get('supersedes') for x in labels}
 return [x for x in labels if x['id'] not in superseded]

def selected_snapshots(snapshots):
 """One audit cohort per day/method/pool. Never select by observed outcomes."""
 groups={}
 for s in snapshots:
  if s.get('state') not in ('COMPLETE','INCOMPLETE'):continue
  key=(s['day'],s.get('method'),s.get('pool_id',s['id']))
  groups.setdefault(key,[]).append(s)
 return [min(g,key=lambda s:(s.get('provenance')!='PROSPECTIVE_LOCAL',s['state']!='COMPLETE',s.get('cutoff',s['entry_at']),s['id'])) for g in groups.values()]

def build(c):
 observations={}
 for cap in store.get(c,'capture'):
  for job in cap['jobs']:
   key=(job['code'],job['at'],job['leg'])
   if job.get('state')=='CAPTURED_UNREVIEWED' or key not in observations:observations[key]=job
 labels=store.get(c,'label');items=[]
 for s in selected_snapshots(store.get(c,'snapshot')):
  for member in s['rows']:
   code=member['code']
   for basis in ('QUOTE_PROXY','ACTUAL'):
    applied=latest_labels([l for l in labels if l['snapshot_id']==s['id'] and l['code']==code and l['basis']==basis])
    if len(applied)==1 and applied[0]['status'] in TERMINAL:continue
    state='WAIT_ENTRY' if lab.stamp(lab.now())<lab.stamp(s['entry_at']) else ('WAIT_EXIT' if lab.stamp(lab.now())<lab.stamp(s['exit_at']) else 'NEEDS_EXECUTION_REVIEW')
    obs=[observations.get((code,s[leg.lower()+'_at'],leg)) for leg in ('ENTRY','EXIT')]
    ids=[x['raw_id'] for x in obs if x and x.get('state')=='CAPTURED_UNREVIEWED']
    if state=='NEEDS_EXECUTION_REVIEW' and len(ids)<2:state='MISSING_QUOTES'
    if basis=='ACTUAL' and state not in ('WAIT_ENTRY','WAIT_EXIT'):state='MISSING_BROKER_EVIDENCE'
    if len(applied)>1:state='AMBIGUOUS_LABEL'
    review={'reviewer':'','reason':'','evidence_ids':ids,'entry_at':s['entry_at'],'exit_at':s['exit_at'],'entry_executable':False,'exit_executable':False,'corporate_action_checked':False}
    if len(applied)==1:review['supersedes']=applied[0]['id']
    draft=dict(snapshot_id=s['id'],code=code,basis=basis,status='MISSING',review=review)
    items.append({'day':s['day'],'code':code,'basis':basis,'state':state,'draft':draft,'snapshot_id':s['id'],'member_state':member.get('state'),'feature_blocker':member.get('quality',{}).get('error'),'previous_status':applied[0]['status'] if len(applied)==1 else None,'required':['verify raw entry/exit semantics','volume/limit/suspension and fill evidence','corporate actions','minimum commission/slippage assumptions'],'provenance':s['provenance']})
 return {'at':lab.now(),'items':items,'automatic_labels':0}

if __name__=='__main__':
 with lab.connect() as c:r=build(c)
 print(json.dumps(r,ensure_ascii=False,indent=2))
