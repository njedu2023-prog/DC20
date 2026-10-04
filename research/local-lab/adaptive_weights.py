"""Versioned research weights and outcome-based challengers, never probability calibration."""
import argparse, hashlib, json, math
from datetime import datetime
from pathlib import Path
import lab
ROOT=Path(__file__).resolve().parent

def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def validate(cfg):
    w=cfg['weights']
    if not w or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) or x<0 for x in w.values()) or abs(sum(w.values())-1)>1e-9:raise ValueError('权重必须非负、有限且合计1')
    if set(w)!=set(cfg['dimensions']):raise ValueError('维度不一致')
    lab.stamp(cfg['effective_at'])
    if cfg['kind']!='RESEARCH_SCORE_NOT_PROBABILITY':raise ValueError('不允许把研究分数标作概率')
    return cfg

def schema(c):
    c.execute('CREATE TABLE IF NOT EXISTS research_weight_versions(version TEXT PRIMARY KEY,sha TEXT NOT NULL,artifact_id INTEGER NOT NULL,payload TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS weight_challenges(id INTEGER PRIMARY KEY,recorded_at TEXT NOT NULL,artifact_id INTEGER NOT NULL UNIQUE,payload TEXT NOT NULL)')
    for t in ('research_weight_versions','weight_challenges'):
        for op in ('UPDATE','DELETE'):c.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{t}_{op} BEFORE {op} ON {t} BEGIN SELECT RAISE(ABORT,'append-only weights'); END")

def register(c,path):
    cfg=validate(json.loads(Path(path).read_text()));schema(c);h=digest(cfg)
    old=c.execute('SELECT sha FROM research_weight_versions WHERE version=?',(cfg['version'],)).fetchone()
    if old and old[0]!=h:raise ValueError('同版本不可覆盖，请建立新版本')
    if not old:
        aid=lab.artifact(c,path);c.execute('INSERT INTO research_weight_versions VALUES(?,?,?,?)',(cfg['version'],h,aid,json.dumps(cfg,ensure_ascii=False)))
        lab.event(c,'RESEARCH_WEIGHT_REGISTER',{'version':cfg['version'],'sha':h,'kind':cfg['kind']});c.commit()
    return h

def score(cfg,features,cutoff):
    validate(cfg);cut=lab.stamp(cutoff)
    if lab.stamp(cfg['effective_at'])>cut:raise ValueError('权重版本尚未生效，不可回填旧预测')
    unknown=set(features)-set(cfg['weights'])
    if unknown:raise ValueError('未知维度')
    used=set();contributions={};missing={};covered=0;total=0
    for k,w in cfg['weights'].items():
        f=features.get(k,{})
        valid=f.get('status')=='VERIFIED' and bool(f.get('evidence_ids')) and bool(f.get('rationale'))
        try:valid=valid and lab.stamp(f['available_at'])<=cut and lab.stamp(f['captured_at'])<=cut
        except (KeyError,ValueError):valid=False
        if not valid:missing[k]=w;continue
        x=f.get('score')
        if isinstance(x,bool) or not isinstance(x,(float,int)) or not math.isfinite(x) or not -1<=x<=1:raise ValueError('因子分必须在-1到1')
        roots=set(f['evidence_ids'])
        if used & roots:raise ValueError('相同证据跨维度重复加权；请指定主要归因维度')
        used|=roots;covered+=w;total+=w*x;contributions[k]={'score':x,'weight':w,'contribution':w*x,'evidence_ids':sorted(roots)}
    u=sum(missing.values())
    return {'version':cfg['version'],'coverage_weight':covered,'contributions':contributions,'missing_weights':missing,'score':total if not missing else None,'score_bounds':[max(-1,total-u),min(1,total+u)],'probability':None,'status':'RESEARCH_ONLY' if not missing else 'INCOMPLETE_NO_RANK','note':'研究评分非概率；缺失权重未重新分配；区间为缺失项数学界限，非置信区间'}

def concordance(rows,w):
    """Equal weight per signal day; compare return ordering within a day."""
    by={}
    for r in rows:by.setdefault(r['day'],[]).append(r)
    days=[]
    for group in by.values():
        pairs=[]
        for i,a in enumerate(group):
            for b in group[i+1:]:
                dy=a['net_return']-b['net_return']
                if abs(dy)<1e-12:continue
                ds=sum(w[k]*(a['x'][k]-b['x'][k]) for k in w)
                pairs.append(.5 if abs(ds)<1e-12 else float(ds*dy>0))
        if pairs:days.append(sum(pairs)/len(pairs))
    return sum(days)/len(days) if days else None

def propose(cfg,rows):
    """Training-only finite candidate search, chronological purged holdout; no promotion."""
    days=sorted({r['day'] for r in rows});g=cfg['evolution'];base=cfg['weights']
    out={'version':cfg['version'],'n':len(rows),'days':len(days),'state':'BLOCKED_INSUFFICIENT_ELIGIBLE_RESULTS','promotion':'DISABLED','probability_calibration':'NOT_IMPLEMENTED'}
    if len(days)<g['min_days'] or len(rows)<g['min_rows']:return out
    split=days[-g['holdout_days']];test=[r for r in rows if r['day']>=split]
    earliest_entry=min(lab.stamp(r['entry_at']) for r in test)
    train=[r for r in rows if r['day']<split and lab.stamp(r['exit_at'])<earliest_entry]
    if len({r['day'] for r in train})<g['min_train_days']:return out
    def objective(w):
        q=concordance(train,w)
        return -1 if q is None else q-g['regularization']*sum((w[k]-base[k])**2 for k in w)
    best=dict(base);value=objective(best)
    # One bounded transfer per proposal avoids a many-parameter fit to a short history.
    for a in base:
        for b in base:
            if a==b or base[b]<g['max_change']:continue
            candidate=dict(base);candidate[a]+=g['max_change'];candidate[b]-=g['max_change']
            v=objective(candidate)
            if v>value+1e-12:best,value=candidate,v
    out.update(state='SHADOW_PROPOSAL_ONLY',proposed_weights=best,train_days=len({r['day'] for r in train}),holdout_start=split,holdout_days=len({r['day'] for r in test}),train_baseline=concordance(train,base),train_challenger=concordance(train,best),holdout_baseline=concordance(test,base),holdout_challenger=concordance(test,best),prediction_ids=[r['id'] for r in rows],note='按日等权的收益排序一致率，非胜率/Brier；留出集只评价，不能据此反复选权重。需后续新样本影子验证，禁止自动晋升。')
    return out

def eligible(c,cfg,model):
    rows=c.execute('''SELECT p.*,o.id oid,o.entry_price,o.exit_price,o.entry_at oe,o.exit_at ox FROM predictions p JOIN outcomes o ON o.prediction_id=p.id WHERE p.model=? AND p.provenance='PROSPECTIVE_LOCAL' AND o.basis='ACTUAL' AND o.status='SETTLED' AND NOT EXISTS(SELECT 1 FROM outcomes n WHERE n.supersedes=o.id)''',(model,)).fetchall()
    good=[];seen=set();excluded=[]
    counts={}
    for r in rows:counts[(r["signal_date"],r["code"])]=counts.get((r["signal_date"],r["code"]),0)+1
    for r in rows:
        try:
            p=json.loads(r['payload']);f=p['weighted_features'];key=(r['signal_date'],r['code'])
            if counts[key]!=1:raise ValueError('同日股票结果重复或冲突，全部排除')
            seen.add(key)
            if r['strategy']!=lab.STRATEGY or abs(r['cost']-.0045)>1e-10 or p.get('weight_version')!=cfg['version']:raise ValueError('规则或版本不一致')
            if p.get('entry_scenario_id')!='GAP_0_3' or not p.get('feature_schema')==cfg['feature_schema']:raise ValueError('入场情景/因子定义不一致')
            if not(lab.stamp(r['cutoff'])<=lab.stamp(r['recorded_at'])<lab.stamp(r['entry_at'])):raise ValueError('非事前冻结')
            if lab.stamp(r['exit_at'])>lab.stamp(lab.now()):raise ValueError('未来结果')
            if r['oe']!=r['entry_at'] or r['ox']!=r['exit_at']:raise ValueError('执行时点不一致')
            s=score(cfg,f,r['cutoff'])
            if s['missing_weights']:raise ValueError('因子不完整')
            good.append({'id':r['id'],'outcome_id':r['oid'],'day':r['signal_date'],'entry_at':r['entry_at'],'exit_at':r['exit_at'],'x':{k:v['score'] for k,v in f.items()},'net_return':r['exit_price']/r['entry_price']-1-r['cost']})
        except (KeyError,ValueError,TypeError) as e:excluded.append({'prediction_id':r['id'],'reason':str(e)})
    return good,excluded

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--config',default=str(ROOT/'weight-policy-v1.json'));ap.add_argument('--out',required=True);ap.add_argument('--model',default='subjective_evidence_review_20260926_v2');a=ap.parse_args()
    c=lab.connect();cfg=json.loads(Path(a.config).read_text());register(c,a.config);rows,excluded=eligible(c,cfg,a.model);report=propose(cfg,rows);report.update(recorded_at=lab.now(),model=a.model,excluded=excluded,sample_fingerprint=digest(rows))
    path=Path(a.out);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    aid=lab.artifact(c,path);c.execute('INSERT INTO weight_challenges(recorded_at,artifact_id,payload) VALUES(?,?,?)',(lab.now(),aid,json.dumps(report,ensure_ascii=False)));lab.event(c,'WEIGHT_CHALLENGE',report);c.commit();print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()
