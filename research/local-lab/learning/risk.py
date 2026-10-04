"""Date-level tail, coverage and predeclared multiple-comparison checks."""
import math
from learning import experiments,trainer

def block_pvalue(values,seed=20260927):
 if len(values)<20:return None
 np=trainer.npmod();a=np.array(values);blocks=[a[i:i+5] for i in range(0,len(a),5)]
 rng=np.random.default_rng(seed);obs=a.mean();count=0
 # One-sided approximate block sign randomization, symmetric-null assumption explicit.
 for _ in range(4000):
  signs=rng.choice([-1,1],len(blocks));null=sum(sign*b.sum() for sign,b in zip(signs,blocks))/len(a)
  count+=null>=obs
 return (count+1)/4001

def expected_shortfall(values):
 if not values:return None
 values=sorted(values);k=max(1,math.ceil(.05*len(values)));return sum(values[:k])/k

def assess(evaluation):
 days=evaluation.get('days',[]);p={};metrics={};reasons=[]
 for name in ('equal_pool','official_top2','momentum_top2'):
  own=[];base=[];diff=[]
  for d in days:
   s=d['costs'][1]['selections'];a=s['independent_top2'];b=s.get(name)
   if not b or a['mean_net'] is None or b['mean_net'] is None:continue
   own.append(a['mean_net']);base.append(b['mean_net']);diff.append(a['mean_net']-b['mean_net'])
  pv=block_pvalue(diff);p[name]=pv if pv is not None else 1.
  ea,eb=expected_shortfall(own),expected_shortfall(base)
  metrics[name]={'dates':len(diff),'own_es5':ea,'baseline_es5':eb,'block_randomization_p':pv}
  if ea is not None and eb is not None and ea<eb-.02:reasons.append('TAIL_DEGRADED_'+name)
 accepted=experiments.holm(p)
 if not all(accepted.values()):reasons.append('MULTIPLE_COMPARISON_NOT_CONFIRMED')
 for d in days:
  s=d['costs'][1]['selections'];a=s['independent_top2'];b=s['equal_pool']
  if a['selected_weight'] and b['selected_weight'] and a['filled_weight']/a['selected_weight']<b['filled_weight']/b['selected_weight']-.1:reasons.append('FILL_COVERAGE_REVIEW');break
 return {'reasons':reasons,'metrics':metrics,'holm_pass':accepted,'note':'Approximate symmetric-null block test; not proof of alpha, no portfolio NAV. Repeated model search needs separate fresh holdout.'}
