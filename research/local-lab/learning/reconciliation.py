"""Read-only cross-store lineage and matched diagnostics. Never backfills a prediction."""
import json,hashlib
from pathlib import Path
from collections import Counter,defaultdict
import lab,benchmark
from research_contract import archived
from learning import store,settlement_queue

VERSION='LINEAGE_REVIEW_V1'

def same_contract(p,payload,s,codes):
 return (s.get('method')=='daily_net_return_v1' and s['day']==p['signal_date']
  and s.get('provenance')=='PROSPECTIVE_LOCAL'
  and p['strategy']==lab.STRATEGY and abs(p['cost']-.0045)<1e-10
  and payload.get('entry_scenario_id') in ('GAP_0_3','ANY_EXECUTABLE_AUCTION')
  and lab.stamp(s['entry_at'])==lab.stamp(p['entry_at'])
  and lab.stamp(s['exit_at'])==lab.stamp(p['exit_at'])
  and len(s['rows'])==s['expected_count']==len(codes)
  and {r['code'] for r in s['rows']}==codes)

def compatible(p,payload,s,codes):
 return same_contract(p,payload,s,codes) and lab.stamp(s['captured_at'])<=lab.stamp(s['cutoff'])<=lab.stamp(s['_recorded_at'])<=lab.stamp(p['cutoff'])<lab.stamp(p['entry_at'])

def report(c):
 snaps=store.get(c,'snapshot');labels=store.get(c,'label');rows=[];groups=defaultdict(list)
 for dbrow in c.execute('select * from predictions order by id'):
  p=dict(dbrow);payload=json.loads(p['payload']);blockers=[];codes=set();bound=None;result_snapshots=[]
  try:
   pool=json.loads(archived(c,payload.get('candidate_pool')).read_text())
   codes={r['code'] for r in pool['candidates']}
   if pool['signal_date']!=p['signal_date'] or len(codes)!=pool['expected_count']:raise ValueError('POOL_MISMATCH')
   result_snapshots=[s for s in snaps if same_contract(p,payload,s,codes)]
   candidates=[s for s in result_snapshots if compatible(p,payload,s,codes)]
   if candidates:
    bound=min(candidates,key=lambda s:(s['state']!='COMPLETE',s['cutoff'],s['id']))
    store.raw(c,bound['id'])
   else:blockers.append('NO_SAME_POOL_PRE_CUTOFF_SNAPSHOT')
  except (ValueError,KeyError,TypeError,OSError):blockers.append('POOL_OR_SNAPSHOT_EVIDENCE_UNVERIFIED')
  if p['provenance']!='PROSPECTIVE_LOCAL':blockers.append('NOT_PROSPECTIVE')
  if bound and bound['state']!='COMPLETE':blockers.append('INCOMPLETE_FEATURE_COHORT')
  member=next((r for r in bound['rows'] if r['code']==p['code']),None) if bound else None
  out={}
  for basis in ('QUOTE_PROXY','ACTUAL'):
   direct=[dict(r) for r in c.execute('select * from outcomes o where prediction_id=? and basis=? and not exists(select 1 from outcomes n where n.supersedes=o.id)',(p['id'],basis))]
   ls=settlement_queue.latest_labels([l for l in labels if l['snapshot_id'] in {s['id'] for s in result_snapshots} and l['code']==p['code'] and l['basis']==basis])
   stage='MISSING'
   if p.get('entry_at') and p.get('exit_at'):
    stage='WAIT_ENTRY' if lab.stamp(lab.now())<lab.stamp(p['entry_at']) else ('WAIT_EXIT' if lab.stamp(lab.now())<lab.stamp(p['exit_at']) else 'MISSING')
   result={'status':stage,'source':'NONE','net':None}
   if len(direct)>1 or len(ls)>1:result.update(status='CONFLICT',source='AMBIGUOUS')
   elif direct:
    o=direct[0];result.update(status=o['status'],source='PREDICTION_OUTCOME',id=o['id'],entry_price=o['entry_price'],exit_price=o['exit_price'])
   elif ls:
    l=ls[0]
    try:
     store.raw(c,l['snapshot_id']);store.raw(c,l['id']);r=l.get('review',{})
     result.update(status=l['status'],source='LEARNING_LABEL',snapshot_id=l['snapshot_id'],id=l['id'],entry_price=r.get('entry_price'),exit_price=r.get('exit_price'),reason=r.get('reason'))
     if l['status']=='SETTLED' and not l.get('execution_reviewed'):result['status']='UNREVIEWED'
    except (ValueError,OSError):result.update(status='CORRUPT_EVIDENCE',source='LEARNING_LABEL')
   if direct and ls and (direct[0]['status']!=ls[0]['status'] or (direct[0]['status']=='SETTLED' and any(direct[0].get(k)!=ls[0].get('review',{}).get(k) for k in ('entry_price','exit_price')))):
    result.update(status='CONFLICT',source='CROSS_STORE_CONFLICT')
   if result['status']=='SETTLED':
    if lab.stamp(p['exit_at'])>lab.stamp(lab.now()):result['status']='FUTURE_RESULT'
    elif result.get('entry_price',0)>0 and result.get('exit_price') is not None:result['net']=result['exit_price']/result['entry_price']-1-p['cost']
    else:result['status']='MISSING_PRICES'
   # Outcome research includes any executable auction gap. Old conditional
   # probabilities must not be calibrated using out-of-window outcomes.
   result['probability_eligible']=True
   if payload.get('entry_scenario_id')=='GAP_0_3' and result['status']=='SETTLED':
    review=ls[0].get('review',{}) if ls and not direct else {}
    if direct:
     try:
      h=c.execute('select sha256 from artifacts where id=?',(direct[0]['artifact_id'],)).fetchone()[0]
      raw=(Path(c.execute('pragma database_list').fetchone()[2]).parent/'blobs'/h).read_bytes()
      if hashlib.sha256(raw).hexdigest()==h:review=json.loads(raw).get('execution_review',{})
     except (OSError,KeyError,TypeError,ValueError):pass
    pre=review.get('previous_close')
    result['probability_eligible']=bool(pre and result.get('entry_price') and -1e-9<=result['entry_price']/pre-1<=.03+1e-9)
    if not result['probability_eligible']:result['probability_note']='旧0–3%条件概率不适用于本笔；仅计收益复盘'
   out[basis]=result
  rated=p['p'] is not None and payload.get('independent_rank_eligible') is not False and payload.get('probability_method')!='UNINFORMED'
  r={'prediction_id':p['id'],'day':p['signal_date'],'code':p['code'],'name':p['name'],'model':p['model'],'p':p['p'],'rank_eligible':rated,'official_rank':payload.get('comparison_official_rank'),'snapshot_id':bound['id'] if bound else None,'member_state':member.get('state') if member else None,'blockers':blockers,'outcomes':out,'provenance':p['provenance'],'expected_codes':sorted(codes)}
  rows.append(r)
  # Only the exact predeclared strategy and policy enter matched diagnostics.
  if p['provenance']=='PROSPECTIVE_LOCAL' and benchmark.policy_compatible(payload) and p['strategy']==lab.STRATEGY and payload.get('entry_scenario_id') in ('GAP_0_3','ANY_EXECUTABLE_AUCTION') and abs(p['cost']-.0045)<1e-10:
   groups[p['model'],p['signal_date']].append(r)
 comparisons=[]
 for (model,day),rs in groups.items():
  full=bool(rs[0]['expected_codes']) and all(r['expected_codes']==rs[0]['expected_codes'] for r in rs) and sorted(r['code'] for r in rs)==rs[0]['expected_codes']
  for basis in ('QUOTE_PROXY','ACTUAL'):
   if not full:comparisons.append({'model':model,'basis':basis,'day':day,'state':'BLOCKED','reason':'INCOMPLETE_PREDICTION_COHORT'});continue
   cohort=[dict(code=r['code'],p=r['p'],rank_eligible=r['rank_eligible'],official_rank=r['official_rank'],**{k:r['outcomes'][basis].get(k) for k in ('status','entry_price','exit_price','probability_eligible')}) for r in rs]
   b=benchmark.compare({day:cohort},basis)
   comparisons.append({'model':model,'day':day,'basis':basis,'state':b['state'],'reason':'; '.join(x['reason'] for x in b['excluded']),'evaluation':b['days']})
 datasets={}
 for basis in ('QUOTE_PROXY','ACTUAL'):
  try:
   d=store.dataset(c,'daily_net_return_v1',basis)
   datasets[basis]={'rows':len(d['rows']),'days':len({r['day'] for r in d['rows']}),'excluded':d['excluded']}
  except (ValueError,OSError,KeyError) as e:datasets[basis]={'rows':0,'days':0,'error':type(e).__name__,'excluded':[]}
 return {'version':VERSION,'at':lab.now(),'rows':rows,'linked_predictions':sum(r['snapshot_id'] is not None for r in rows),'blockers':dict(Counter(b for r in rows for b in r['blockers'])),'datasets':datasets,'comparisons':comparisons,'queue':settlement_queue.build(c),'policy':'Read-only lineage; immutable predictions and outcomes retained. Labels remain snapshot-bound; no implicit copy or training promotion.'}
