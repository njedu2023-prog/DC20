"""Fixed-policy, purged walk-forward Ridge return + logistic/Platt probability learner.
NumPy only; no pickle, no production promotion, and no optimization on test data.
"""
import argparse,json,math
from collections import Counter,defaultdict
from pathlib import Path
import lab
from learning import store,features

def npmod():
 import numpy as np
 return np

def split(rows,policy,end=None):
 days=sorted({r['day'] for r in rows});days=days[:end] if end else days
 if len(days)<policy['train_days']+policy['calibration_days']+policy['test_days']:return None
 testdays=days[-policy['test_days']:];caldays=days[-policy['test_days']-policy['calibration_days']:-policy['test_days']];traindays=days[:-policy['test_days']-policy['calibration_days']]
 test=[r for r in rows if r['day'] in testdays];cal=[r for r in rows if r['day'] in caldays];train=[r for r in rows if r['day'] in traindays]
 strict=policy.get('strict_cutoff',False)
 if strict and any(not r.get('cutoff') or not r.get('label_available_at') for r in rows):
  raise ValueError('PREDICTION_AND_LABEL_AVAILABILITY_REQUIRED')
 boundary='cutoff' if strict else 'entry_at'
 def purge(group,cut):
  # An unavailable label excludes its whole date, never just the inconvenient stock.
  bad={r['day'] for r in group if lab.stamp(r['exit_at'])>=cut or
       (strict and lab.stamp(r['label_available_at'])>=cut)}
  return [r for r in group if r['day'] not in bad]
 testcut=min(lab.stamp(r[boundary]) for r in test)
 cal=purge(cal,testcut)
 if not cal:return None
 calcut=min(lab.stamp(r[boundary]) for r in cal)
 train=purge(train,calcut)
 if len({r['day'] for r in train})<policy['train_days']:return None
 if len({r['day'] for r in cal})<max(2,policy['calibration_days']-3):return None
 return train,cal,test

def weights(rows):
 np=npmod();counts=Counter(r['day'] for r in rows);w=np.array([1/counts[r['day']] for r in rows]);return w/w.sum()
def preprocess_fit(rows,max_missing,feature_names=None):
 np=npmod();keys=[];med=[];mu=[];sd=[];rejected={}
 for k in sorted(feature_names or features.FEATURES):
  a=np.array([r['x'].get(k) if r['x'].get(k) is not None else np.nan for r in rows],float)
  if np.isinf(a).any():raise ValueError('INFINITE_FEATURE')
  if np.isnan(a).mean()>max_missing or np.isnan(a).all():rejected[k]='EXCESSIVE_TRAIN_MISSING';continue
  median=float(np.nanmedian(a));filled=np.where(np.isnan(a),median,a)
  if float(filled.std())<1e-12:rejected[k]='CONSTANT_TRAIN_FEATURE';continue
  keys.append(k);med.append(median);mu.append(float(filled.mean()));sd.append(float(filled.std()))
 if not keys:raise ValueError('NO_USABLE_FEATURES')
 return dict(keys=keys,median=med,mean=mu,std=sd,rejected=rejected,missing_indicators=True)
def transform(rows,p):
 np=npmod();a=np.array([[r['x'].get(k) if r['x'].get(k) is not None else np.nan for k in p['keys']] for r in rows],float)
 if np.isinf(a).any():raise ValueError('INFINITE_FEATURE')
 mask=np.isnan(a);a=np.where(mask,np.array(p['median']),a);z=(a-np.array(p['mean']))/np.array(p['std'])
 return np.column_stack([np.ones(len(rows)),z,mask.astype(float)])
def ridge(x,y,w,alpha):
 np=npmod();pen=np.eye(x.shape[1])*alpha;pen[0,0]=0
 return np.linalg.solve(x.T@(w[:,None]*x)+pen,x.T@(w*y))
def sigmoid(z):
 np=npmod();return 1/(1+np.exp(-np.clip(z,-40,40)))
def logistic(x,y,w,l2,steps=1500):
 np=npmod();beta=np.zeros(x.shape[1]);pen=np.ones(x.shape[1])*l2;pen[0]=0
 lr=.8/(.25*np.linalg.norm(x*np.sqrt(w[:,None]),2)**2+l2+1e-9)
 for i in range(steps):
  gradient=x.T@(w*(sigmoid(x@beta)-y))+pen*beta
  beta-=lr*gradient
  if np.linalg.norm(gradient)<1e-7:break
 return beta

def train_bundle(train,cal,policy):
 observed=policy.get('observation_status','SETTLED')
 np=npmod();tr=[r for r in train if r['status']==observed];ca=[r for r in cal if r['status']==observed]
 if not tr or not ca:raise ValueError('NO_SETTLED_TRAIN_OR_CALIBRATION')
 pp=preprocess_fit(tr,policy['missing_train_fraction_max'],policy.get('feature_names'));x=transform(tr,pp);xc=transform(ca,pp);w=weights(tr)
 y=np.array([r['gross']-policy['cost'] for r in tr]);yc=np.array([r['gross']-policy['cost'] for r in ca]);binary=(y>0).astype(float);bc=(yc>0).astype(float)
 if len(set(binary))<2 or len(set(bc))<2:raise ValueError('ONE_CLASS_TRAIN_OR_CALIBRATION')
 rb=ridge(x,y,w,policy['ridge_alpha']);lb=logistic(x,binary,w,policy['logistic_l2'])
 # Platt parameters see calibration only, never test labels.
 platt=logistic(np.column_stack([np.ones(len(ca)),xc@lb]),bc,weights(ca),.01)
 errors=yc-xc@rb;q=np.quantile(errors,[.1,.9]).tolist()
 return dict(preprocess=pp,return_coefficients=rb.tolist(),probability_coefficients=lb.tolist(),platt=platt.tolist(),residual_band=q,band_note='calibration residual 10/90 empirical band, not guaranteed coverage',constant_return=float(w@y),constant_probability=float(w@binary),train_days=sorted({r['day'] for r in train}),calibration_days=sorted({r['day'] for r in cal}),last_fit_exit=max(r['exit_at'] for r in train+cal),last_fit_available_at=max((r.get('label_available_at',r['exit_at']) for r in train+cal),key=lab.stamp),basis=policy.get('basis'),feature_order=['intercept']+pp['keys']+['missing:'+k for k in pp['keys']])

def predict(rows,bundle):
 np=npmod();x=transform(rows,bundle['preprocess']);ret=x@np.array(bundle['return_coefficients']);logits=x@np.array(bundle['probability_coefficients']);p=sigmoid(bundle['platt'][0]+bundle['platt'][1]*logits)
 return [dict(code=r['code'],day=r['day'],expected_net=float(v),profit_probability=float(prob),return_band=[float(v+e) for e in bundle['residual_band']],rank_basis='EXPECTED_NET_RETURN',basis=bundle.get('basis'),status='QUOTE_SHADOW_UNVALIDATED' if bundle.get('basis')=='QUOTE_RETURN' else 'SHADOW_UNVALIDATED') for r,v,prob in zip(rows,ret,p)]

def select(rows,key,k=2):
 levels=sorted({r[key] for r in rows},reverse=True);remaining=min(k,len(rows));out={}
 for value in levels:
  group=[r for r in rows if r[key]==value];w=min(1,remaining/len(group))
  if w<=0:break
  out.update({r['code']:w for r in group});remaining-=w*len(group)
 return out

def evaluate(rows,pred,bundle,policy):
 np=npmod();lookup={(p['day'],p['code']):p for p in pred};by=defaultdict(list)
 for r in rows:by[r['day']].append(dict(r,**{k:v for k,v in lookup[(r['day'],r['code'])].items() if k in ('expected_net','profit_probability','return_band')}))
 out=[]
 for day,group in sorted(by.items()):
  valid=[r for r in group if r['status']==policy.get('observation_status','SETTLED')]
  if not valid:continue
  target=np.array([r['gross']-policy['cost'] for r in valid]);pv=np.array([r['profit_probability'] for r in valid]);y=(target>0).astype(float)
  pair=[]
  for i,a in enumerate(valid):
   for b in valid[i+1:]:
    dy=a['gross']-b['gross'];dp=a['expected_net']-b['expected_net']
    if abs(dy)>1e-12:pair.append(.5 if abs(dp)<1e-12 else float(dy*dp>0))
  choices={'independent_top2':select(group,'expected_net'),'equal_pool':{r['code']:1 for r in group}}
  momentum_complete=all(isinstance(r['x'].get('return_5'),(int,float)) and math.isfinite(r['x']['return_5']) for r in group)
  if momentum_complete:choices['momentum_top2']=select([dict(r,momentum=r['x']['return_5']) for r in group],'momentum')
  official={r['code']:1 for r in group if r.get('official_rank') in (1,2)}
  if len(official)==2:choices['official_top2']=official
  costs=[]
  for fee in policy['costs']:
   selections={}
   for name,chosen in choices.items():
    fills=[(r,chosen[r['code']]) for r in valid if r['code'] in chosen];den=sum(w for r,w in fills)
    selections[name]=dict(selected_weight=sum(chosen.values()),**({'observed_weight':den} if policy.get('basis')=='QUOTE_RETURN' else {'filled_weight':den}),mean_net=sum((r['gross']-fee)*w for r,w in fills)/den if den else None,worst_net=min((r['gross']-fee for r,w in fills),default=None))
   costs.append(dict(cost=fee,selections=selections))
  predret=np.array([r['expected_net'] for r in valid]);base=bundle['constant_probability']
  out.append(dict(day=day,n=len(valid),**({'unobserved':len(group)-len(valid)} if policy.get('basis')=='QUOTE_RETURN' else {'unfilled':len(group)-len(valid)}),baseline_missing=[] if momentum_complete else ['MOMENTUM_FEATURE_MISSING'],brier=float(np.mean((pv-y)**2)),brier_base=float(np.mean((base-y)**2)),log_loss=float(-np.mean(y*np.log(np.clip(pv,1e-9,1))+(1-y)*np.log(np.clip(1-pv,1e-9,1)))),return_mae=float(np.mean(abs(target-predret))),return_mae_base=float(np.mean(abs(target-bundle['constant_return']))),pairwise_concordance=sum(pair)/len(pair) if pair else None,band_coverage=sum(r['return_band'][0]<=t<=r['return_band'][1] for r,t in zip(valid,target))/len(valid),costs=costs))
 if not out:return {'days':[],'state':'NO_EVALUABLE_DAYS'}
 # Moving blocks of dates, not independent stock bootstrap.
 def ci(values):
  if len(values)<5:return None
  rng=np.random.default_rng(policy['seed']);a=np.array(values);n=len(a);length=min(5,n);means=[]
  for _ in range(1000):
   starts=rng.integers(0,n,size=math.ceil(n/length));sample=np.concatenate([a[(s+np.arange(length))%n] for s in starts])[:n];means.append(sample.mean())
  return np.quantile(means,[.025,.975]).tolist()
 gains={}
 for baseline in ('equal_pool','official_top2','momentum_top2'):
  vals=[]
  for d in out:
   s=next(z['selections'] for z in d['costs'] if abs(z['cost']-policy['cost'])<1e-12);a=s['independent_top2']['mean_net'];b=s.get(baseline,{}).get('mean_net')
   if a is not None and b is not None:vals.append(a-b)
  gains[baseline]=dict(n_days=len(vals),mean=sum(vals)/len(vals) if vals else None,block_bootstrap_95=ci(vals))
 tail={}
 for name in ('independent_top2','equal_pool','official_top2','momentum_top2'):
  vals=[next(z['selections'] for z in d['costs'] if abs(z['cost']-policy['cost'])<1e-12).get(name,{}).get('mean_net') for d in out]
  vals=sorted(v for v in vals if v is not None)
  tail[name]=dict(n_days=len(vals),worst_day=min(vals) if vals else None,worst_20pct_day_mean=sum(vals[:max(1,math.ceil(.2*len(vals)))])/max(1,math.ceil(.2*len(vals))) if vals else None)
 quote=policy.get('basis')=='QUOTE_RETURN'
 return dict(days=out,day_equal_brier=sum(d['brier'] for d in out)/len(out),day_equal_brier_base=sum(d['brier_base'] for d in out)/len(out),gains=gains,tail=tail,scope='QUOTE_RETURN_ONLY; no fill or executable probability claim' if quote else 'conditional filled equal-weight daily trade cohorts, NOT portfolio NAV',promotion='DISABLED_QUOTE_RESEARCH_ONLY' if quote else 'SHADOW_REVIEW_REQUIRED')

def fit_dataset(data,policy):
 quote=data['basis']=='QUOTE_RETURN'
 if quote and (policy.get('basis')!='QUOTE_RETURN' or policy.get('observation_status')!='VERIFIED_PRICE_PAIR' or not policy.get('strict_cutoff')):raise ValueError('QUOTE_POLICY_REQUIRED')
 if not quote and policy.get('observation_status','SETTLED')!='SETTLED':raise ValueError('BASIS_POLICY_MISMATCH')
 rows=data['rows'];days=sorted({r['day'] for r in rows});settled=[r for r in rows if r['status']==policy.get('observation_status','SETTLED')]
 if quote and (len(settled)!=len(rows) or any(not r.get('cutoff') or not r.get('label_available_at') or lab.stamp(r['cutoff'])>=lab.stamp(r['entry_at']) or lab.stamp(r['label_available_at'])<lab.stamp(r['exit_at']) for r in rows)):raise ValueError('INVALID_QUOTE_TEMPORAL_CONTRACT')
 report=dict(state='BLOCKED_INSUFFICIENT_DATA',reason='Need purged train/calibration/test dates and '+('verified full-cohort quote labels' if quote else 'settled full-cohort labels'),eligible_days=len(days),eligible_rows=len(settled),basis=data['basis'],dataset_fingerprint=data['fingerprint'],policy=policy,excluded=data['excluded'],promotion='DISABLED')
 if len(settled)<policy['min_rows']:return report
 folds=[]
 # Non-overlapping test blocks; expanding training, disjoint calibration per fold.
 for end in range(policy['train_days']+policy['calibration_days']+policy['test_days'],len(days)+1):
  if (len(days)-end)%policy['test_days']:continue
  parts=split(rows,policy,end)
  if not parts:continue
  tr,ca,te=parts
  try:
   bundle=train_bundle(tr,ca,policy);pred=predict(te,bundle);ev=evaluate(te,pred,bundle,policy)
   folds.append(dict(train_dates=bundle['train_days'],calibration_dates=bundle['calibration_days'],test_dates=sorted({r['day'] for r in te}),bundle=bundle,predictions=pred,evaluation=ev))
  except ValueError as e:report.setdefault('fold_errors',[]).append(str(e))
 if not folds:return report
 report.update(state='TRAINED_RESEARCH_ONLY',reason='Fixed-policy purged walk-forward executed; no claimed profitability',folds=folds,bundle=folds[-1]['bundle'],test_days=sorted({d for f in folds for d in f['test_dates']}),promotion='DISABLED_QUOTE_RESEARCH_ONLY' if quote else 'NEW_FORWARD_SHADOW_REQUIRED')
 return report

def train(c,method,basis):
 policy=store.register_policy(c);data=store.dataset(c,method,basis);code_sha=__import__('hashlib').sha256(Path(__file__).read_bytes()).hexdigest();key=store.digest([policy,data['fingerprint'],data['excluded'],basis,code_sha])
 old=[x for x in store.get(c,'model') if x['key']==key]
 if old:return old[-1]
 report=fit_dataset(data,policy);report.update(method=method,created_at=lab.now(),policy_sha=store.digest(policy),source_ids=sorted({r['snapshot_id'] for r in data['rows']}|{r['label_id'] for r in data['rows']}),code_sha=__import__('hashlib').sha256(Path(__file__).read_bytes()).hexdigest())
 mid=store.put(c,'model',key,report);return dict(id=mid,**report)

def shadow(c,model_id,snapshot_id):
 key=store.digest([model_id,snapshot_id])
 old=[r for r in store.get(c,'shadow') if r['key']==key]
 if old:return old[-1]['id']
 m=store.raw(c,model_id);s=store.raw(c,snapshot_id)
 if m.get('state')!='TRAINED_RESEARCH_ONLY' or s.get('state')!='COMPLETE':raise ValueError('SHADOW_NOT_READY')
 if m['method']!=s['method'] or s['provenance']!='PROSPECTIVE_LOCAL':raise ValueError('METHOD_OR_PROVENANCE_MISMATCH')
 # Prevent retrospective inference masquerading as unseen live evidence.
 if lab.stamp(m['created_at'])>=lab.stamp(s['cutoff']) or lab.stamp(lab.now())>=lab.stamp(s['entry_at']):raise ValueError('SHADOW_NOT_PRE_ENTRY')
 if s['day']<=max(m['test_days']) or lab.stamp(m['bundle']['last_fit_exit'])>=lab.stamp(s['cutoff']):raise ValueError('SHADOW_NOT_UNSEEN')
 rows=[dict(day=s['day'],code=x['code'],x=x['features']) for x in s['rows']]
 if m.get('research_proposal_id'):
  from learning import research_agent
  rows=research_agent.shadow_rows(c,m,dict(s,id=snapshot_id))
 pred=predict(rows,m['bundle'])
 pp=m['bundle']['preprocess'];x=transform(rows,pp);np=npmod();drift=dict(standardized_abs_over5_fraction=float(np.mean(abs(x[:,1:1+len(pp['keys'])])>5)),missing_fraction=float(np.mean(x[:,1+len(pp['keys']):])))
 body=dict(model_id=model_id,snapshot_id=snapshot_id,at=lab.now(),predictions=pred,drift=drift,state='DRIFT_REVIEW' if drift['standardized_abs_over5_fraction']>.2 or drift['missing_fraction']>.5 else 'SHADOW_ONLY')
 return store.put(c,'shadow',store.digest([model_id,snapshot_id]),body)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--method',default='daily_net_return_v1');p.add_argument('--basis',choices=['ACTUAL','QUOTE_PROXY'],default='QUOTE_PROXY');a=p.parse_args()
 with lab.connect() as c:r=train(c,a.method,a.basis)
 print(json.dumps({k:r.get(k) for k in ('id','state','reason','eligible_days','eligible_rows','basis')},ensure_ascii=False))
