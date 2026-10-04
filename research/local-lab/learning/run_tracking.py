"""Append-only maintenance/agent provenance. Scheduling itself remains external."""
import uuid
import lab
from learning import store

VERSION = 'RESEARCH_RUN_V1'
ORIGINS = {'MANUAL', 'ENGINEERING_ACCEPTANCE', 'SCHEDULED'}
TERMINAL = {'COMPLETED_AGENT', 'COMPLETED_MAINTENANCE'}

def events(c, run_id):
    return [x for x in store.get(c, 'research_run_event') if x['run_id'] == run_id]

def latest(c, run_id):
    rows = events(c, run_id)
    if not rows: raise ValueError('UNKNOWN_RUN')
    return rows[-1]

def append(c, context, phase, **details):
    body = {k: context.get(k) for k in ('run_id', 'origin', 'trigger_id', 'attempt')}
    body.update(version=VERSION, phase=phase, at=lab.now(), **details)
    return store.put(c, 'research_run_event', store.digest(body), body)

def begin(c, run_id=None, origin='MANUAL', trigger_id=None, resume=False):
    if origin not in ORIGINS: raise ValueError('INVALID_TRIGGER_ORIGIN')
    if origin == 'SCHEDULED' and not trigger_id: raise ValueError('SCHEDULED_TRIGGER_ID_REQUIRED')
    rid = run_id or str(uuid.uuid4())
    if not isinstance(rid, str) or not rid.strip() or len(rid)>200: raise ValueError('INVALID_RUN_ID')
    prior = events(c, rid)
    if prior:
        last = prior[-1]
        if (last['origin'], last.get('trigger_id')) != (origin, trigger_id): raise ValueError('RUN_TRIGGER_MISMATCH')
        if last['phase'] in TERMINAL or last['phase'] in ('AWAITING_AGENT','AGENT_STARTED'):
            return dict(last, reused=True)
        if not resume: raise ValueError('EXPLICIT_RESUME_REQUIRED')
        if last['phase'] not in ('FAILED','INTERRUPTED'):
            append(c, last, 'INTERRUPTED', reason='Explicit recovery under exclusive maintenance lock')
        context=dict(run_id=rid,origin=origin,trigger_id=trigger_id,attempt=last['attempt']+1)
        append(c, context, 'RESUMED', previous_event_id=last['id'])
    else:
        context=dict(run_id=rid,origin=origin,trigger_id=trigger_id,attempt=1)
        append(c, context, 'STARTED')
    return dict(context,reused=False)

def maintenance_finished(c, context, report):
    if report['state']=='FAILED':
        append(c, context, 'FAILED', reason=report.get('error','MAINTENANCE_FAILED'))
        return latest(c,context['run_id'])
    worker=report.get('steps',{}).get('learning',{}).get('active_research',{}).get('worker',{})
    if worker.get('resume_run_id'):
        previous=latest(c,worker['resume_run_id'])
        if previous['phase'] not in ('AWAITING_AGENT','AGENT_STARTED'):raise ValueError('INVALID_RESEARCH_RESUME_POINTER')
        append(c,context,'COMPLETED_MAINTENANCE',resume_run_id=previous['run_id'],
               maintenance_state=report['state'],research_completed=False)
        return latest(c,context['run_id'])
    task_ids=[t['id'] for t in worker.get('tasks',[])] if worker.get('due') else []
    append(c,context,'AWAITING_AGENT' if task_ids else 'COMPLETED_MAINTENANCE',
           task_ids=task_ids, evidence_signature=worker.get('signature'),
           maintenance_state=report['state'], worker_reason=worker.get('reason','NO_RESEARCH_HANDOFF'),
           research_completed=False)
    return latest(c,context['run_id'])

def agent_start(c, run_id, task_ids, resume=False):
    last=latest(c,run_id)
    if last['phase']=='COMPLETED_AGENT':
        if set(last['task_ids'])!=set(task_ids): raise ValueError('COMPLETED_TASK_MISMATCH')
        return dict(last,reused=True)
    if last['phase']=='AGENT_STARTED':
        if not resume: raise ValueError('EXPLICIT_AGENT_RESUME_REQUIRED')
        if set(last['task_ids'])!=set(task_ids): raise ValueError('RESUME_TASK_MISMATCH')
        # Keep start watermark so already completed tasks can resume without duplication.
        return dict(last,reused=True,resumed=True)
    if last['phase']!='AWAITING_AGENT': raise ValueError('NO_AGENT_HANDOFF')
    if not task_ids or len(set(task_ids))!=len(task_ids) or len(task_ids)>3 or not set(task_ids)<=set(last['task_ids']):
        raise ValueError('INVALID_AGENT_TASKS')
    for tid in task_ids:
        task=store.raw(c,tid)
        if not any(t['id']==tid for t in store.get(c,'research_task')):raise ValueError('NOT_RESEARCH_TASK')
        for eid in task.get('evidence_ids',[]): store.raw(c,eid)
    append(c,last,'AGENT_STARTED',task_ids=task_ids,handoff_id=last['id'],research_completed=False)
    return latest(c,run_id)

def validate_disposition(c, run_id, task_id):
    start=latest(c,run_id)
    if start['phase']!='AGENT_STARTED' or task_id not in start['task_ids']:raise ValueError('AGENT_TASK_NOT_STARTED')
    return start

def complete_agent(c, run_id, task_ids):
    last=latest(c,run_id)
    if last['phase']=='COMPLETED_AGENT':
        if set(last['task_ids'])!=set(task_ids):raise ValueError('COMPLETED_TASK_MISMATCH')
        return dict(last,reused=True)
    if last['phase']!='AGENT_STARTED' or set(last['task_ids'])!=set(task_ids):raise ValueError('AGENT_SESSION_MISMATCH')
    dispositions=[]
    for tid in task_ids:
        choices=[e for e in store.get(c,'research_event') if e.get('run_id')==run_id and e['task_id']==tid and e['id']>last['id']]
        if not choices:raise ValueError('FRESH_DISPOSITION_REQUIRED')
        e=choices[-1]
        for eid in e['evidence_ids']:store.raw(c,eid)
        dispositions.append(e['id'])
    append(c,last,'COMPLETED_AGENT',task_ids=task_ids,disposition_ids=dispositions,research_completed=True,
           outcome_note='Completed investigation; not proof of model or trading improvement')
    return latest(c,run_id)

def dashboard(c):
    last={}
    for e in store.get(c,'research_run_event'):last[e['run_id']]=e
    rows=sorted(last.values(),key=lambda x:x['id'],reverse=True)
    return dict(version=VERSION,runs=rows[:20],
                open_runs=[r for r in rows if r['phase'] in ('AWAITING_AGENT','AGENT_STARTED','FAILED','INTERRUPTED')],
                scheduled_linked_completed=sum(r['phase']=='COMPLETED_AGENT' and r['origin']=='SCHEDULED' and bool(r['trigger_id']) for r in rows),
                engineering_completed=sum(r['phase']=='COMPLETED_AGENT' and r['origin']=='ENGINEERING_ACCEPTANCE' for r in rows),
                pending=sum(r['phase'] in ('AWAITING_AGENT','AGENT_STARTED','FAILED','INTERRUPTED') for r in rows),
                natural_acceptance='REQUIRES_MATCHING_EXTERNAL_SCHEDULER_RECORD',
                note='Trigger identity is recorded, not independently attested. Maintenance completion is not agent research completion.')
