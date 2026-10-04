"""Durable handoff to scheduled Codex. No shell execution or production promotion.
Hypotheses are authored by the agent; numeric experiments use a bounded PIT DSL.
"""
import argparse, copy, json, math
from datetime import datetime, timedelta
from pathlib import Path
import lab
from learning import store, features, experiments, trainer, quote_learning

VERSION = 'ACTIVE_RESEARCH_V1'
METHOD = 'daily_net_return_v1'
TERMINAL = {'REJECTED', 'COMPLETED'}

def latest_events(c):
    return {x['task_id']: x for x in store.get(c, 'research_event')}

def task(c, topic, evidence, question):
    key = store.digest([VERSION, topic, evidence])
    old = [r for r in store.get(c, 'research_task') if r['key'] == key]
    if old: return old[0]['id']
    return store.put(c, 'research_task', key, dict(topic=topic, evidence_ids=evidence,
        question=question, created_at=lab.now(), state='OPEN', version=VERSION))

def event(c, task_id, state, reason, evidence_ids, run_id=None):
    if state not in {'BLOCKED', 'COMPLETED', 'REJECTED'} or not reason or not evidence_ids:
        raise ValueError('EVIDENCE_AND_EXPLICIT_DISPOSITION_REQUIRED')
    if task_id not in {x['id'] for x in store.get(c, 'research_task')}: raise ValueError('UNKNOWN_TASK')
    for i in evidence_ids: store.raw(c, i)
    if run_id:
        from learning import run_tracking
        run_tracking.validate_disposition(c,run_id,task_id)
    body = dict(task_id=task_id, state=state, reason=reason, evidence_ids=evidence_ids)
    if run_id:body['run_id']=run_id
    return store.put(c, 'research_event', store.digest(body), body)

def scan(c):
    """Nightly evidence triggers; no new task when the relevant evidence is unchanged."""
    policy = store.register_policy(c)
    for basis in ('QUOTE_PROXY', 'ACTUAL'):
        data = store.dataset(c, METHOD, basis)
        labels = sorted({r['label_id'] for r in data['rows']})
        settled = [r for r in data['rows'] if r['status'] == 'SETTLED']
        diagnostic = dict(basis=basis, dataset_sha=data['fingerprint'], eligible_days=len({r['day'] for r in settled}),
            settled_rows=len(settled), excluded=data['excluded'], label_ids=labels,
            note='Descriptive outcome audit, not evidence of forecast error or causality')
        if settled:
            diagnostic['negative_net_fraction'] = sum(r['gross'] - policy['cost'] < 0 for r in settled) / len(settled)
        did = store.put(c, 'research_diagnostic', store.digest(diagnostic), diagnostic)
        topic = 'OUTCOME_DIAGNOSIS_' + basis if settled else 'LABEL_READINESS_' + basis
        task(c, topic, [did], '核对误差与可执行性；区分报价代理和实际成交。没有合格结果时列出阻断条件，不调权重。')
    # Quote diagnostics advance only when a saved, verified dataset changes.
    quote_sets = quote_learning._records(c, 'quote_dataset')
    if quote_sets:
        newest = quote_sets[-1]
        data = quote_learning._verify(c, newest)
        prior = [d for d in store.get(c, 'research_diagnostic') if d.get('basis') == 'QUOTE_RETURN']
        if not prior or prior[-1].get('dataset_sha') != data['fingerprint']:
            observations = data['rows']
            diagnostic = dict(basis='QUOTE_RETURN', dataset_sha=data['fingerprint'],
                dataset_id=newest['id'], eligible_days=len({r['day'] for r in observations}),
                quote_rows=len(observations), summary=data['summary'], excluded=data['excluded'],
                label_ids=sorted({r['label_id'] for r in observations}), scope=quote_learning.SCOPE,
                calibrates_original_probability=False,
                note='Full-date quote outcomes and point-in-time numeric evidence; no execution claim')
            if observations:
                diagnostic['negative_net_fraction'] = sum(r['gross'] - quote_learning.policy()['cost'] < 0 for r in observations) / len(observations)
            did = store.put(c, 'research_diagnostic', store.digest(diagnostic), diagnostic)
            topic = 'OUTCOME_DIAGNOSIS_QUOTE_RETURN' if observations else 'LABEL_READINESS_QUOTE_RETURN'
            task(c, topic, [did], '核对原候选分母、报价收益和截止前数值证据；按新日期检验假设，部分日期只描述，不把报价概率当作可执行盈利概率。')
    for model in store.get(c,'model'):
        if model.get('state') != 'TRAINED_RESEARCH_ONLY': continue
        ds=store.dataset(c,METHOD,model['basis'])
        truth={(r['snapshot_id'],r['code']):r for r in ds['rows'] if r['status']=='SETTLED'}
        errors=[];refs=[]
        for sh in store.get(c,'shadow'):
            if sh['model_id'] != model['id'] or sh['state'] != 'SHADOW_ONLY': continue
            for pred in sh['predictions']:
                row=truth.get((sh['snapshot_id'],pred['code']))
                if row is None: continue
                actual=row['gross']-model['policy']['cost']
                errors.append(dict(day=row['day'],code=row['code'],expected_net=pred['expected_net'],
                    actual_net=actual,residual=actual-pred['expected_net'],
                    brier=(pred['profit_probability']-float(actual>0))**2))
                refs.extend([sh['id'],row['label_id']])
        if errors:
            body=dict(model_id=model['id'],basis=model['basis'],errors=errors,evidence_ids=sorted(set(refs)),
                      note='Matched frozen shadow predictions and reviewed outcomes; correlated within dates')
            rid=store.put(c,'research_error_audit',store.digest(body),body)
            task(c,'PREDICTION_ERRORS',[rid],'按日期检查排序、概率与收益误差，找反复出现的机制；样本不足不宣称统计显著。')
    scenario_ids = [r['id'] for r in store.get(c, 'label') if r['status']=='OUT_OF_SCENARIO' and r['basis']=='QUOTE_PROXY']
    if scenario_ids:
        task(c, 'SCENARIO_COVERAGE', scenario_ids, '用户已取消跳空限制；审核所有可执行竞价结果，跳空仅作分组特征。旧条件概率独立校准，不回写旧预测。')
    enrich = store.get(c, 'enrichment')
    if enrich:
        task(c, 'EVIDENCE_QUALITY', [enrich[-1]['id']], '检查缺失、单位、信息时点和涨停原因反证；识别值得验证的新方法，不将新增数据冒充预测增益。')
    for r in store.get(c, 'shadow'):
        if r.get('state') == 'DRIFT_REVIEW':
            task(c, 'SHADOW_REVIEW', [r['id']], '检查特征漂移与数据故障，必要时暂停该影子模型；不可据此声称收益失效。')
    for r in store.get(c, 'shadow_review'):
        task(c, 'SHADOW_REVIEW', [r['id']], '检查风险、校准与基线增益；准入不等于采用。证据完整性故障可提出暂停影子模型，普通短期亏损不自动回滚。')
    return dashboard(c)

def dashboard(c):
    events = latest_events(c)
    pending = [dict(r, disposition=events.get(r['id'])) for r in store.get(c, 'research_task')
               if events.get(r['id'], {}).get('state') not in TERMINAL]
    return dict(version=VERSION, mode='SCHEDULED_CODEX_HANDOFF', pending=pending,
        history_events=list(events.values()), proposals=store.get(c, 'research_proposal'), runs=store.get(c, 'research_trial')[-10:],
        worker_runs=store.get(c, 'research_worker')[-5:], tracking=__import__('learning.run_tracking',fromlist=['dashboard']).dashboard(c), promotion=False,
        note='Queue generation is not completion of agent research. Natural unattended execution must be verified separately.')

def worker_due(c, day):
    """At most one weekly research session; new shadow-review evidence can interrupt."""
    datetime.strptime(day, '%Y%m%d')
    view = dashboard(c)
    all_tasks = {t['id']: t for t in store.get(c, 'research_task')}
    tasks = view['pending']; signature = store.digest(list(all_tasks))
    unfinished = [r for r in view['tracking'].get('open_runs',view['tracking']['runs'])
                  if r['phase'] in ('AWAITING_AGENT', 'AGENT_STARTED') and r.get('origin') != 'ENGINEERING_ACCEPTANCE']
    # Resume the original provenance/session, including already disposed members.
    # A weekly/new-evidence gate must not strand interrupted agent work.
    for run in sorted(unfinished, key=lambda r: (r['phase'] != 'AGENT_STARTED', r['id'])):
        owned = [all_tasks[i] for i in run.get('task_ids', []) if i in all_tasks]
        if owned:
            return dict(due=True, reason='RESUME_UNFINISHED_AGENT', resume_run_id=run['run_id'],
                        signature=signature, tasks=owned, next_day=day,
                        origin=run['origin'], trigger_id=run.get('trigger_id'))
    runs = [r for r in store.get(c, 'research_worker') if r.get('origin')!='ENGINEERING_ACCEPTANCE']
    last = runs[-1] if runs else None
    if not tasks: return dict(due=False, reason='NO_TASKS')
    if last and {t['id'] for t in tasks} <= set(last['task_ids']): return dict(due=False, reason='NO_NEW_EVIDENCE')
    critical = any(t['topic'] == 'SHADOW_REVIEW' and t['id'] not in (last or {}).get('task_ids', []) for t in tasks)
    elapsed = 7 if not last else (datetime.strptime(day, '%Y%m%d') - datetime.strptime(last['day'], '%Y%m%d')).days
    return dict(due=elapsed >= 7 or critical, reason='NEW_REVIEW' if critical else 'WEEKLY_RESEARCH',
        signature=signature, tasks=tasks, next_day=day if not last else (datetime.strptime(last['day'], '%Y%m%d') + timedelta(days=7)).strftime('%Y%m%d'))

def finish_worker(c, day, task_ids, run_id=None):
    events = latest_events(c)
    if not task_ids or any(i not in events for i in task_ids): raise ValueError('DISPOSITION_REQUIRED_BEFORE_FINISH')
    datetime.strptime(day, '%Y%m%d')
    all_ids = [r['id'] for r in store.get(c, 'research_task')]
    if not set(task_ids) <= set(all_ids): raise ValueError('UNKNOWN_TASK')
    if run_id:
        from learning import run_tracking
        result=run_tracking.complete_agent(c,run_id,task_ids)
        old=[r for r in store.get(c,'research_worker') if r.get('run_id')==run_id]
        if old:return old[-1]['id']
        body=dict(day=day,task_ids=task_ids,signature=store.digest(all_ids),at=lab.now(),run_id=run_id,
                  origin=result['origin'],trigger_id=result.get('trigger_id'),trace_event_id=result['id'])
    else:
        body = dict(day=day, task_ids=task_ids, signature=store.digest(all_ids), at=lab.now(),origin='LEGACY_UNLINKED')
    return store.put(c, 'research_worker', store.digest(body), body)

def allowed_features(basis=None):
    if basis == 'QUOTE_RETURN': return set(features.FEATURES)
    return set(features.FEATURES) | {f'flow_{k}_{n}' for k in ('net_cny', 'positive_ratio') for n in (1,3,5,10,20)} | {'limit_open_count'} | {f'{code}_return_{n}' for code in ('883900','883958') for n in (1,3,5,10,20)}

def register(c, p):
    """Preregister a new hypothesis BEFORE any confirming forward data exist."""
    p = copy.deepcopy(p)
    for field in ('hypothesis', 'mechanism', 'falsification', 'missing_policy', 'primary_metric', 'risk_limits'):
        if not isinstance(p.get(field), str) or not p[field].strip(): raise ValueError('MISSING_' + field)
    if p['primary_metric'] != 'TOP2_NET_UPLIFT_WITH_TAIL_AND_COVERAGE': raise ValueError('FIXED_OBJECTIVE_REQUIRED')
    if p.get('basis') not in ('QUOTE_PROXY','ACTUAL','QUOTE_RETURN'): raise ValueError('INVALID_BASIS')
    task_ids = {r['id'] for r in store.get(c, 'research_task')}
    if p.get('task_id') not in task_ids or not p.get('evidence_ids'): raise ValueError('TASK_AND_EVIDENCE_REQUIRED')
    for i in p['evidence_ids']: store.raw(c, i)
    keys = p.get('features', [])
    if not keys or len(keys) > 35 or len(keys) != len(set(keys)) or not set(keys) <= allowed_features(p['basis']): raise ValueError('INVALID_FEATURE_SET')
    recipes = p.get('recipes', [])
    if len(recipes) > 3: raise ValueError('RECIPE_BUDGET')
    names = set(keys)
    for r in recipes:
        if r.get('op') not in ('product','difference') or r.get('left') not in keys or r.get('right') not in keys: raise ValueError('INVALID_RECIPE')
        if not isinstance(r.get('name'),str) or not r['name'].startswith('derived_') or r['name'] in names: raise ValueError('INVALID_RECIPE_NAME')
        names.add(r['name'])
    definition = store.digest(p)
    old = [r for r in store.get(c, 'research_proposal') if r['definition_sha'] == definition]
    if old: return old[0]['id']
    now = lab.now(); week = datetime.fromisoformat(now).isocalendar()[:2]
    count = sum(datetime.fromisoformat(r['registered_at']).isocalendar()[:2] == week for r in store.get(c,'research_proposal'))
    if count >= 2: raise ValueError('GLOBAL_WEEKLY_TWO_PROPOSAL_BUDGET')
    p.update(definition_sha=definition, registered_at=now, version=VERSION,
        confirmation='ONLY_SNAPSHOTS_CAPTURED_AFTER_REGISTRATION', production_promotion=False,
        evaluation_policy=quote_learning.policy() if p['basis']=='QUOTE_RETURN' else store.register_policy(c))
    return store.put(c, 'research_proposal', definition, p)

def evaluate(c, proposal_id):
    p = store.raw(c, proposal_id)
    if proposal_id not in {x['id'] for x in store.get(c,'research_proposal')}: raise ValueError('NOT_PROPOSAL')
    quote = p['basis'] == 'QUOTE_RETURN'
    if quote:
        data = copy.deepcopy(quote_learning.dataset(c))
        snapshots = {s['id']: s for s in quote_learning._records(c, 'snapshot')}
        by_day = {}
        for row in data['rows']: by_day.setdefault(row['day'], []).append(row)
        confirmed = []
        registered = lab.stamp(p['registered_at'])
        for day, cohort in sorted(by_day.items()):
            # Confirmation is by whole date: one pre-registration member excludes all.
            fresh = True
            for row in cohort:
                s = snapshots.get(row['snapshot_id'])
                if (s is None or lab.stamp(s['captured_at']) <= registered or
                    lab.stamp(s['_recorded_at']) <= registered or lab.stamp(row['cutoff']) <= registered):
                    fresh = False; break
                quote_learning._verify(c, s)
            if fresh: confirmed.extend(cohort)
            else: data['excluded'].append(dict(day=day, reason='PRE_REGISTRATION_COHORT_NOT_CONFIRMATION', expected_count=len(cohort)))
        data['rows'] = confirmed
        data['fingerprint'] = store.digest(dict(rows=confirmed, excluded=data['excluded'], basis=data['basis']))
    else:
        data = experiments.attach(c, store.dataset(c, METHOD, p['basis']))
        # Old outcomes motivate research, never confirm a newly selected hypothesis.
        data['rows'] = [r for r in data['rows'] if lab.stamp(store.raw(c,r['snapshot_id'])['captured_at']) > lab.stamp(p['registered_at'])]
        data['fingerprint'] = store.digest(data['rows'])
    source_sha = implementation_sha()
    key = store.digest([proposal_id, data['fingerprint'], source_sha])
    old = [x for x in store.get(c,'research_trial') if x['key'] == key]
    if old: return old[0]
    previous = [x for x in store.get(c,'research_trial') if x['proposal_id']==proposal_id and x['state']=='EVALUATED_RESEARCH_ONLY']
    days = sorted({r['day'] for r in data['rows']})
    if previous:
        last=previous[-1]
        if len(set(days)-set(last['days'])) < 5 or lab.stamp(lab.now()) - lab.stamp(last['at']) < timedelta(days=7):
            return dict(state='WAITING_WEEK_AND_FIVE_NEW_DAYS', promotion=False)
    policy = copy.deepcopy(p['evaluation_policy'])
    policy['feature_names'] = p['features'] + [r['name'] for r in p.get('recipes',[])]
    for row in data['rows']: apply_recipes(row['x'], p)
    result=trainer.fit_dataset(data,policy)
    body=dict(proposal_id=proposal_id,at=lab.now(),days=days,source_sha=source_sha,dataset_sha=data['fingerprint'],
        state='EVALUATED_RESEARCH_ONLY' if result['state']=='TRAINED_RESEARCH_ONLY' else result['state'],
        result=result,promotion=False,decision='SEPARATE_FRESH_SHADOW_REVIEW_REQUIRED; repeated looks are exploratory')
    if result['state']=='TRAINED_RESEARCH_ONLY':
        model=dict(result,method=quote_learning.METHOD if quote else METHOD,created_at=lab.now(),research_proposal_id=proposal_id,
                   source_ids=sorted({r['snapshot_id'] for r in data['rows']} | {r['label_id'] for r in data['rows']}),code_sha=source_sha)
        if quote:
            model.update(scope=quote_learning.SCOPE, model_kind='quote_model',
                         calibrates_original_probability=False, production_promotion=False,
                         promotion='DISABLED_QUOTE_RESEARCH_ONLY')
        body['model_id']=store.put(c,'quote_model' if quote else 'model','proposal:'+key,model)
    rid=store.put(c,'research_trial',key,body)
    return dict(id=rid,**body)

def implementation_sha():
    return store.digest([Path(x.__file__).read_text() for x in (store,trainer,experiments,features,quote_learning)] + [Path(__file__).read_text()])

def apply_recipes(x, proposal):
    for recipe in proposal.get('recipes', []):
        a, b = x.get(recipe['left']), x.get(recipe['right'])
        value = None if a is None or b is None else (a*b if recipe['op']=='product' else a-b)
        if value is not None and not math.isfinite(value): raise ValueError('NONFINITE_DERIVED_FEATURE')
        x[recipe['name']] = value
    return x

def shadow_rows(c, model, snapshot):
    if model['code_sha'] != implementation_sha(): raise ValueError('CANDIDATE_IMPLEMENTATION_CHANGED_REVIEW_REQUIRED')
    p=store.raw(c,model['research_proposal_id'])
    if p['basis'] == 'QUOTE_RETURN':
        if model.get('basis') != 'QUOTE_RETURN' or model.get('method') != quote_learning.METHOD:
            raise ValueError('QUOTE_PROPOSAL_MODEL_MISMATCH')
        if lab.stamp(snapshot['captured_at']) <= lab.stamp(p['registered_at']):
            raise ValueError('QUOTE_PROPOSAL_SNAPSHOT_NOT_FRESH')
        if not set(p['features']) <= set(features.FEATURES):
            raise ValueError('QUOTE_FEATURES_REQUIRE_ORIGINAL_CUTOFF_EVIDENCE')
        return [dict(day=snapshot['day'], code=r['code'],
                     x=apply_recipes({k:r['features'].get(k) for k in features.FEATURES}, p))
                for r in snapshot['rows']]
    es=[e for e in store.get(c,'enrichment') if e['snapshot_id']==snapshot['id'] and e['provenance']=='PROSPECTIVE_LOCAL'
        and lab.stamp(e['cutoff'])<=lab.stamp(e['_recorded_at'])<lab.stamp(snapshot['entry_at'])]
    if not es: raise ValueError('NO_PRE_ENTRY_ENRICHMENT')
    e=min(es,key=lambda x:x['id']);store.raw(c,e['id'])
    for rid in e['raw_ids']:
        if lab.stamp(store.raw(c,rid)['captured_at'])>lab.stamp(e['cutoff']): raise ValueError('FUTURE_ENRICHMENT')
    mapped={r['code']:r for r in e['rows']}
    if set(mapped)!={r['code'] for r in snapshot['rows']}: raise ValueError('COHORT_MISMATCH')
    rows=[]
    for r in snapshot['rows']:
        x=dict(r['features']);x.update(mapped[r['code']]['features']);x.update(e['shared']['indices'])
        for recipe in p.get('recipes',[]):
            a,b=x.get(recipe['left']),x.get(recipe['right'])
            x[recipe['name']]=None if a is None or b is None else (a*b if recipe['op']=='product' else a-b)
        rows.append(dict(day=snapshot['day'],code=r['code'],x=x))
    return rows


def scheduled_training(c, day):
    outputs=[]
    for basis in ('QUOTE_PROXY','ACTUAL'):
        data=store.dataset(c,METHOD,basis);days=sorted({r['day'] for r in data['rows']})
        prior=[r for r in store.get(c,'research_training_cycle') if r['basis']==basis]
        last=prior[-1] if prior else None
        if last and (last['fingerprint']==data['fingerprint'] or
            (datetime.strptime(day,'%Y%m%d')-datetime.strptime(last['day'],'%Y%m%d')).days<7 or
            len(set(days)-set(last['days']))<5):
            outputs.append(dict(basis=basis,state='WAITING_NEW_DATA_OR_WEEKLY_BUDGET'));continue
        model=trainer.train(c,METHOD,basis);comparison=experiments.run(c,basis=basis)
        body=dict(day=day,basis=basis,days=days,fingerprint=data['fingerprint'],model_id=model['id'],experiment_id=comparison['id'])
        store.put(c,'research_training_cycle',store.digest(body),body)
        outputs.append(dict(body,state=model['state']))
    return outputs


def run(c, day, run_context=None):
    scan(c)
    trials=[]
    for p in store.get(c,'research_proposal'):
        try: trials.append(evaluate(c,p['id']))
        except (ValueError,KeyError,OSError) as exc:
            body=dict(proposal_id=p['id'],state='EVIDENCE_OR_CODE_FAILURE',error=str(exc),promotion=False)
            rid=store.put(c,'research_failure',store.digest(body),body)
            task(c,'EXPERIMENT_FAILURE',[rid],'修复证据或代码故障；不降低门槛，不覆盖原实验。')
            trials.append(body)
    return dict(queue=dashboard(c),worker=worker_due(c,day),trials=trials,run_context=run_context)

if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--db',type=Path,default=lab.DB)
    sub=parser.add_subparsers(dest='action',required=True)
    a=sub.add_parser('scan');a.add_argument('--day',required=True)
    a=sub.add_parser('register');a.add_argument('file',type=Path)
    a=sub.add_parser('evaluate');a.add_argument('--proposal',type=int,required=True)
    a=sub.add_parser('event');a.add_argument('file',type=Path)
    a=sub.add_parser('start');a.add_argument('--run-id',required=True);a.add_argument('--tasks',type=int,nargs='+',required=True);a.add_argument('--resume',action='store_true')
    a=sub.add_parser('finish');a.add_argument('--day',required=True);a.add_argument('--tasks',type=int,nargs='+',required=True);a.add_argument('--run-id',required=True)
    args=parser.parse_args()
    import operations
    with lab.connect(args.db) as c, operations.lock(args.db.parent/'nightly.lock'):
        if args.action=='scan': output=run(c,args.day)
        elif args.action=='register': output=register(c,json.loads(args.file.read_text()))
        elif args.action=='evaluate': output=evaluate(c,args.proposal)
        elif args.action=='event': output=event(c,**json.loads(args.file.read_text()))
        elif args.action=='start':
            from learning import run_tracking
            output=run_tracking.agent_start(c,args.run_id,args.tasks,args.resume)
        else: output=finish_worker(c,args.day,args.tasks,args.run_id)
    print(json.dumps(output,ensure_ascii=False))
