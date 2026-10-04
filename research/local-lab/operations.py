"""Local nightly maintenance: evidence capture, immutable backup, actionable gaps; no messaging."""
import argparse,hashlib,json,os,shutil,sqlite3,fcntl
from contextlib import contextmanager,closing
from pathlib import Path
import lab,result_capture

@contextmanager
def lock(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+') as f:
        try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Another local research run is active')
        try:yield
        finally:fcntl.flock(f,fcntl.LOCK_UN)

def backup(c,folder):
    if c.in_transaction:raise ValueError("Commit pending writes before backup; refusing self-wait")
    folder.mkdir(parents=True,exist_ok=False)
    db=folder/'research.sqlite3'
    with closing(sqlite3.connect(db)) as b:c.backup(b)
    source=Path(c.execute('PRAGMA database_list').fetchone()[2]).parent/'blobs'
    target=folder/'blobs';target.mkdir()
    hashes=sorted({r[0] for r in c.execute('SELECT sha256 FROM artifacts')})
    for h in hashes:
        raw=(source/h).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=h:raise ValueError('Source blob corrupted: '+h)
        (target/h).write_bytes(raw)
    report=verify_backup(folder)
    (folder/'verification.json').write_text(json.dumps(report,indent=2));return report

def verify_backup(folder):
    # Read back the copied database and every referenced byte, without touching live storage.
    with closing(sqlite3.connect('file:'+str((folder/'research.sqlite3').resolve())+'?mode=ro',uri=True)) as b:
        if b.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or b.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Backup database invalid')
        hashes={r[0] for r in b.execute('SELECT sha256 FROM artifacts')}
        for h in hashes:
            if hashlib.sha256((folder/'blobs'/h).read_bytes()).hexdigest()!=h:raise ValueError('Backup blob invalid')
        counts={t:b.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('predictions','outcomes','artifacts')}
    return dict(status='VERIFIED_READBACK',blobs=len(hashes),counts=counts,note='SQLite及所有引用原文已回读；未替换生产库。')

def gaps(c):
    rows=[dict(r) for r in c.execute('SELECT * FROM predictions')];out=[]
    for p in rows:
        if p['provenance']!='PROSPECTIVE_LOCAL':continue
        data=json.loads(p['payload'])
        if not data.get('research_contract'):out.append(dict(prediction_id=p['id'],code=p['code'],kind='LEGACY_CONTRACT',action='保留旧预测；新版本需完整契约，不回填旧因子'))
    return out

def run(c,folder,fetch=False,trigger_origin='MANUAL',trigger_id=None,run_id=None,resume=False):
    db=Path(c.execute('PRAGMA database_list').fetchone()[2])
    with lock(db.parent/'nightly.lock'):
        folder.mkdir(parents=True,exist_ok=False)
        started=lab.now();report=dict(started_at=started,state='RUNNING',steps={},gaps=gaps(c))
        context=None
        try:
            report['steps']['backup']=backup(c,folder/'backup')
            from learning import run_tracking
            context=run_tracking.begin(c,run_id,trigger_origin,trigger_id,resume)
            report['run_context']={k:context.get(k) for k in ('run_id','origin','trigger_id','attempt','reused')}
            if context.get('reused'):
                report.update(state='ALREADY_RECORDED',tracking=context,finished_at=lab.now())
                (folder/'run.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
                return report
            if fetch:
                from learning import nightly
                report['steps']['learning']=nightly.run(c,run_context=report['run_context'])
            capture=result_capture.run(c,folder/'capture',started,fetch=fetch)
            report['steps']['capture']={'jobs':capture['jobs'],'attempts':len(capture['results']),'failures':[x for x in capture['results'] if not x.get('ok')]}
            report['state']='NEEDS_EVIDENCE' if report['steps']['capture']['failures'] or any(j['state']=='DUE' for j in capture['jobs']) else 'WAITING_FUTURE_RESULTS'
            learning=report['steps'].get('learning',{})
            if learning.get('intake',{}).get('state') in ('BLOCKED','INCOMPLETE') or learning.get('catchup',{}).get('remaining') or any(e.get('issues',0) for e in learning.get('enrichment',[])):report['state']='NEEDS_EVIDENCE'
        except Exception as e:
            report['state']='FAILED';report['error']=str(e)
        if context:
            from learning import run_tracking
            report['tracking']=run_tracking.maintenance_finished(c,context,report)
        report['finished_at']=lab.now();p=folder/'run.json';p.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        aid=lab.artifact(c,p);lab.event(c,'NIGHTLY_MAINTENANCE',dict(artifact_id=aid,state=report['state']));c.commit()
        return report

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--db',type=Path,default=lab.DB);a.add_argument('--out',type=Path,required=True);a.add_argument('--fetch',action='store_true')
    a.add_argument('--trigger-origin',choices=['MANUAL','ENGINEERING_ACCEPTANCE','SCHEDULED'],default='MANUAL');a.add_argument('--trigger-id');a.add_argument('--run-id');a.add_argument('--resume',action='store_true');o=a.parse_args()
    with lab.connect(o.db) as c:r=run(c,o.out,o.fetch,o.trigger_origin,o.trigger_id,o.run_id,o.resume)
    print(json.dumps(r,ensure_ascii=False));raise SystemExit(1 if r['state']=='FAILED' else 0)

def delivery_actions(records):
    """Read-only retry classification. Never sends or treats one recipient as another."""
    latest={}
    for r in records:latest[(r.get('recipient'),r.get('signal_date'),r.get('fingerprint') or r.get('image_sha256'),r.get('version'))]=r
    out=[]
    for key,r in latest.items():
        status=r.get('status','UNKNOWN')
        if status=='DELIVERED':continue
        action='RETRY_NEXT_AUTHORIZED_NIGHT' if status in ('FAILED','NOT_SENT') else 'CHECK_CONVERSATION_BEFORE_ANY_RESEND'
        out.append(dict(recipient=key[0],signal_date=key[1],version=key[3],action=action))
    return out

def dashboard(c):
    ps=[dict(r) for r in c.execute("SELECT payload FROM predictions WHERE provenance='PROSPECTIVE_LOCAL'")]
    contracts=[json.loads(p['payload']) for p in ps if json.loads(p['payload']).get('research_contract')=='RESEARCH_FREEZE_V1']
    row=c.execute("SELECT detail FROM audit WHERE event='NIGHTLY_MAINTENANCE' ORDER BY id DESC LIMIT 1").fetchone();last=None
    if row:
        aid=json.loads(row[0]).get('artifact_id');r=c.execute('SELECT sha256 FROM artifacts WHERE id=?',(aid,)).fetchone()
        if r:
            blob=Path(c.execute('PRAGMA database_list').fetchone()[2]).parent/'blobs'/r[0]
            data=json.loads(blob.read_text());last={'state':data['state'],'at':data['finished_at'],'backup':data['steps'].get('backup'),'capture_jobs':data['steps'].get('capture',{}).get('jobs',[]),'failures':len(data['steps'].get('capture',{}).get('failures',[]))}
    return dict(contract_version='RESEARCH_FREEZE_V1',prospective=len(ps),contract_bound=len(contracts),complete_features=sum(not p['weighted_review']['missing_weights'] for p in contracts),outcomes=c.execute('SELECT count(*) FROM outcomes').fetchone()[0],last_run=last,probability_calibration='NOT_IMPLEMENTED',promotion='DISABLED')
