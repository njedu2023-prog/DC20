"""Explicit reviewed label import. Does not infer fills from prices."""
import argparse,json
from pathlib import Path
import lab
from learning import store,trainer

def report_shadow(c,model_id):
 m=store.raw(c,model_id);policy=m['policy'];data=store.dataset(c,m['method'],m['basis']);by={}
 for r in data['rows']:by.setdefault(r['snapshot_id'],[]).append(r)
 shadows=[s for s in store.get(c,'shadow') if s['model_id']==model_id];rows=[];pred=[];excluded=[];used=set()
 for s in shadows:
  if s['state']!='SHADOW_ONLY':excluded.append({'shadow':s['id'],'reason':s['state']});continue
  rs=by.get(s['snapshot_id'],[])
  if not rs:excluded.append({'shadow':s['id'],'reason':'OUTCOMES_INCOMPLETE'});continue
  if rs[0]['day'] in used:raise ValueError('DUPLICATE_FORWARD_DAY')
  used.add(rs[0]['day']);rows+=rs;pred+=s['predictions']
 if not rows:return {'state':'WAITING_NEW_FORWARD_RESULTS','days':0,'promotion':False,'excluded':excluded}
 ev=trainer.evaluate(rows,pred,m['bundle'],policy);gains=ev.get('gains',{});reasons=[]
 if len(used)<policy['test_days']:reasons.append('INSUFFICIENT_NEW_DAYS')
 for base in ('equal_pool','official_top2','momentum_top2'):
  g=gains.get(base,{});ci=g.get('block_bootstrap_95')
  if g.get('n_days',0)<policy['test_days'] or ci is None or ci[0]<=0:reasons.append('UNCERTAIN_UPLIFT_'+base)
 if ev.get('day_equal_brier',1)>ev.get('day_equal_brier_base',0):reasons.append('CALIBRATION_DEGRADED')
 from learning import risk
 risk_report=risk.assess(ev);reasons+=risk_report['reasons']
 if excluded:reasons.append('UNRESOLVED_FORWARD_COHORTS')
 tested=[x for x in store.get(c,'model') if x.get('state')=='TRAINED_RESEARCH_ONLY' and x.get('basis')==m['basis']]
 if len(tested)>1:reasons.append('MULTIPLE_MODEL_SEARCH_REQUIRES_FRESH_HOLDOUT_REVIEW')
 expensive=[d['costs'][-1]['selections']['independent_top2']['mean_net'] for d in ev['days']];expensive=[x for x in expensive if x is not None]
 if not expensive or sum(expensive)/len(expensive)<=0:reasons.append('HIGH_COST_NONPOSITIVE')
 result=dict(state='REVIEW_ELIGIBLE' if not reasons else 'BLOCKED_UPGRADE',days=len(used),model_id=model_id,reasons=reasons,evaluation=ev,risk_review=risk_report,excluded=excluded,promotion=False,at=lab.now(),note='Eligibility is not activation. Review capacity, overlapping capital, tail events and multiple testing before deployment.')
 store.put(c,'shadow_review',store.digest(result),result);return result

if __name__=='__main__':
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
 a=sub.add_parser('label');a.add_argument('file',type=Path)
 a=sub.add_parser('shadow');a.add_argument('--model',type=int,required=True);a.add_argument('--snapshot',type=int,required=True)
 a=sub.add_parser('evaluate');a.add_argument('--model',type=int,required=True)
 a=sub.add_parser('lifecycle');a.add_argument('--model',type=int,required=True);a.add_argument('--state',choices=['SHADOW','REJECT','ROLLBACK'],required=True);a.add_argument('--reason',required=True)
 a=p.parse_args()
 with lab.connect() as c:
  if a.action=='label':
   d=json.loads(a.file.read_text());r=store.label(c,d['snapshot_id'],d['code'],d['basis'],d['status'],d['review'])
  elif a.action=='shadow':r=trainer.shadow(c,a.model,a.snapshot)
  elif a.action=='evaluate':r=report_shadow(c,a.model)
  else:r=store.lifecycle(c,a.model,a.state,a.reason)
 print(json.dumps(r,ensure_ascii=False))
