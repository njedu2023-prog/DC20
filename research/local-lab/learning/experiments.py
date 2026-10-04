"""Predeclared feature ablations; no winner selection on test data, no promotion."""
import copy,json,math,hashlib
from pathlib import Path
import lab
from learning import store,trainer,features
PLAN={'version':'ABLATION_PLAN_V1','families':['BASELINE','PLUS_FLOW','PLUS_FLOW_LIMIT_EMOTION'],'max_variants':3,'selection':'NO_AUTOMATIC_WINNER','minimum_new_forward_days':20,'multiple_testing':'Holm correction of predeclared day-block comparisons; exploratory tests cannot grant activation'}

def attach(c,data):
 enrich=store.get(c,'enrichment');out=copy.deepcopy(data);out['rows']=[];excluded=list(data['excluded']);by={}
 for r in data['rows']:by.setdefault(r['snapshot_id'],[]).append(r)
 for sid,rs in by.items():
  candidates=[e for e in enrich if e['snapshot_id']==sid and e['provenance']=='PROSPECTIVE_LOCAL' and lab.stamp(e['cutoff'])<=lab.stamp(e['_recorded_at'])<lab.stamp(rs[0]['entry_at'])]
  if not candidates:excluded.append({'day':rs[0]['day'],'reason':'NO_PRE_ENTRY_ENRICHMENT'});continue
  e=min(candidates,key=lambda x:x['id']);store.raw(c,e['id'])
  for rid in e['raw_ids']:
   raw=store.raw(c,rid)
   if lab.stamp(raw['captured_at'])>lab.stamp(e['cutoff']):raise ValueError('ENRICHMENT_FUTURE_SOURCE')
  mapped={r['code']:r for r in e['rows']}
  if set(mapped)!={r['code'] for r in rs}:raise ValueError('ENRICHMENT_COHORT_MISMATCH')
  for r in rs:
   v=mapped[r['code']]['features'];r['x'].update(v);r['x'].update(e['shared']['indices']);r['enrichment_id']=e['id'];out['rows'].append(r)
 out['excluded']=excluded;out['fingerprint']=store.digest(out['rows']);return out

def run(c,method='daily_net_return_v1',basis='QUOTE_PROXY'):
 store.put(c,'experiment_plan',PLAN['version'],PLAN)
 data=attach(c,store.dataset(c,method,basis));base=store.register_policy(c)
 code_sha=hashlib.sha256(Path(__file__).read_bytes()+Path(trainer.__file__).read_bytes()).hexdigest();key=store.digest([PLAN,data['fingerprint'],data['excluded'],basis,code_sha])
 old=[r for r in store.get(c,'experiment') if r['key']==key]
 if old:return old[-1]
 outputs=[]
 for family in PLAN['families']:
  policy=copy.deepcopy(base);keys=list(features.FEATURES)
  if family!='BASELINE':keys+=[f'flow_{k}_{n}' for k in ('net_cny','positive_ratio') for n in (1,3,5,10,20)]
  if family=='PLUS_FLOW_LIMIT_EMOTION':keys+=['limit_open_count']+[f'{code}_return_{n}' for code in ('883900','883958') for n in (1,3,5,10,20)]
  policy['feature_names']=keys;policy['experiment_family']=family
  report=trainer.fit_dataset(data,policy)
  outputs.append({'family':family,'report':report})
 body={'at':lab.now(),'code_sha':code_sha,'plan':PLAN,'basis':basis,'method':method,'dataset_sha':data['fingerprint'],'variants':outputs,'state':'RESEARCH_COMPARISON_ONLY','promotion':False,'note':'Identical eligible cohort for all ablations. Coefficients may train, but no test winner becomes production.'}
 rid=store.put(c,'experiment',key,body);return dict(id=rid,**body)

def holm(pvalues,alpha=.05):
 result={k:False for k in pvalues};ordered=sorted(pvalues,key=pvalues.get)
 for i,k in enumerate(ordered):
  p=pvalues[k]
  if not isinstance(p,(int,float)) or not math.isfinite(p) or not 0<=p<=1:raise ValueError('BAD_PVALUE')
 for i,k in enumerate(ordered):
  if pvalues[k]>alpha/(len(ordered)-i):break
  result[k]=True
 return result
