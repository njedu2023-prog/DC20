"""Nightly price evidence capture. Never declares fills or settles a forecast."""
import argparse,json
from datetime import datetime,timezone
from pathlib import Path
import lab,ths_evidence

def jobs(predictions,at):
    now=lab.stamp(at);out={}
    for p in predictions:
        if p['provenance']!='PROSPECTIVE_LOCAL' or not p['entry_at'] or not p['exit_at']:continue
        for leg,field in [('ENTRY','entry_at'),('EXIT','exit_at')]:
            due=lab.stamp(p[field]);day=due.astimezone(timezone(__import__('datetime').timedelta(hours=8))).strftime('%Y%m%d');key=(p['code'],day,leg)
            if key not in out:out[key]=dict(code=p['code'],day=day,leg=leg,required_at=p[field],prediction_ids=[],state='DUE' if now>=due else 'WAITING')
            out[key]['prediction_ids'].append(p['id'])
    return list(out.values())

def run(c,folder,at,fetch=False):
    if folder.exists():raise ValueError('Use new output folder')
    folder.mkdir(parents=True)
    c.execute('CREATE TABLE IF NOT EXISTS quote_captures(code TEXT,day TEXT,artifact_id INTEGER REFERENCES artifacts(id),metrics TEXT,PRIMARY KEY(code,day))')
    ps=[dict(r) for r in c.execute('SELECT * FROM predictions')];plan=jobs(ps,at);results=[]
    for j in plan:
        if c.execute('SELECT 1 FROM quote_captures WHERE code=? AND day=?',(j['code'],j['day'])).fetchone():j['state']='CAPTURED_UNSETTLED'
    if fetch:
        for day in sorted({j['day'] for j in plan if j['state']=='DUE'}):
            codes=sorted({'hs_'+j['code'][:6] for j in plan if j['day']==day and j['state']=='DUE'})
            results+=ths_evidence.collect(folder/day,day,codes)
    for j in plan:
        captured=c.execute('SELECT 1 FROM quote_captures WHERE code=? AND day=?',(j['code'],j['day'])).fetchone()
        matches=[r for r in results if r['code']=='hs_'+j['code'][:6] and r.get('metrics',{}).get('date')==j['day'] and r.get('kind')=='minute' and r.get('ok')]
        if captured or matches:j['state']='CAPTURED_UNSETTLED'
        j['next_action']='等待冻结执行时点' if j['state']=='WAITING' else ('核验09:25竞价成交证据；09:30不能替代' if j['leg']=='ENTRY' else '核验10:00报价及流动性/可卖出证据')
        j['retry_policy']='下一次既定夜间执行重试；历史日期回退须拒绝，不增加白天唤醒'
    report=dict(at=at,jobs=plan,results=results,settlement_created=False,note='ENTRY minute 09:30 is not auction proof; EXIT 10:00 sampled quote is not sellability proof. Missing past dates stay missing. No automatic outcome insertion.')
    f=folder/'capture-plan.json';f.write_text(json.dumps(report,ensure_ascii=False,indent=2));lab.artifact(c,f)
    for r in results:
        if r.get('file'):
            aid=lab.artifact(c,Path(r['file']))
            if r.get('ok') and r['kind']=='minute':
                code=next(j['code'] for j in plan if j['code'][:6]==r['code'][3:])
                c.execute('INSERT OR IGNORE INTO quote_captures VALUES(?,?,?,?)',(code,r['metrics']['date'],aid,json.dumps(r['metrics'])))
    lab.event(c,'RESULT_CAPTURE',{'jobs':len(plan),'due':sum(j['state']=='DUE' for j in plan),'fetched':len(results),'settlements':0});c.commit();return report
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--fetch',action='store_true');a=p.parse_args();r=run(lab.connect(),a.out,lab.now(),a.fetch);print(json.dumps({'jobs':len(r['jobs']),'due':sum(j['state']=='DUE' for j in r['jobs']),'fetched':len(r['results'])}))
