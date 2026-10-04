"""Called by existing night maintenance; never creates schedules or sends messages."""
import json
from datetime import datetime,timezone,timedelta
import lab
from learning import pipeline,store,trainer,features,review,enrichment,reliability,settlement_queue,experiments,membership,primary_news,research_agent

def run(c,day=None,query=None,run_context=None):
 day=day or datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d')
 q=query or pipeline.tushare_access.query
 report={'at':lab.now(),'day':day}
 try:
  cal=pipeline.fetch(c,'trade_cal',{'exchange':'SSE','start_date':day,'end_date':day},q)
  rows=features.records(cal['data'])
  if len(rows)!=1 or rows[0]['cal_date']!=day or rows[0]['exchange']!='SSE':raise ValueError('EXACT_DAY_CALENDAR_REQUIRED')
  report['intake']=pipeline.run(c,day,query=q) if rows[0]['is_open']==1 else {'state':'NON_SESSION'}
 except Exception as e:report['intake']={'state':'BLOCKED','reason':type(e).__name__}
 report['catchup']=reliability.catchup(c,day,q)
 report['enrichment']=[]
 report['membership']=[]
 report['primary_news_queue']=[]
 for run in [report['intake']]+report['catchup']['jobs']:
  if run.get('state') in ('COMPLETE','INCOMPLETE'):
   e=enrichment.collect(c,run['snapshot_id'],q)
   report['enrichment'].append(e)
   report['membership'].append(membership.collect(c,run['snapshot_id'],e['id'],q))
   report['primary_news_queue'].append(primary_news.queue(c,run['snapshot_id'],e['id']))
 report['shadows']=[]
 latest={}
 for event in store.get(c,'lifecycle'):latest[event['model_id']]=event['action']
 for mid,action in latest.items():
  if action!='SHADOW':continue
  sid=report['intake'].get('snapshot_id')
  if sid:
   try:report['shadows'].append({'model':mid,'shadow_id':trainer.shadow(c,mid,sid)})
   except ValueError as e:report['shadows'].append({'model':mid,'state':'BLOCKED','reason':str(e)})
 report['capture']=pipeline.capture_due(c,q)
 from learning import quote_review
 report['scenario_audit']=quote_review.run(c)
 from learning import effectiveness
 report['quote_context']=effectiveness.refresh_context(c,q)
 report['effectiveness']=effectiveness.publish(c)
 from learning import quote_learning
 report['quote_learning']=quote_learning.run(c,day)
 report['quote_shadows']=[]
 sid=report['intake'].get('snapshot_id')
 if sid:
  candidates=[m for m in store.get(c,'quote_model') if m.get('state')=='TRAINED_RESEARCH_ONLY']
  # One latest baseline per lineage; no selection by test performance.
  by_lineage={m.get('research_proposal_id','baseline'):m for m in candidates}
  for model in by_lineage.values():
   try:report['quote_shadows'].append(dict(model_id=model['id'],shadow_id=quote_learning.shadow(c,model['id'],sid)))
   except ValueError as exc:report['quote_shadows'].append(dict(model_id=model['id'],state='BLOCKED',reason=str(exc)))
 report['settlement_queue']=settlement_queue.build(c)
 from learning import reconciliation
 report['reconciliation']=reconciliation.report(c)
 report['reviews']=[review.report_shadow(c,mid) for mid,a in latest.items() if a=='SHADOW']
 report['training']=research_agent.scheduled_training(c,day)
 report['active_research']=research_agent.run(c,day,run_context=run_context)
 store.put(c,'nightly',store.digest(report),report);return report
