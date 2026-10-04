#!/usr/bin/env python3
"""Local, append-only research evidence store. No broker or messaging integration."""
import argparse, csv, hashlib, json, math, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import context as research_context
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
ROOT=Path(__file__).resolve().parent
ARCHIVE=ROOT.parent/'top2-nightly'
DB=ROOT/'data'/'research.sqlite3'
STRATEGY='T_AUCTION_T1_1000_NO_HOLD_V1'
def now(): return datetime.now(timezone.utc).isoformat()
def stamp(s):
    d=datetime.fromisoformat(s.replace('Z','+00:00'))
    if d.tzinfo is None: raise ValueError('时间必须含时区')
    return d

def connect(path=DB):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(path);c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS artifacts(id INTEGER PRIMARY KEY,path TEXT NOT NULL,sha256 TEXT NOT NULL,imported_at TEXT NOT NULL,UNIQUE(path,sha256));
    CREATE TABLE IF NOT EXISTS models(id TEXT PRIMARY KEY,status TEXT NOT NULL,kind TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS predictions(id INTEGER PRIMARY KEY,signal_date TEXT NOT NULL,code TEXT NOT NULL,name TEXT NOT NULL,model TEXT NOT NULL REFERENCES models(id),strategy TEXT NOT NULL,p REAL CHECK(p IS NULL OR(p>=0 AND p<=1)),cost REAL NOT NULL CHECK(cost>=0 AND cost<1),entry_at TEXT,exit_at TEXT,cutoff TEXT,recorded_at TEXT NOT NULL,provenance TEXT NOT NULL,artifact_id INTEGER REFERENCES artifacts(id),payload TEXT NOT NULL,UNIQUE(artifact_id,code,model));
    CREATE TABLE IF NOT EXISTS outcomes(id INTEGER PRIMARY KEY,prediction_id INTEGER NOT NULL REFERENCES predictions(id),basis TEXT NOT NULL CHECK(basis IN ('ACTUAL','QUOTE_PROXY')),status TEXT NOT NULL CHECK(status IN ('SETTLED','UNFILLED','UNEXITED','MISSING')),entry_price REAL,exit_price REAL,entry_at TEXT,exit_at TEXT,recorded_at TEXT NOT NULL,evidence TEXT NOT NULL,artifact_id INTEGER NOT NULL REFERENCES artifacts(id),supersedes INTEGER UNIQUE REFERENCES outcomes(id),UNIQUE(prediction_id,artifact_id));
    CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at TEXT NOT NULL,event TEXT NOT NULL,detail TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS evaluations(id INTEGER PRIMARY KEY,at TEXT NOT NULL,model TEXT NOT NULL,basis TEXT NOT NULL,payload TEXT NOT NULL);
    ''')
    for t in ('predictions','outcomes','artifacts','models','audit','evaluations'):
        for op in ('UPDATE','DELETE'):
            c.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{t}_{op} BEFORE {op} ON {t} BEGIN SELECT RAISE(ABORT,'append-only evidence'); END")
    c.commit();return c

def event(c,e,d):c.execute('INSERT INTO audit(at,event,detail) VALUES(?,?,?)',(now(),e,json.dumps(d,ensure_ascii=False)))
def artifact(c,path):
    path=Path(path).resolve();h=hashlib.sha256(path.read_bytes()).hexdigest()
    # Preserve bytes, not just the hash of a mutable source file.
    dbfile=c.execute('PRAGMA database_list').fetchone()[2]
    store=Path(dbfile).parent/'blobs';store.mkdir(parents=True,exist_ok=True)
    target=store/h
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest()!=h:raise ValueError('归档内容哈希损坏')
    else:
        with target.open('xb') as f:f.write(path.read_bytes())
    c.execute('INSERT OR IGNORE INTO artifacts(path,sha256,imported_at) VALUES(?,?,?)',(str(path),h,now()))
    return c.execute('SELECT id FROM artifacts WHERE path=? AND sha256=?',(str(path),h)).fetchone()[0]
def model(c,name,kind='subjective'):
    c.execute('INSERT OR IGNORE INTO models VALUES(?,?,?,?)',(name,'RESEARCH_ONLY',kind,now()))
def read(path):return json.loads(Path(path).read_text())

def import_archive(c,archive=ARCHIVE):
    """Historical timestamps are declared evidence, never retroactively prospective."""
    before=c.total_changes
    for f in sorted(archive.rglob('*')):
        if f.is_file() and f.suffix in ('.json','.csv','.md','.png','.txt','.py'):artifact(c,f)
    for f in sorted(archive.rglob('probabilities*.json')):
        d=read(f);day=next((x for x in f.parts if len(x)==8 and x.isdigit()),None)
        if not day:continue
        values=d.get('profit_percent',d.get('probabilities_percent',{}))
        if not values: continue
        aid=artifact(c,f);mid='legacy-subjective:'+f.parent.name+':'+day+':'+f.stem;model(c,mid)
        metrics=read(f.parent/'metrics.json') if (f.parent/'metrics.json').exists() else {}
        prov='RETROSPECTIVE' if 'posthoc' in d.get('mode','') else 'LEGACY_UNVERIFIED_FREEZE'
        for code,p in values.items():
            if p is not None and (not isinstance(p,(float,int)) or not 0<=p<=100):raise ValueError('非法概率')
            payload={'source':str(f.relative_to(archive)),'metrics':metrics.get(code,{}),'source_metadata':d,'missing':['历史同源资金序列','Level2','固定10:00校准样本'],'entry_scenario':d.get('entry','历史情景，见原报告'),'confidence':'低 / 未校准'}
            c.execute('INSERT OR IGNORE INTO predictions(signal_date,code,name,model,strategy,p,cost,entry_at,exit_at,cutoff,recorded_at,provenance,artifact_id,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(day,code,metrics.get(code,{}).get('name',code),mid,STRATEGY,None if p is None else p/100,d.get('cost',.0045),None,d.get('exit'),d.get('data_cutoff',d.get('cutoff')),now(),prov,aid,json.dumps(payload,ensure_ascii=False)))
    # Explicit historical values in delivery manifests are importable, but not prospective proof.
    statefile=archive/'state.json'
    if statefile.exists():
        state=read(statefile);aid=artifact(c,statefile)
        for delivery in state.get('deliveries',[]):
            values=delivery.get('probabilities',{})
            if not values:continue
            day=delivery['signal_date'];mid='legacy-delivery:'+day+':'+delivery['version'];model(c,mid)
            for code,value in values.items():
                value=value.get('profit') if isinstance(value,dict) else value
                if value is not None and not 0<=value<=1:raise ValueError('非法历史概率')
                if c.execute('SELECT 1 FROM predictions WHERE model=? AND code=?',(mid,code)).fetchone():continue
                payload={'source':'state.json / '+delivery['version'],'entry_scenario':delivery.get('entry_scenario','见原始报告'),'confidence':'低 / 未校准','missing':['原始特征尚待结构化迁移','不可变事前冻结证据','固定10:00结果'],'report':delivery.get('report')}
                retrospective='intraday' in delivery.get('version','') or 'posthoc' in delivery.get('purpose','')
                c.execute('INSERT INTO predictions(signal_date,code,name,model,strategy,p,cost,recorded_at,provenance,artifact_id,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(day,code,code,mid,STRATEGY,value,.0045,now(),'RETROSPECTIVE' if retrospective else 'LEGACY_UNVERIFIED_FREEZE',aid,json.dumps(payload,ensure_ascii=False)))
    event(c,'IMPORT_ARCHIVE',{'path':str(archive),'changes':c.total_changes-before});c.commit()

def freeze(c,path):
    d=read(path);t=stamp(now());entry=stamp(d['entry_at']);ex=stamp(d['exit_at']);cut=stamp(d['information_cutoff']);available=stamp(d['latest_data_available_at'])
    if not (cut<=available<=t<entry<ex):raise ValueError('冻结时间或数据可获得时间不满足事前要求')
    if d['strategy']!=STRATEGY or not d.get('calendar_evidence'):raise ValueError('需匹配策略和交易日历依据')
    if ex.astimezone(timezone(__import__('datetime').timedelta(hours=8))).strftime('%H:%M:%S')!='10:00:00':raise ValueError('退出必须北京时间10:00:00')
    if entry.astimezone(timezone(__import__('datetime').timedelta(hours=8))).strftime('%H:%M:%S')!='09:25:00':raise ValueError('竞价成交标记须09:25:00，决策冻结须在此前')
    rows=d['predictions'];codes=[x['code'] for x in rows]
    if len(set(codes))!=len(codes) or not codes:raise ValueError('候选为空或重复')
    for x in rows:
        if x['p'] is not None and (not isinstance(x['p'],(float,int)) or not math.isfinite(x['p']) or not 0<=x['p']<=1):raise ValueError('非法概率')
        if not x.get('reason') or not x.get('data_sources'):raise ValueError('每股需要依据与数据来源')
    if not 0<=d['cost']<1:raise ValueError('非法成本')
    import research_contract
    reviews=research_contract.validate(c,d)
    # Preflight every existing row before writing any prediction in the batch.
    input_sha=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    for x in rows:
        prev=c.execute('SELECT a.sha256 FROM predictions p JOIN artifacts a ON a.id=p.artifact_id WHERE p.signal_date=? AND p.code=? AND p.model=?',(d['signal_date'],x['code'],d['model'])).fetchone()
        if prev and prev[0]!=input_sha:raise ValueError('同模型版本同D预测已冻结；修订必须新版本')
    aid=artifact(c,path);model(c,d['model'],d.get('model_kind','subjective'))
    for x in rows:
        prev=c.execute('SELECT artifact_id FROM predictions WHERE signal_date=? AND code=? AND model=?',(d['signal_date'],x['code'],d['model'])).fetchone()
        if prev and prev['artifact_id']!=aid:raise ValueError('同模型版本同D预测已冻结；修订必须新版本')
        c.execute('INSERT OR IGNORE INTO predictions(signal_date,code,name,model,strategy,p,cost,entry_at,exit_at,cutoff,recorded_at,provenance,artifact_id,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(d['signal_date'],x['code'],x['name'],d['model'],STRATEGY,x['p'],d['cost'],d['entry_at'],d['exit_at'],d['information_cutoff'],t.isoformat(),'PROSPECTIVE_LOCAL',aid,json.dumps({'entry_scenario':d['entry_scenario'],'calendar_evidence':d['calendar_evidence'],**x,**reviews[x['code']]},ensure_ascii=False)))
    event(c,'FREEZE',{'artifact':aid,'model':d['model'],'count':len(rows)});c.commit()

def outcome(c,path):
    d=read(path);p=c.execute('SELECT * FROM predictions WHERE id=?',(d['prediction_id'],)).fetchone()
    if not p:raise ValueError('预测ID不存在')
    if d['basis'] not in ('ACTUAL','QUOTE_PROXY') or d['status'] not in ('SETTLED','UNFILLED','UNEXITED','MISSING'):raise ValueError('非法结果状态')
    if not d.get('evidence'):raise ValueError('必须给出行情或成交证据')
    if d['status']=='SETTLED':
        for k in ('entry_price','exit_price'):
            if not isinstance(d.get(k),(float,int)) or not math.isfinite(d[k]) or d[k]<=0:raise ValueError('价格必须为正数')
        if not p['entry_at'] or not p['exit_at']:raise ValueError('历史预测缺少规范执行时间，先审计，不自动结算')
        if stamp(d['entry_at'])!=stamp(p['entry_at']) or stamp(d['exit_at'])!=stamp(p['exit_at']):raise ValueError('成交时间不符合固定策略；不得用收盘价替代10:00')
        if stamp(d['exit_at'])>stamp(now()):raise ValueError('不能提前结算未来结果')
    elif d.get('exit_price') is not None:raise ValueError('未结算状态不能填退出价')
    import research_contract
    research_contract.validate_outcome(c,p,d)
    aid=artifact(c,path)
    if c.execute('SELECT 1 FROM outcomes WHERE prediction_id=? AND artifact_id=?',(p['id'],aid)).fetchone():return
    prev=c.execute('SELECT * FROM outcomes WHERE prediction_id=? AND basis=? ORDER BY id DESC LIMIT 1',(p['id'],d['basis'])).fetchone()
    if (prev and d.get('supersedes')!=prev['id']) or (not prev and d.get('supersedes') is not None):raise ValueError('更正须显式引用同预测同口径的最新结果ID')
    c.execute('INSERT INTO outcomes(prediction_id,basis,status,entry_price,exit_price,entry_at,exit_at,recorded_at,evidence,artifact_id,supersedes) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(p['id'],d['basis'],d['status'],d.get('entry_price'),d.get('exit_price'),d.get('entry_at'),d.get('exit_at'),now(),d['evidence'],aid,d.get('supersedes')))
    event(c,'OUTCOME',{'prediction':p['id'],'basis':d['basis'],'status':d['status']});c.commit()

def evaluate(c,mid,basis,linked=None):
    if basis not in ('ACTUAL','QUOTE_PROXY'):raise ValueError('INVALID_BASIS')
    if linked is None:linked=__import__('learning.reconciliation',fromlist=['report']).report(c)
    identity={r['prediction_id']:r for r in linked['rows']}
    valid=[]
    for row in c.execute('SELECT * FROM predictions WHERE model=?',(mid,)):
        r=dict(row);link=identity.get(r['id']);o=(link or {}).get('outcomes',{}).get(basis,{})
        if r['provenance']=='PROSPECTIVE_LOCAL' and link and link['rank_eligible'] and o.get('status')=='SETTLED' and o.get('probability_eligible',True):
            r.update(status=o['status'],entry_price=o['entry_price'],exit_price=o['exit_price']);valid.append(r)
    out={'model':mid,'basis':basis,'n':len(valid),'days':len(set(r['signal_date'] for r in valid)),'brier':None,'log_loss':None,'mean_net_return':None,'win_rate':None,'bins':[],'cost_sensitivity':[], 'promotion':'BLOCKED_NO_OOS_CHALLENGER_EVIDENCE','notes':'仅事前本地冻结且同规则已结算样本；实际/行情代理分开。同日股票相关，未据逐股数量声称独立样本。'}
    if valid:
        ret=[r['exit_price']/r['entry_price']-1-r['cost'] for r in valid];y=[int(x>0) for x in ret];pv=[r['p'] for r in valid];n=len(valid)
        out.update(brier=sum((a-b)**2 for a,b in zip(pv,y))/n,log_loss=-sum(b*math.log(max(1e-12,a))+(1-b)*math.log(max(1e-12,1-a)) for a,b in zip(pv,y))/n,mean_net_return=sum(ret)/n,win_rate=sum(y)/n,loss_rate=sum(x<0 for x in ret)/n,flat_rate=sum(x==0 for x in ret)/n)
        for lo in range(0,100,10):
            ix=[i for i,a in enumerate(pv) if lo/100<=a<(lo+10)/100 or(lo==90 and a==1)]
            if ix:out['bins'].append({'lo':lo,'n':len(ix),'predicted':sum(pv[i] for i in ix)/len(ix),'observed':sum(y[i] for i in ix)/len(ix)})
        for cost in (.002,.0045,.008):
            rr=[r['exit_price']/r['entry_price']-1-cost for r in valid];out['cost_sensitivity'].append({'cost':cost,'mean_return':sum(rr)/n,'win_rate':sum(x>0 for x in rr)/n})
    return out

def official(archive):
    out={}
    for f in sorted(set(archive.rglob('*day_*.json')) | set(archive.rglob('formal-frozen.json'))):
        try:d=read(f)
        except (ValueError,OSError):continue
        if d.get('status')!='FROZEN_FORMAL_PROFIT_FORWARD_VALIDATION' or d.get('formal_rank_allowed') is False:continue
        if not d.get('activation_id') or not d.get('pre_cas_freeze_at_utc'):continue
        day=d.get('signal_date');rows=d.get('rows',[]);org=d.get('original_rows',[])
        if day:out[day]={'rows':rows,'promotion':org,'exec_date':d.get('exec_date'),'exit_date':d.get('exit_date'),'count':d.get('original_candidate_count'), 'source':str(f.relative_to(archive)), 'sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'frozen_at':d.get('pre_cas_freeze_at_utc'),'status':'ARCHIVED_FORMAL_NOT_LIVE'}
    return out

def research_stage(pred,at):
    """Time passage never implies a fill, loss, or settlement."""
    if pred.get('outcomes'):
        return 'RESULTS_RECORDED_SEE_BASIS'
    if pred.get('provenance')!='PROSPECTIVE_LOCAL':
        return 'LEGACY_REQUIRES_EVIDENCE_AUDIT'
    if not pred.get('entry_at') or not pred.get('exit_at'):
        return 'TIMING_EVIDENCE_MISSING'
    if stamp(at)<stamp(pred['entry_at']):return 'WAITING_ENTRY'
    if stamp(at)<stamp(pred['exit_at']):return 'WAITING_EXIT_FILL_UNCONFIRMED'
    return 'DUE_FOR_OUTCOME_EVIDENCE'

def snapshot(c,archive=ARCHIVE):
    s=read(archive/'state.json') if (archive/'state.json').exists() else {}
    forms=official(archive);pred=[]
    for row in c.execute('SELECT * FROM predictions ORDER BY signal_date DESC,id'):
        d=dict(row);d['payload']=json.loads(d['payload']);d['outcomes']=[dict(x) for x in c.execute('SELECT * FROM outcomes o WHERE prediction_id=? AND NOT EXISTS(SELECT 1 FROM outcomes n WHERE n.supersedes=o.id)',(d['id'],))]
        # Evidence may contain private execution details: only show explicit result fields, not file paths.
        for o in d['outcomes']:o.pop('evidence',None)
        d['research_stage']=research_stage(d,now())
        d['display_evidence']=__import__('dashboard_projection').project(c,d['payload'])
        pred.append(d)
    models=[dict(x) for x in c.execute('SELECT * FROM models')]
    lineage=__import__('learning.reconciliation',fromlist=['report']).report(c)
    evaluations=[evaluate(c,m['id'],b,lineage) for m in models for b in ('ACTUAL','QUOTE_PROXY')]
    days=sorted(set(forms)|set(x['signal_date'] for x in pred)|{p.name for p in archive.iterdir() if p.is_dir() and len(p.name)==8 and p.name.isdigit()},reverse=True)
    delivery=[{'signal_date':x.get('signal_date'),'status':x.get('status'),'sent_at':x.get('sent_at_local'),'version':x.get('version')} for x in s.get('deliveries',[])]
    missing=[]
    for day in days:
        if not any(x['signal_date']==day for x in pred):missing.append({'severity':'warning','message':f'D{day} 缺结构化预测；报告可能已归档，尚未完成规范迁移'})
    for f in archive.rglob('probabilities*.json'):
        h=hashlib.sha256(f.read_bytes()).hexdigest()
        if not c.execute('SELECT 1 FROM artifacts WHERE path=? AND sha256=?',(str(f.resolve()),h)).fetchone():missing.append({'severity':'warning','message':str(f.relative_to(archive))+' 有未导入新版本'})
    return {'effectiveness':__import__('learning.effectiveness',fromlist=['latest']).latest(c),'reconciliation':lineage,'learning':__import__('learning.store',fromlist=['dashboard']).dashboard(c),'closed_loop':__import__('operations').dashboard(c),'research_context':research_context.latest(c),'generated_at':now(),'db_integrity':c.execute('PRAGMA integrity_check').fetchone()[0],'artifacts':c.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0],'days':days,'official':forms,'predictions':pred,'models':models,'evaluations':evaluations,'pending':s.get('pending_signal_dates',[]),'last_check':s.get('last_completed_check_at'),'schedule':s.get('last_verified_schedule'),'delivery':delivery,'alerts':missing,'audit':[dict(r) for r in c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 30')],'status':{'data_adapter':'THS_CHART_BATCH_AND_NATIVE_EVIDENCE','outcome_adapter':'QUOTE_CAPTURE_PLUS_REVIEWED_IMPORT','trainer':'RIDGE_LOGISTIC_CALIBRATION_RESEARCH_ONLY','promotion':'DISABLED','live_ths':'NOT_CONNECTED','scheduler':'EXTERNAL_RECORDED_STATE_NOT_LIVE'}}

def serve(db,port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get('Host','').split(':')[0] not in ('127.0.0.1','localhost'):self.send_error(403);return
            route=urlparse(self.path).path
            if route=='/api/state':
                with connect(db) as c:body=json.dumps(snapshot(c),ensure_ascii=False).encode()
                typ='application/json; charset=utf-8'
            elif route in ('/','/app.js','/style.css'):
                file=ROOT/'web'/({'/':'index.html'}.get(route,route[1:]));body=file.read_bytes();typ={'.html':'text/html','.js':'text/javascript','.css':'text/css'}[file.suffix]+'; charset=utf-8'
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',typ);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'");self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    print(f'Local research dashboard http://127.0.0.1:{port}',flush=True);ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()

def main():
    a=argparse.ArgumentParser();a.add_argument('--db',type=Path,default=DB);sub=a.add_subparsers(dest='command',required=True)
    sub.add_parser('init');sub.add_parser('import-archive');sub.add_parser('snapshot');s=sub.add_parser('serve');s.add_argument('--port',type=int,default=8766)
    for name in ('freeze','outcome','research-context'):s=sub.add_parser(name);s.add_argument('file',type=Path)
    s=sub.add_parser('evaluate');s.add_argument('model');s.add_argument('--basis',choices=['ACTUAL','QUOTE_PROXY'],required=True)
    s=sub.add_parser('backup');s.add_argument('file',type=Path)
    args=a.parse_args()
    if args.command=='serve':serve(args.db,args.port);return
    with connect(args.db) as c:
        if args.command=='import-archive':import_archive(c)
        elif args.command=='research-context':
            report=research_context.ingest(c,args.file,artifact,event,now);print(json.dumps({'eligible':report['eligible_n'],'observed':len(report['observations'])}))
        elif args.command=='freeze':freeze(c,args.file)
        elif args.command=='outcome':outcome(c,args.file)
        elif args.command=='snapshot':print(json.dumps(snapshot(c),ensure_ascii=False))
        elif args.command=='evaluate':
            e=evaluate(c,args.model,args.basis);c.execute('INSERT INTO evaluations(at,model,basis,payload) VALUES(?,?,?,?)',(now(),args.model,args.basis,json.dumps(e)));print(json.dumps(e,ensure_ascii=False))
        elif args.command=='backup':
            if args.file.exists():raise ValueError('不覆盖已有备份')
            with sqlite3.connect(args.file) as b:c.backup(b)
            print(args.file)
if __name__=='__main__':main()
