"""Recovery planning and isolated restore drills; never overwrite a live database."""
import json,hashlib,tempfile,shutil,sqlite3
from pathlib import Path
import lab,operations
from learning import store,pipeline,features

def catchup(c,day,query=None,max_days=5):
 q=query or pipeline.tushare_access.query
 pools=store.get(c,'pool')
 if not pools:return {'state':'NO_REGISTERED_START','jobs':[],'remaining':[]}
 live= [x['day'] for x in store.get(c,'snapshot') if x.get('provenance')=='PROSPECTIVE_LOCAL']
 start=min(live) if live else min(x['signal_date'] for x in pools)
 z=pipeline.fetch(c,'trade_cal',{'exchange':'SSE','start_date':start,'end_date':day},q)
 rs=features.records(z['data'])
 from datetime import datetime,timedelta
 lo=datetime.strptime(start,'%Y%m%d');hi=datetime.strptime(day,'%Y%m%d')
 expected=[(lo+timedelta(days=i)).strftime('%Y%m%d') for i in range((hi-lo).days+1)]
 if sorted(r.get('cal_date') for r in rs)!=expected or any(r.get('exchange')!='SSE' or r.get('is_open') not in (0,1) for r in rs):raise ValueError('INCOMPLETE_CATCHUP_CALENDAR')
 done={s['day'] for s in store.get(c,'snapshot') if s['state'] in ('COMPLETE','NO_CANDIDATES')}
 dates=sorted(r['cal_date'] for r in rs if r['is_open']==1 and r['cal_date']<day and r['cal_date'] not in done)
 jobs=[pipeline.run(c,d,query=q) for d in dates[:max_days]]
 return {'state':'RECOVERY_CHECKED','start':start,'jobs':jobs,'remaining':[d for d in dates if not any(j['day']==d and j['state'] in ('COMPLETE','NO_CANDIDATES') for j in jobs)],'note':'Late snapshots remain retrospective; no fabricated pre-event prediction'}

def integrity(c):
 errors=[]
 if c.execute('pragma integrity_check').fetchone()[0]!='ok':errors.append('SQLITE_INTEGRITY')
 if c.execute('pragma foreign_key_check').fetchall():errors.append('FOREIGN_KEY')
 for r in c.execute('select * from learning_records'):
  try:
   obj=store.raw(c,r['id'])
   if store.digest(obj)!=r['sha'] or obj!=json.loads(r['payload']):errors.append('RECORD_MISMATCH_'+str(r['id']))
  except Exception:errors.append('EVIDENCE_CORRUPT_'+str(r['id']))
 return {'state':'VERIFIED' if not errors else 'FAILED','errors':errors,'learning_records':c.execute('select count(*) from learning_records').fetchone()[0]}

def restore_drill(backup):
 operations.verify_backup(backup)
 with tempfile.TemporaryDirectory(prefix='dc20-restore-') as d:
  target=Path(d)/'copy';shutil.copytree(backup,target)
  with lab.connect(target/'research.sqlite3') as c:
   store.schema(c);result=integrity(c)
   result['predictions']=c.execute('select count(*) from predictions').fetchone()[0]
   result['outcomes']=c.execute('select count(*) from outcomes').fetchone()[0]
 result['mode']='ISOLATED_COPY_ONLY';return result
