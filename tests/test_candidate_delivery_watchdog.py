"""No network, credentials, dispatches or market data collection in tests."""
import json
from copy import deepcopy
from datetime import datetime
import pytest
from urllib.parse import parse_qs, urlsplit
from scripts import candidate_delivery_watchdog as m

NOW = datetime.fromisoformat('2026-09-22T22:00:00+08:00')
def raw(value): return json.dumps(value).encode()
def run(workflow, id=7, **changes):
    value=dict(id=id, workflow_id=m.IDS.get(workflow, 8), path='.github/workflows/'+workflow,
        run_attempt=1, head_branch='main', repository={'full_name':m.REPO},
        head_repository={'full_name':m.REPO}, event='workflow_dispatch', status='completed',
        head_sha='b'*40, conclusion='success', created_at='2026-09-22T12:00:00Z',
        run_started_at='2026-09-22T12:00:01Z', updated_at='2026-09-22T12:10:00Z')
    value.update(changes)
    return value

def p0_jobs(r, *, reuse=False):
    times = [('12:00:01','12:05:00'),('12:05:01','12:06:00'),('12:06:01','12:09:00')]
    if reuse: times = [('13:00:01','13:05:00'),('13:05:01','13:06:00'),('13:06:01','13:09:00')]
    conclusions = ('success','skipped','success') if reuse else ('success','success',r['conclusion'])
    jobs=[dict(id=r['id']*100+i,run_id=r['id'],name=name,status='completed',conclusion=conclusion,
        started_at='2026-09-22T'+span[0]+'Z',completed_at='2026-09-22T'+span[1]+'Z')
        for i,(name,conclusion,span) in enumerate(zip(m.P0_JOBS,conclusions,times),1)]
    jobs[0]['steps']=[dict(name=name,number=i,status='completed',
        conclusion='skipped' if reuse and i>1 else 'success') for i,name in enumerate(
        ('Resolve exact D and reject a completed duplicate',*m.P0_GENERATION_STEPS),1)]
    return {'total_count':len(jobs),'jobs':jobs}

class Fake:
    def __init__(self):
        self.head='a'*40; self.posts=[]; self.busy=[]; self.live=True
        self.p0_runs=[run(m.P0)]; self.p0_jobs={7:p0_jobs(self.p0_runs[0])}; self.revisions={}
        cal=b'exchange,cal_date,is_open\nSSE,20260921,1\nSSE,20260922,1\nSSE,20260923,1\nSSE,20260924,1\nSSE,20260925,1\n'
        p0=raw({'rows':[{'promotion_rank':i} for i in (1,2,3)]})
        self.files={'models/decision_candidate_profit_activation_v1.json':raw({'enabled':True}),
          'data/market/trade_cal_sse.csv':cal,
          'outputs/decision/three_rank_top10_20260922.json':p0,
          'outputs/decision/primary_d_receipt_20260922.json':raw(dict(signal_date='20260922',exec_date='20260923',exit_date='20260924',primary_status='READY',generation_mode='NATURAL',inputs={'git_head':'b'*40,'calendar':{'sha256':m.digest(cal)}},outputs={'json_sha256':m.digest(p0)}))}
    def file(self,path,head):
        if head==self.head:return self.files.get(path)
        return self.revisions.get(head,{}).get(path)
    def public_ready(self,*args): return self.live
    def public_core_status(self,*args): return None
    def candidate_code_approval(self,head):
        return {'status':'APPROVED_IDENTICAL_STATIC_SURFACE','repository':m.REPO,
                'current_head_sha':head,'daily_source_evidence_admitted':False,
                'production_freeze_issued':False}
    def request(self,path,body=None):
        if body is not None: self.posts.append((path,deepcopy(body))); return None
        if path=='/git/ref/heads/main': return {'object':{'sha':self.head}}
        if path=='/actions/concurrency_groups?per_page=100': return {'total_count':0,'concurrency_groups':[]}
        if path=='/actions/concurrency_groups/'+m.GROUP: return None
        if path.startswith('/actions/runs?branch=main&status='):
            query=parse_qs(urlsplit(path).query)
            rows=[r for r in self.busy if r.get('status')==query['status'][0]]
            page=int(query['page'][0])
            return {'total_count':len(rows),'workflow_runs':deepcopy(rows[(page-1)*100:page*100])}
        if path.startswith('/actions/workflows/') and 'created=' in path:
            query=parse_qs(urlsplit(path).query)
            workflow=path.split('/')[3]
            if workflow==m.P0 and query.get('status')==['completed']:
                return {'total_count':len(self.p0_runs),'workflow_runs':deepcopy(self.p0_runs)}
            start=datetime.fromisoformat(query['created'][0][2:].replace('Z','+00:00'))
            rows=[r for r in self.busy if r.get('path')=='.github/workflows/'+workflow and
                  datetime.fromisoformat(r['created_at'].replace('Z','+00:00'))>=start]
            page=int(query['page'][0])
            return {'total_count':len(rows),'workflow_runs':deepcopy(rows[(page-1)*100:page*100])}
        if path.startswith('/actions/workflows/'+m.P0+'/runs'): return {'workflow_runs':deepcopy(self.p0_runs)}
        if '/attempts/1/jobs?per_page=100' in path:return deepcopy(self.p0_jobs[int(path.split('/')[3])])
        if path.endswith('/jobs?per_page=100'): return {'jobs':[{'steps':[{'name':'Upload immutable P0 candidate','conclusion':'success'}]}]}
        if path=='/actions/runs/8': return run(m.NATURAL,8)
        if path=='/actions/runs/9': return run(m.OBSERVER,9)
        if path.endswith('/runs?per_page=50'): return {'workflow_runs':self.busy}
        raise AssertionError(path)
    def freeze(self):
        snap=raw(dict(signal_date='20260922',exec_date='20260923',exit_date='20260924'))
        self.files[m.ROOT+'candidate_natural_forward/day_20260922.json']=snap
        for kind in ('p0_sources','local_freeze'):
            self.files[m.ROOT+f'candidate_natural_forward/{kind}_20260922.json']=raw({'fixture':True})
        self.files[m.ROOT+'candidate_natural_forward/workflow_20260922.json']=raw(dict(signal_date='20260922',run_id=8,snapshot_file_sha256=m.digest(snap)))
    def observe(self):
        self.freeze()
        self.files[m.ROOT+'candidate_natural_evidence/20260922/context.json']=raw(dict(signal_date='20260922',freeze_run_id=8,observer_run_id=9,snapshot_file_sha256=m.digest(self.files[m.ROOT+'candidate_natural_forward/day_20260922.json'])))
    def formal(self):
        self.observe()
        day=raw(dict(signal_date='20260922',exec_date='20260923',exit_date='20260924',p0_file_sha256=m.digest(self.files['outputs/decision/three_rank_top10_20260922.json']),rows=[{'candidate_rank':1,'ts_code':'A'},{'candidate_rank':2,'ts_code':'B'}]))
        self.files[m.PUBLIC+'day_20260922.json']=day
        self.files[m.PUBLIC+'index.json']=raw({'days':[{'signal_date':'20260922','sha256':m.digest(day)}]})
        groups = {}
        for group, code in zip(('candidate_top1', 'candidate_top2'), ('A', 'B')):
            groups[group] = {'daily_sequence': [dict(signal_date='20260922', ts_code=code,
                formal_projection_sha256=m.canonical(json.loads(day)))]}
        self.files[m.PUBLIC+'summary.json'] = raw({'groups': groups})

@pytest.mark.parametrize('setup,target', [(None,m.NATURAL),('freeze',m.OBSERVER),('observe',m.PUBLISHER)])
def test_missing_stage_dispatches_exact_existing_workflow(setup,target):
    f=Fake()
    if setup: getattr(f,setup)()
    before=deepcopy(f.files); p=m.inspect(f,NOW)
    assert p['workflow']==target
    assert m.act(f,p,NOW,execute=False)['status']=='DRY_RUN_WOULD_DISPATCH' and not f.posts
    assert m.act(f,p,NOW,execute=True)['status']=='DISPATCHED_NOT_COMPLETED'
    assert len(f.posts)==1 and f.posts[0][0]=='/actions/workflows/'+target+'/dispatches'
    assert f.files==before and f.posts[0][1]['ref']=='main'


def test_pending_code_ci_waits_without_dispatch_or_retry_budget_reset():
    f = Fake()
    f.busy = [{**run(m.NATURAL, id=70+i), 'conclusion': 'failure'} for i in range(2)]
    approve = f.candidate_code_approval
    f.candidate_code_approval = lambda head: {**approve(head), 'status': 'WAITING_FOR_CODE_CI_APPROVAL'}
    result = m.act(f, m.inspect(f, NOW), NOW, execute=True)
    assert result['status'] == 'WAITING_FOR_CODE_CI_APPROVAL'
    assert result['prior_failed_attempts'] == 2 and not f.posts and len(f.busy) == 2
    # Approval later permits one attempt; the two earlier failures remain counted.
    f.candidate_code_approval = approve
    result = m.act(f, m.inspect(f, NOW), NOW, execute=True)
    assert result['status'] == 'DISPATCHED_NOT_COMPLETED'
    assert result['prior_failed_attempts'] == 2 and len(f.posts) == 1


def test_exhausted_retry_budget_is_not_forgiven_even_if_ci_is_pending():
    f = Fake()
    f.busy = [{**run(m.NATURAL, id=70+i), 'conclusion': 'failure'} for i in range(3)]
    f.candidate_code_approval = lambda head: pytest.fail('Budget must remain authoritative')
    with pytest.raises(ValueError, match='RETRY_BUDGET_EXHAUSTED'):
        m.act(f, m.inspect(f, NOW), NOW, execute=True)
    assert not f.posts


def test_approval_api_failure_never_dispatches_or_becomes_waiting():
    f = Fake()
    def unavailable(head):
        raise ValueError('GITHUB_READ_UNAVAILABLE')
    f.candidate_code_approval = unavailable
    with pytest.raises(ValueError, match='GITHUB_READ_UNAVAILABLE'):
        m.act(f, m.inspect(f, NOW), NOW, execute=True)
    assert not f.posts


@pytest.mark.parametrize('field,value', [('current_head_sha', 'f'*40), ('repository', 'other/repo'),
    ('daily_source_evidence_admitted', True), ('production_freeze_issued', True), ('status', 'SUCCESS')])
def test_dispatch_rejects_unbound_or_unapproved_code_receipt(field, value):
    f = Fake()
    approve = f.candidate_code_approval
    f.candidate_code_approval = lambda head: {**approve(head), field: value}
    with pytest.raises(ValueError, match='DISPATCH_CODE_APPROVAL'):
        m.act(f, m.inspect(f, NOW), NOW, execute=True)
    assert not f.posts

def test_complete_is_noop_and_stale_deploy_is_only_publisher():
    f=Fake(); f.formal()
    assert m.act(f,m.inspect(f,NOW),NOW,execute=True)['status']=='FORMAL_TOP2_LEDGER_AND_PAGE_PRESENT'
    assert not f.posts
    f.live=False
    assert m.inspect(f,NOW)['status']=='NEEDS_DEPLOYMENT'

def test_no_duplicate_while_stage_running():
    f=Fake(); f.busy=[{**run(m.NATURAL),'status':'in_progress','conclusion':None}]
    assert m.act(f,m.inspect(f,NOW),NOW,execute=True)['status']=='UPSTREAM_OR_RECOVERY_ALREADY_RUNNING'
    assert not f.posts

def test_three_failures_require_repair_not_infinite_dispatch():
    f=Fake(); f.busy=[{**run(m.NATURAL,id=70+i), 'conclusion':'failure'} for i in range(3)]
    with pytest.raises(ValueError,match='RETRY_BUDGET'): m.act(f,m.inspect(f,NOW),NOW,execute=True)
    assert not f.posts

def test_deadline_never_backfills():
    with pytest.raises(ValueError,match='DEADLINE'): m.inspect(Fake(),datetime.fromisoformat('2026-09-23T09:20:00+08:00'))

@pytest.mark.parametrize('field,value',[('run_attempt',2),('workflow_id',1),('head_branch','fork'),('conclusion','failure'),('event','push'),('repository',{'full_name':'other/repo'}),('head_repository',{'full_name':'other/repo'})])
def test_untrusted_upstream_is_rejected(field,value):
    r=run(m.NATURAL); r[field]=value
    with pytest.raises(ValueError): m.valid_run(r,m.NATURAL)

def test_missing_p0_does_not_substitute_old_day():
    f=Fake(); del f.files['outputs/decision/primary_d_receipt_20260922.json']
    with pytest.raises(ValueError,match='INCOMPLETE_P0'): m.inspect(f,NOW)

def test_corrupt_original_file_is_rejected():
    f=Fake(); f.files['outputs/decision/three_rank_top10_20260922.json']=b'{}'
    with pytest.raises(ValueError,match='P0_BYTES'): m.inspect(f,NOW)

def test_formal_summary_must_match_slots():
    f=Fake(); f.formal(); s=json.loads(f.files[m.PUBLIC+'summary.json']);s['groups']['candidate_top1']['daily_sequence'][0]['ts_code']='other'
    f.files[m.PUBLIC+'summary.json']=raw(s)
    with pytest.raises(ValueError,match='FORMAL_LEDGER'): m.inspect(f,NOW)

def test_file_digest_cannot_substitute_for_ledger_canonical_digest():
    f=Fake(); f.formal(); day=f.files[m.PUBLIC+'day_20260922.json']
    assert m.digest(day)!=m.canonical(json.loads(day))
    assert m.inspect(f,NOW)['status']=='FORMAL_TOP2_LEDGER_AND_PAGE_PRESENT'
    s=json.loads(f.files[m.PUBLIC+'summary.json'])
    s['groups']['candidate_top1']['daily_sequence'][0]['formal_projection_sha256']=m.digest(day)
    f.files[m.PUBLIC+'summary.json']=raw(s)
    with pytest.raises(ValueError,match='FORMAL_LEDGER'): m.inspect(f,NOW)

def test_head_change_stops_dispatch():
    f=Fake(); plan=m.inspect(f,NOW);f.head='b'*40
    with pytest.raises(ValueError,match='MAIN_MOVED'):m.act(f,plan,NOW,execute=True)
    assert not f.posts

def test_friday_to_monday_calendar():
    cal=b'exchange,cal_date,is_open\nSSE,20260925,1\nSSE,20260926,0\nSSE,20260927,0\nSSE,20260928,1\nSSE,20260929,1\n'
    d,t,e,c=m.window(cal,datetime.fromisoformat('2026-09-26T02:00:00+08:00'))
    assert (d,t,e)==('20260925','20260928','20260929') and c.hour==9 and c.minute==20

def test_partial_freeze_is_not_overwritten():
    f=Fake();f.freeze();del f.files[m.ROOT+'candidate_natural_forward/day_20260922.json']
    with pytest.raises(ValueError,match='INCOMPLETE_FREEZE'):m.inspect(f,NOW)

def test_disabled_activation_never_reenabled_by_watchdog():
    f=Fake();f.files['models/decision_candidate_profit_activation_v1.json']=raw({'enabled':False})
    assert m.act(f,m.inspect(f,NOW),NOW,execute=True)['status']=='ACTIVATION_DISABLED_NO_RECOVERY'
    assert not f.posts

@pytest.mark.parametrize('setup,run_id,status',[('freeze',8,'FREEZE_STILL_RUNNING'),('observe',9,'OBSERVER_STILL_RUNNING')])
def test_partial_success_still_running_is_wait_not_failure(setup,run_id,status):
    f=Fake();getattr(f,setup)();original=f.request
    def request(path,body=None):
        r=original(path,body)
        if path==f'/actions/runs/{run_id}':r.update(status='in_progress',conclusion=None)
        return r
    f.request=request
    assert m.act(f,m.inspect(f,NOW),NOW,execute=True)['status']==status
    assert not f.posts

def test_workflow_is_valid_and_has_no_repository_writer():
    from pathlib import Path
    import yaml
    doc=yaml.safe_load((Path(__file__).resolve().parents[1]/m.WORKFLOW).read_text())
    assert doc['jobs']['recover']['permissions']=={'contents':'read','actions':'write'}
    assert all(job.get('permissions',{}).get('contents')!='write' for job in doc['jobs'].values())
    assert 'schedule' in doc[True] and 'push' in doc[True]
    trigger = doc[True]['workflow_run']
    assert trigger == {'workflows': [m.P0_NAME], 'branches': ['main'], 'types': ['completed']}
    assert doc['concurrency'] == {'group': 'dc20-delivery-watchdog', 'cancel-in-progress': False}
    verify = doc['jobs']['verify']['if']
    for gate in ('workflow_id == 343703608', 'run_attempt == 1', "head_branch == 'main'",
                 "status == 'completed'", "conclusion == 'failure'", "conclusion == 'success'",
                 'head_repository.full_name == github.repository',
                 'repository.full_name == github.repository',
                 "event == 'workflow_dispatch'", "path == '.github/workflows/run_primary_d_daily.yml'"):
        assert gate in verify
    assert "github.event_name == 'workflow_run'" in doc['jobs']['recover']['if']
    assert "workflow_run.event == 'schedule'" not in verify
    assert "github.event_name == 'workflow_run'" in doc['jobs']['recover']['steps'][-1]['env']['EXECUTE']

def test_confirmed_old_zero_job_orphan_does_not_block_new_day_dispatch():
    f=Fake(); old={**run('verify_decision_observations.yml',id=7001),
        'status':'queued','conclusion':None,'created_at':'2026-08-01T12:00:00Z'}
    f.busy=[old]; original=f.request
    def request(path,body=None):
        if path=='/actions/runs/7001':return deepcopy(old)
        if path=='/actions/runs/7001/jobs?per_page=100':return {'total_count':0,'jobs':[]}
        if path=='/actions/runs/7001/concurrency_groups?per_page=100':return {'total_count':0,'concurrency_groups':[]}
        return original(path,body)
    f.request=request
    result=m.act(f,m.inspect(f,NOW),NOW,execute=True)
    assert result['status']=='DISPATCHED_NOT_COMPLETED'
    assert result['queue']['orphan_run_ids']==[7001]
    assert len(f.posts)==1 and f.posts[0][0].endswith('/dispatches')

def test_writer_appearing_immediately_before_dispatch_blocks_without_post():
    f=Fake(); original=f.request; checks=[]
    def request(path,body=None):
        if path=='/actions/concurrency_groups?per_page=100':
            checks.append(1)
            return {'total_count':0,'concurrency_groups':[]}
        if path=='/actions/concurrency_groups/'+m.GROUP and len(checks)>1:
            return {'group_name':m.GROUP,'total_count':1,
                    'group_members':[{'run_id':456,'status':'in_progress'}]}
        return original(path,body)
    f.request=request
    result=m.act(f,m.inspect(f,NOW),NOW,execute=True)
    assert result['status']=='UPSTREAM_OR_RECOVERY_ALREADY_RUNNING'
    assert result['queue']['blocking_run_ids']==[456] and not f.posts

def test_queue_read_client_does_not_gain_cancel_or_write_permissions():
    client=m.GitHub('fake-never-sent')
    with pytest.raises(ValueError,match='DISPATCH_TARGET_REJECTED'):
        client.request('/actions/runs/7001/cancel',{})

def test_main_change_during_final_queue_check_stops_dispatch():
    f=Fake(); original=f.request; checks=[]
    def request(path,body=None):
        if path=='/actions/concurrency_groups?per_page=100':
            checks.append(1)
            if len(checks)==4:f.head='b'*40
        return original(path,body)
    f.request=request
    plan=m.inspect(f,NOW)
    with pytest.raises(ValueError,match='MAIN_MOVED'):
        m.act(f,plan,NOW,execute=True)
    assert not f.posts

@pytest.mark.parametrize('path,code,allowed',[
    ('/actions/concurrency_groups/'+m.GROUP,404,True),
    ('/actions/concurrency_groups/'+m.GROUP,403,False),
    ('/actions/concurrency_groups?per_page=100',404,False),
    ('/actions/runs/7001/concurrency_groups?per_page=100',404,False),
])
def test_only_specific_inactive_group_404_is_not_an_api_failure(monkeypatch,path,code,allowed):
    import urllib.error
    def unavailable(*args,**kwargs):
        raise urllib.error.HTTPError('https://api.github.com',code,'fixture',{},None)
    monkeypatch.setattr(m.urllib.request,'urlopen',unavailable)
    client=m.GitHub('fake-never-sent')
    if allowed:assert client.request(path) is None
    else:
        with pytest.raises(ValueError,match='GITHUB_HTTP_'+str(code)):client.request(path)


class Continuation(Fake):
    """Real failure shape: original compute/CAS succeed; later no-op deploys."""
    def __init__(self, *, reuse=True):
        super().__init__()
        title='DC20 controlled daily NATURAL | D=20260922'
        original=run(m.P0,7,conclusion='failure',name=title,display_title=title)
        self.p0_runs=[original]
        self.p0_jobs={7:p0_jobs(original)}
        self.cas='c'*40
        self.commits=[{'sha':self.cas,'parents':[{'sha':'b'*40}]}]
        self.commit={'sha':self.cas,'parents':[{'sha':'b'*40}],
                     'committer':{'date':'2026-09-22T12:05:30Z'}}
        self.files['outputs/decision/three_rank_top10_20260922.csv']=b'rank,ts_code\n1,A\n2,B\n3,C\n'
        self.files['outputs/decision/primary_d_runtime_features_20260922.csv']=b'rank,feature\n1,1\n2,2\n3,3\n'
        path='outputs/decision/primary_d_receipt_20260922.json'
        receipt=json.loads(self.files[path])
        receipt['inputs'].update(candidate={'source_repository':m.UPSTREAMS[0],'resolved_commit':'d'*40},
                                 market={'source_repository':m.UPSTREAMS[1],'resolved_commit':'e'*40})
        receipt['outputs'].update(csv_sha256=m.digest(self.files['outputs/decision/three_rank_top10_20260922.csv']),
            runtime_features_sha256=m.digest(self.files['outputs/decision/primary_d_runtime_features_20260922.csv']))
        self.files[path]=raw(receipt)
        self.revisions[self.cas]=deepcopy(self.files)
        self.artifacts={'total_count':1,'artifacts':[dict(id=900,name='primary-d-candidate-7-1',
            expired=False,size_in_bytes=100,digest='sha256:'+'f'*64,created_at='2026-09-22T12:04:00Z',
            updated_at='2026-09-22T12:04:30Z',workflow_run={'id':7,'head_branch':'main','head_sha':'b'*40})]}
        if reuse:self.add_reuse()

    def add_reuse(self, **changes):
        r=run(m.P0,70,head_sha=self.head,event='schedule',created_at='2026-09-22T13:00:00Z',
              run_started_at='2026-09-22T13:00:01Z',updated_at='2026-09-22T13:10:00Z')
        r.update(changes)
        self.p0_runs.insert(0,r)
        self.p0_jobs[r['id']]=p0_jobs(r,reuse=True)
        return r

    def request(self,path,body=None):
        if body is None:
            if path.startswith('/commits?'):return deepcopy(self.commits)
            if path=='/git/commits/'+self.cas:return deepcopy(self.commit)
            if path=='/actions/runs/7/artifacts?per_page=100':return deepcopy(self.artifacts)
            if path=='/actions/workflows/'+m.P0+'/runs?per_page=50':return {'workflow_runs':deepcopy(self.busy)}
        return super().request(path,body)


def test_failed_generation_plus_successful_noop_dispatches_existing_continuation():
    f=Continuation(); before=deepcopy(f.files)
    plan=m.inspect(f,NOW)
    assert plan['status']=='NEEDS_FREEZE_CONTINUATION'
    assert plan['handoff_authority']=='EXISTING_PINNED_CONTINUATION_IMPORTER'
    assert plan['inputs']=={'p0_run_id':'70','original_generation_run_id':'7',
                           'original_cas_commit':f.cas,'dry_run':False}
    assert m.act(f,plan,NOW,execute=True)['status']=='DISPATCHED_NOT_COMPLETED'
    assert f.posts==[('/actions/workflows/'+m.NATURAL+'/dispatches',{'ref':'main','inputs':plan['inputs']})]
    assert f.files==before


def test_no_successful_reuse_dispatches_only_existing_p0_with_original_source_commits():
    f=Continuation(reuse=False); before=deepcopy(f.files)
    plan=m.inspect(f,NOW)
    assert plan['status']=='NEEDS_P0_REDEPLOY' and plan['workflow']==m.P0
    assert plan['recovery_kind']=='EXISTING_P0_REUSE_ONLY'
    assert plan['inputs']['pred_commit']=='d'*40 and plan['inputs']['market_commit']=='e'*40
    assert plan['inputs']['generation_mode']=='NATURAL'
    assert plan['inputs']['confirm_daily_generation'] and not plan['inputs']['confirm_recovery']
    assert m.act(f,plan,NOW,execute=True)['status']=='DISPATCHED_NOT_COMPLETED'
    assert len(f.posts)==1 and f.files==before
    # Next tick after successful no-op resumes the existing freeze, not P0 again.
    f.add_reuse()
    assert m.inspect(f,NOW)['status']=='NEEDS_FREEZE_CONTINUATION'


@pytest.mark.parametrize('now',['2026-09-22T23:30:00+08:00','2026-09-23T02:00:00+08:00'])
def test_missing_reuse_outside_controlled_window_waits_for_existing_schedule(now):
    f=Continuation(reuse=False); clock=datetime.fromisoformat(now)
    plan=m.inspect(f,clock)
    assert m.act(f,plan,clock,execute=True)['status']=='P0_REDEPLOY_REQUIRES_EXISTING_NATURAL_SCHEDULE'
    assert 'workflow' not in plan and not f.posts


@pytest.mark.parametrize('at_inspect',[True,False])
def test_continuation_deadline_is_0915_not_normal_0920(at_inspect):
    f=Continuation(); too_late=datetime.fromisoformat('2026-09-23T09:15:00+08:00')
    with pytest.raises(ValueError,match='0915_DEADLINE|DISPATCH_DEADLINE'):
        plan=m.inspect(f,too_late if at_inspect else NOW)
        m.act(f,plan,too_late,execute=True)
    assert not f.posts


@pytest.mark.parametrize('change,reason',[
    ('other_original','CONTROLLED_NATURAL'),('old_original','ORIGINAL_GENERATING'),
    ('attempt2','ORIGINAL_GENERATING'),('compute_failure','JOB_OUTCOMES'),
    ('cas_failure','ORIGINAL_GENERATING'),('secondary_failure','JOB_OUTCOMES'),
    ('jobs_partial','COMPLETE_JOBS'),('jobs_api_failure','GITHUB_READ_UNAVAILABLE'),
    ('duplicate_step','STEP_IDENTITY'),('no_upload','GENERATION_OR_REUSE'),
    ('reuse_generated','GENERATION_OR_REUSE'),('reuse_cas','JOB_OUTCOMES'),
    ('cas_parent','CAS_TIME_OR_PARENT'),('cas_time','CAS_TIME_OR_PARENT'),
    ('ambiguous_cas','UNIQUE_ORIGINAL_CAS'),('cas_source_changed','ORIGINAL_BYTES'),
    ('current_source_changed','ORIGINAL_BYTES'),('expired_artifact','ARTIFACT_ORIGIN'),
    ('no_artifact','ORIGINAL_ARTIFACT'),('artifact_partial','COMPLETE_ARTIFACT'),
    ('artifact_digest','ARTIFACT_ORIGIN'),('artifact_run','ARTIFACT_ORIGIN'),
    ('artifact_after_cas','ARTIFACT_ORIGIN'),('run_list_partial','COMPLETE_RUN_LIST')])
def test_continuation_rejects_broken_provenance_without_dispatch(change,reason):
    f=Continuation()
    original=f.p0_runs[1]; generation=f.p0_jobs[7]['jobs']; reuse=f.p0_jobs[70]['jobs']
    if change=='other_original':original['name']='ordinary manual run'
    if change=='old_original':original['created_at']='2026-09-21T12:00:00Z'
    if change=='attempt2':original['run_attempt']=2
    if change=='compute_failure':generation[0]['conclusion']='failure'
    if change=='cas_failure':generation[1]['conclusion']='failure'
    if change=='secondary_failure':
        generation.append({**generation[0],'id':799,'name':'unrelated check','conclusion':'failure'})
        f.p0_jobs[7]['total_count']+=1
    if change=='jobs_partial':f.p0_jobs[7]['total_count']+=1
    if change=='duplicate_step':generation[0]['steps'].append(deepcopy(generation[0]['steps'][0]))
    if change=='no_upload':generation[0]['steps'][-1]['conclusion']='skipped'
    if change=='reuse_generated':reuse[0]['steps'][-1]['conclusion']='success'
    if change=='reuse_cas':reuse[1]['conclusion']='success'
    if change=='cas_parent':f.commit['parents']=[{'sha':'d'*40}]
    if change=='cas_time':f.commit['committer']['date']='2026-09-22T12:10:00Z'
    if change=='ambiguous_cas':f.commits.append(deepcopy(f.commits[0]))
    if change=='cas_source_changed':f.revisions[f.cas]['outputs/decision/three_rank_top10_20260922.csv']=b'changed'
    if change=='current_source_changed':f.files['outputs/decision/primary_d_runtime_features_20260922.csv']=b'changed'
    if change=='expired_artifact':f.artifacts['artifacts'][0]['expired']=True
    if change=='no_artifact':f.artifacts={'total_count':0,'artifacts':[]}
    if change=='artifact_partial':f.artifacts['total_count']=2
    if change=='artifact_digest':f.artifacts['artifacts'][0]['digest']='sha256:invalid'
    if change=='artifact_run':f.artifacts['artifacts'][0]['workflow_run']['id']=70
    if change=='artifact_after_cas':f.artifacts['artifacts'][0]['updated_at']='2026-09-22T12:06:00Z'
    request=f.request
    def changed_request(path,body=None):
        if change=='jobs_api_failure' and '/attempts/1/jobs' in path:raise ValueError('GITHUB_READ_UNAVAILABLE')
        result=request(path,body)
        if change=='run_list_partial' and 'created=%3E' in path:result['total_count']+=1
        return result
    f.request=changed_request
    with pytest.raises(ValueError,match=reason):m.act(f,m.inspect(f,NOW),NOW,execute=True)
    assert not f.posts


def test_ordinary_manual_reuse_cannot_supply_continuation():
    f=Continuation(reuse=False)
    f.add_reuse(event='workflow_dispatch',name='ordinary',display_title='ordinary')
    assert m.inspect(f,NOW)['status']=='NEEDS_P0_REDEPLOY'


def test_controlled_natural_reuse_is_accepted_without_recompute():
    f=Continuation(reuse=False); title='DC20 controlled daily NATURAL | D=20260922'
    f.add_reuse(event='workflow_dispatch',name=title,display_title=title)
    assert m.inspect(f,NOW)['status']=='NEEDS_FREEZE_CONTINUATION'


def test_after_midnight_natural_reuse_continues_original_day():
    f=Continuation(); r=f.p0_runs[0]
    for key in ('created_at','run_started_at','updated_at'):r[key]=r[key].replace('T13:','T17:')
    for job in f.p0_jobs[70]['jobs']:
        for key in ('started_at','completed_at'):job[key]=job[key].replace('T13:','T17:')
    plan=m.inspect(f,datetime.fromisoformat('2026-09-23T02:00:00+08:00'))
    assert plan['signal_date']=='20260922' and plan['status']=='NEEDS_FREEZE_CONTINUATION'


def test_existing_successful_generation_is_preferred_over_later_noop():
    f=Continuation(); f.p0_runs[1]['conclusion']='success'
    f.p0_jobs[7]['jobs'][2]['conclusion']='success'
    plan=m.inspect(f,NOW)
    assert plan['status']=='NEEDS_FREEZE' and plan['inputs']=={'p0_run_id':'7','dry_run':False}


def test_running_p0_reuse_is_followed_without_duplicate_dispatch():
    f=Continuation(reuse=False)
    f.busy=[run(m.P0,70,status='in_progress',conclusion=None)]
    result=m.act(f,m.inspect(f,NOW),NOW,execute=True)
    assert result['status']=='UPSTREAM_OR_RECOVERY_ALREADY_RUNNING' and not f.posts


def test_p0_redeploy_still_has_bounded_retry_budget():
    f=Continuation(reuse=False)
    f.busy=[run(m.P0,70+i,conclusion='failure',created_at='2026-09-22T13:00:00Z',
                display_title='DC20 controlled daily NATURAL | D=20260922') for i in range(3)]
    with pytest.raises(ValueError,match='RETRY_BUDGET'):m.act(f,m.inspect(f,NOW),NOW,execute=True)
    assert not f.posts


@pytest.mark.parametrize('kind',['p0_sources','local_freeze'])
def test_continuation_does_not_overwrite_partial_freeze(kind):
    f=Continuation()
    f.files[m.ROOT+f'candidate_natural_forward/{kind}_20260922.json']=b'{}'
    with pytest.raises(ValueError,match='INCOMPLETE_FREEZE'):m.inspect(f,NOW)
    assert not f.posts


def test_failed_pre_cas_attempt_does_not_hide_original_receipt_writer():
    f=Continuation()
    r=run(m.P0,6,conclusion='failure')
    f.p0_runs.append(r); f.p0_jobs[6]=p0_jobs(r)
    f.p0_jobs[6]['jobs'][1]['conclusion']='skipped'
    assert m.inspect(f,NOW)['inputs']['original_generation_run_id']=='7'


def test_ambiguous_generating_runs_are_not_guessed():
    f=Continuation()
    r={**f.p0_runs[1],'id':6}
    f.p0_runs.append(r); f.p0_jobs[6]=p0_jobs(r)
    with pytest.raises(ValueError,match='ORIGINAL_GENERATING_P0_RUN_MISSING_OR_AMBIGUOUS'):m.inspect(f,NOW)
    assert not f.posts


def test_original_cas_visible_while_pages_running_is_followed_without_new_freeze():
    f=Fake()
    original={**f.p0_runs[0], 'status':'in_progress', 'conclusion':None}
    f.p0_runs=[]  # The completed-only endpoint correctly excludes it.
    f.busy=[original]
    before=deepcopy(f.files)
    plan=m.inspect(f,NOW)
    assert plan['status']=='UPSTREAM_OR_RECOVERY_ALREADY_RUNNING'
    assert plan['pending_stage']=='ORIGINAL_P0_COMPLETION'
    assert plan['queue']['same_day_target_run_ids']==[7]
    assert m.act(f,plan,NOW,execute=True)==plan
    assert not f.posts and f.files==before


def test_original_cas_wait_does_not_treat_queue_api_failure_as_empty():
    f=Fake();f.p0_runs=[]
    request=f.request
    def unavailable(path,body=None):
        if path=='/actions/concurrency_groups?per_page=100':
            raise ValueError('GITHUB_READ_UNAVAILABLE')
        return request(path,body)
    f.request=unavailable
    with pytest.raises(ValueError,match='GITHUB_READ_UNAVAILABLE'):
        m.inspect(f,NOW)
    assert not f.posts


@pytest.mark.parametrize('continuation,cutoff', [
    (False,'2026-09-23T09:20:00+08:00'),
    (True,'2026-09-23T09:15:00+08:00'),
])
def test_live_clock_after_final_queue_read_prevents_expired_freeze(continuation,cutoff):
    f=Continuation() if continuation else Fake()
    plan=m.inspect(f,NOW)
    request=f.request;calls=[]
    def tracked(path,body=None):
        calls.append(path)
        return request(path,body)
    f.request=tracked
    def advanced_clock():
        assert calls[-1]=='/git/ref/heads/main'
        return datetime.fromisoformat(cutoff)
    with pytest.raises(ValueError,match='DISPATCH_DEADLINE_EXPIRED'):
        m.act(f,plan,NOW,execute=True,clock=advanced_clock)
    assert not f.posts


def test_live_clock_before_deadline_still_dispatches_one_existing_workflow():
    f=Continuation();plan=m.inspect(f,NOW)
    live=datetime.fromisoformat('2026-09-23T09:14:59+08:00')
    assert m.act(f,plan,NOW,execute=True,clock=lambda:live)['status']=='DISPATCHED_NOT_COMPLETED'
    assert len(f.posts)==1


def test_executable_watchdog_always_injects_real_dispatch_clock():
    from pathlib import Path
    source=(Path(__file__).resolve().parents[1]/'scripts/candidate_delivery_watchdog.py').read_text()
    assert 'clock=lambda: datetime.now(timezone.utc)' in source.split('def main():',1)[1]

@pytest.mark.parametrize('n',[0,1,2])
def test_legal_small_promotion_pool_continues_without_inventing_top3(n):
    f=Fake(); path='outputs/decision/three_rank_top10_20260922.json'
    f.files[path]=raw({'rows':[{'promotion_rank':i+1} for i in range(n)]})
    receipt=json.loads(f.files['outputs/decision/primary_d_receipt_20260922.json'])
    receipt['outputs']['json_sha256']=m.digest(f.files[path])
    f.files['outputs/decision/primary_d_receipt_20260922.json']=raw(receipt)
    assert m.inspect(f,NOW)['status']=='NEEDS_FREEZE'

@pytest.mark.parametrize('n',[0,1])
def test_legitimate_missing_profit_slots_are_verified_terminal_delivery(n):
    f=Fake();f.formal();path=m.PUBLIC+'day_20260922.json';day=json.loads(f.files[path])
    day['rows']=day['rows'][:n]
    day['candidate_slots']=[{'slot':i+1,'status':'PENDING_T' if i<n else 'MISSING_CANDIDATE',
        'ts_code':day['rows'][i]['ts_code'] if i<n else None} for i in range(2)]
    f.files[path]=raw(day);f.files[m.PUBLIC+'index.json']=raw({'days':[{'signal_date':'20260922','sha256':m.digest(f.files[path])}]})
    summary=json.loads(f.files[m.PUBLIC+'summary.json'])
    for i,g in enumerate(('candidate_top1','candidate_top2')):
        row=summary['groups'][g]['daily_sequence'][0];row['formal_projection_sha256']=m.canonical(day)
        if i>=n:row.update(ts_code=None,status='MISSING_CANDIDATE')
    f.files[m.PUBLIC+'summary.json']=raw(summary)
    result=m.inspect(f,NOW)
    assert result['status']=='FORMAL_AVAILABLE_SLOTS_LEDGER_AND_PAGE_PRESENT'
    assert result['missing_profit_slots']==2-n and not f.posts
    day['candidate_slots'][-1]['slot_net_return']=0;f.files[path]=raw(day)
    f.files[m.PUBLIC+'index.json']=raw({'days':[{'signal_date':'20260922','sha256':m.digest(f.files[path])}]})
    with pytest.raises(ValueError,match='ABSENT_SLOT'):m.inspect(f,NOW)

def test_original_schedule_can_continue_without_claiming_controlled_identity():
    f=Continuation();r=next(x for x in f.p0_runs if x['id']==7)
    r.update(event='schedule',name=m.P0_NAME,display_title=m.P0_NAME)
    assert m.inspect(f,NOW)['status']=='NEEDS_FREEZE_CONTINUATION'
    r['name']='other'
    with pytest.raises(ValueError,match='REGISTERED_SCHEDULE'):m.inspect(f,NOW)

@pytest.mark.parametrize('mutation',[None,'old_day','wrong_source_index','wrong_day','wrong_p0','wrong_snapshot','wrong_index_hash'])
def test_degraded_public_core_requires_exact_current_day_and_all_hashes(monkeypatch,mutation):
    f=Fake();f.formal();f.public_core_status=lambda *args:m.GitHub.public_core_status(f,*args)
    d='20260922';indexpath=m.PUBLIC+'index.json';daypath=m.PUBLIC+f'day_{d}.json'
    source_index=f.files[indexpath];day=json.loads(f.files[daypath]);day['snapshot_file_sha256']='a'*64
    f.files[daypath]=raw(day)
    entry=dict(signal_date=d,path=daypath,sha256=m.digest(f.files[daypath]),
        p0_file_sha256=day['p0_file_sha256'],snapshot_file_sha256=day['snapshot_file_sha256'])
    f.files[indexpath]=raw({'days':[entry]});source_index=f.files[indexpath]
    marker=dict(schema_version='dc20_candidate_delivery_isolation_v1',signal_date=d,status='PARTIAL',
        current_ranking_status='READY',summary_status='UNAVAILABLE',new_freeze_created=False,
        historical_results_rewritten=False,source_index_sha256=m.digest(source_index),excluded_days=[])
    public={indexpath:source_index,daypath:f.files[daypath],
        f'outputs/decision/three_rank_top10_{d}.json':f.files[f'outputs/decision/three_rank_top10_{d}.json']}
    rev=dict(signal_date=d,primary_d_status='READY',candidate_profit_delivery=marker,
        candidate_profit_index_url=indexpath,candidate_profit_index_sha256=m.digest(source_index))
    if mutation=='old_day':rev['signal_date']='20260921'
    elif mutation=='wrong_source_index':marker['source_index_sha256']='0'*64
    elif mutation=='wrong_day':public[daypath]+=b' '
    elif mutation=='wrong_p0':public[f'outputs/decision/three_rank_top10_{d}.json']+=b' '
    elif mutation=='wrong_snapshot':entry['snapshot_file_sha256']='b'*64;public[indexpath]=raw({'days':[entry]});rev['candidate_profit_index_sha256']=m.digest(public[indexpath])
    elif mutation=='wrong_index_hash':rev['candidate_profit_index_sha256']='0'*64
    public['revision.json']=raw(rev)
    import io
    monkeypatch.setattr(m.urllib.request,'urlopen',lambda url,timeout:io.BytesIO(public[url.removeprefix('https://njedu2023-prog.github.io/DC20/')]))
    result=f.public_core_status(d,public[f'outputs/decision/three_rank_top10_{d}.json'] if mutation!='wrong_p0' else f.files[f'outputs/decision/three_rank_top10_{d}.json'],source_index,f.files[daypath])
    assert (result is not None)==(mutation is None)
    if mutation is None:
        f.files.pop(m.PUBLIC+'summary.json')
        plan=m.inspect(f,NOW)
        assert plan['status']=='CORE_DELIVERED_AUX_PENDING' and 'workflow' not in plan

@pytest.mark.parametrize('bad',[None,'stale_revision','missing_p0','changed_p0'])
def test_healthy_public_delivery_also_requires_current_primary_list(monkeypatch,bad):
    f=Fake();f.formal();d='20260922';p0=f.files[f'outputs/decision/three_rank_top10_{d}.json']
    paths={m.PUBLIC+name:f.files[m.PUBLIC+name] for name in ('index.json','summary.json',f'day_{d}.json')}
    paths['revision.json']=raw(dict(signal_date='20260921' if bad=='stale_revision' else d,primary_d_status='READY'))
    paths[f'outputs/decision/three_rank_top10_{d}.json']=p0+(b' ' if bad=='changed_p0' else b'')
    import io
    def fetch(url,timeout):
        path=url.removeprefix('https://njedu2023-prog.github.io/DC20/')
        if bad=='missing_p0' and path.startswith('outputs/decision/three_rank_top10_'):raise OSError('404')
        return io.BytesIO(paths[path])
    monkeypatch.setattr(m.urllib.request,'urlopen',fetch)
    assert m.GitHub.public_ready(f,d,m.digest(paths[m.PUBLIC+f'day_{d}.json']),m.digest(paths[m.PUBLIC+'summary.json']),p0)==(bad is None)

@pytest.mark.parametrize('kind',['day','workflow','p0_sources','local_freeze'])
def test_partial_original_capsule_has_no_blind_dispatch_or_overwrite(kind):
    f=Fake();f.freeze();f.files.pop(m.ROOT+f'candidate_natural_forward/{kind}_20260922.json')
    before=deepcopy(f.files)
    with pytest.raises(ValueError,match='INCOMPLETE_FREEZE'):m.inspect(f,NOW)
    assert f.files==before and not f.posts


def completion_event(client, upstream=None):
    upstream = deepcopy(upstream or client.p0_runs[0])
    original_request = client.request
    def request(path, body=None):
        if body is None and path == f'/actions/runs/{upstream["id"]}':
            return deepcopy(upstream)
        return original_request(path, body)
    client.request = request
    return {'action': 'completed', 'workflow_run': deepcopy(upstream)}, upstream


def test_completion_failure_redeploys_then_success_only_resumes_original_capsule():
    f = Continuation(reuse=False)
    before = deepcopy(f.files)
    event, _ = completion_event(f)
    day = m.completion_wakeup(f, event, NOW)
    plan = m.inspect(f, NOW, wakeup_day=day)
    assert plan['status'] == 'NEEDS_P0_REDEPLOY'
    assert m.act(f, plan, NOW, execute=True)['status'] == 'DISPATCHED_NOT_COMPLETED'
    assert f.posts[0][1]['inputs']['pred_commit'] == 'd' * 40
    assert f.posts[0][1]['inputs']['market_commit'] == 'e' * 40
    title = 'DC20 controlled daily NATURAL | D=20260922'
    reused = f.add_reuse(name=title, display_title=title, event='workflow_dispatch')
    event, _ = completion_event(f, reused)
    day = m.completion_wakeup(f, event, NOW)
    plan = m.inspect(f, NOW, wakeup_day=day)
    assert plan['status'] == 'NEEDS_FREEZE_CONTINUATION'
    assert plan['inputs'] == {'p0_run_id': '70', 'original_generation_run_id': '7',
                              'original_cas_commit': f.cas, 'dry_run': False}
    assert f.files == before


def test_success_completion_waits_for_already_queued_natural_forward():
    f = Fake()
    title = 'DC20 controlled daily NATURAL | D=20260922'
    r = run(m.P0, name=title, display_title=title)
    event, _ = completion_event(f, r)
    f.busy = [run(m.NATURAL, status='queued', conclusion=None)]
    day = m.completion_wakeup(f, event, NOW)
    assert m.act(f, m.inspect(f, NOW, wakeup_day=day), NOW, execute=True)['status'] == 'UPSTREAM_OR_RECOVERY_ALREADY_RUNNING'
    assert not f.posts


@pytest.mark.parametrize('field,value', [
    ('workflow_id', 1), ('path', '.github/workflows/candidate_delivery_watchdog.yml'),
    ('run_attempt', 2), ('head_branch', 'other'), ('head_sha', 'bad'),
    ('repository', {'full_name': 'other/repo'}), ('head_repository', {'full_name': 'other/repo'}),
    ('status', 'in_progress'), ('conclusion', 'cancelled'), ('event', 'workflow_run'),
    ('event', 'push'), ('event', 'schedule'), ('name', 'unregistered'), ('display_title', 'ordinary manual'),
    ('updated_at', '2026-09-23T12:10:00Z'), ('run_started_at', '2026-09-22T11:00:00Z'),
])
def test_completion_source_mutation_never_dispatches(field, value):
    f = Continuation(reuse=False)
    r = deepcopy(f.p0_runs[0]); r[field] = value
    event, _ = completion_event(f, r)
    with pytest.raises(ValueError):
        m.completion_wakeup(f, event, NOW)
    assert not f.posts


@pytest.mark.parametrize('kind', ['api_drift', 'api_error', 'bad_action', 'missing_run'])
def test_completion_api_failure_or_event_mismatch_is_not_a_successful_noop(kind):
    f = Continuation(reuse=False)
    event, api_run = completion_event(f)
    if kind == 'api_drift': api_run['head_sha'] = 'f' * 40
    if kind == 'bad_action': event['action'] = 'requested'
    if kind == 'missing_run': del event['workflow_run']
    if kind == 'api_error':
        def unavailable(*args, **kwargs): raise ValueError('GITHUB_READ_UNAVAILABLE')
        f.request = unavailable
    with pytest.raises(ValueError):
        m.completion_wakeup(f, event, NOW)
    assert not f.posts


def test_old_completion_is_explicit_noop_before_inspecting_current_business_files():
    f = Continuation(reuse=False)
    r = deepcopy(f.p0_runs[0])
    for key in ('created_at', 'run_started_at', 'updated_at'):
        r[key] = r[key].replace('2026-09-22', '2026-09-21')
    r['name'] = r['display_title'] = 'DC20 controlled daily NATURAL | D=20260921'
    event, _ = completion_event(f, r)
    day = m.completion_wakeup(f, event, NOW)
    f.files['outputs/decision/primary_d_receipt_20260922.json'] = b'corrupt-current-business-file'
    plan = m.inspect(f, NOW, wakeup_day=day)
    assert plan['status'] == 'STALE_P0_COMPLETION_IGNORED'
    assert plan['signal_date'] == '20260922' and plan['wakeup_signal_date'] == '20260921'
    assert plan['reason'] == 'COMPLETION_D_DIFFERS_FROM_CURRENT_STRICT_CALENDAR_D'
    assert m.act(f, plan, NOW, execute=True) == plan and not f.posts


def test_delayed_friday_schedule_created_monday_cannot_wake_monday_controlled_recovery():
    f = Continuation(reuse=False)
    monday = datetime.fromisoformat('2026-09-28T22:00:00+08:00')
    r = run(m.P0, event='schedule', name=m.P0_NAME, display_title=m.P0_NAME,
            created_at='2026-09-28T12:00:00Z', run_started_at='2026-09-28T12:00:01Z',
            updated_at='2026-09-28T12:10:00Z')
    event, _ = completion_event(f, r)
    with pytest.raises(ValueError, match='P0_COMPLETION_SOURCE_INVALID'):
        m.completion_wakeup(f, event, monday)
    assert not f.posts


@pytest.mark.parametrize('bad_event', [False, True])
def test_registered_workflow_run_cli_authenticates_before_dispatch(monkeypatch, tmp_path, capsys, bad_event):
    import sys
    f = Continuation(reuse=False)
    event, _ = completion_event(f)
    if bad_event: event['workflow_run']['head_sha'] = 'f' * 40
    event_path = tmp_path / 'event.json'; event_path.write_text(json.dumps(event))
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return NOW.astimezone(tz) if tz else NOW
    monkeypatch.setattr(m, 'datetime', Clock)
    monkeypatch.setattr(m, 'GitHub', lambda token: f)
    monkeypatch.setattr(sys, 'argv', ['candidate_delivery_watchdog.py', '--execute'])
    for key, value in {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': m.REPO,
                       'GITHUB_REF': 'refs/heads/main', 'GITHUB_RUN_ATTEMPT': '1',
                       'GITHUB_WORKFLOW_REF': m.REPO + '/' + m.WORKFLOW + '@refs/heads/main',
                       'GITHUB_EVENT_NAME': 'workflow_run', 'GITHUB_EVENT_PATH': str(event_path)}.items():
        monkeypatch.setenv(key, value)
    assert m.main() == int(bad_event)
    output = capsys.readouterr().out
    if bad_event:
        assert 'P0_COMPLETION_API_IDENTITY_CHANGED' in output and not f.posts
    else:
        assert json.loads(output)['status'] == 'DISPATCHED_NOT_COMPLETED' and len(f.posts) == 1



@pytest.mark.parametrize('continuation', [False, True])
def test_previous_day_morning_failures_do_not_consume_new_p0_budget(continuation):
    f = Continuation() if continuation else Fake()
    f.busy = [run(m.NATURAL, 80+i, conclusion='failure',
                  created_at='2026-09-22T00:19:23Z') for i in range(4)]
    plan = m.inspect(f, NOW)
    assert plan['retry_origin_run_id'] == '7'
    assert plan['retry_not_before'] == '2026-09-22T12:00:00Z'
    assert m.act(f, plan, NOW, execute=True)['status'] == 'DISPATCHED_NOT_COMPLETED'
    assert len(f.posts) == 1


@pytest.mark.parametrize('created', ['2026-09-22T12:00:00Z', '2026-09-22T12:30:00Z'])
def test_continuation_keeps_failures_at_original_boundary_and_before_reuse(created):
    f = Continuation()
    f.busy = [run(m.NATURAL, 80+i, conclusion='failure', created_at=created) for i in range(3)]
    with pytest.raises(ValueError, match='RETRY_BUDGET'):
        m.act(f, m.inspect(f, NOW), NOW, execute=True)
    assert not f.posts


def test_old_failures_excluded_but_new_failures_still_exhaust_budget():
    f = Continuation()
    f.busy = [run(m.NATURAL, 80+i, conclusion='failure',
                  created_at='2026-09-22T00:19:23Z') for i in range(4)]
    f.busy += [run(m.NATURAL, 90+i, conclusion='timed_out',
                   created_at='2026-09-22T12:30:00Z') for i in range(3)]
    with pytest.raises(ValueError, match='RETRY_BUDGET'):
        m.act(f, m.inspect(f, NOW), NOW, execute=True)
    assert not f.posts


@pytest.mark.parametrize('change', ['missing_origin', 'successor_origin', 'missing_time', 'future_time'])
def test_retry_origin_evidence_is_required(change):
    f = Continuation(); plan = m.inspect(f, NOW)
    if change == 'missing_origin': plan.pop('retry_origin_run_id')
    if change == 'successor_origin': plan['retry_origin_run_id'] = '70'
    if change == 'missing_time': plan.pop('retry_not_before')
    if change == 'future_time': plan['retry_not_before'] = '2026-09-22T23:00:00+08:00'
    with pytest.raises(ValueError): m.act(f, plan, NOW, execute=True)
    assert not f.posts


@pytest.mark.parametrize('setup,workflow', [('freeze', m.OBSERVER), ('observe', m.PUBLISHER)])
def test_other_stage_retry_budget_remains_unchanged(setup, workflow):
    f = Fake(); getattr(f, setup)()
    f.busy = [run(workflow, 80+i, conclusion='failure',
                  created_at='2026-09-22T00:19:23Z') for i in range(3)]
    with pytest.raises(ValueError, match='RETRY_BUDGET'):
        m.act(f, m.inspect(f, NOW), NOW, execute=True)
    assert not f.posts


def test_retry_budget_survives_midnight_and_new_deployment_head():
    f = Continuation()
    f.busy = [run(m.NATURAL, 80+i, conclusion='failure',
                  created_at='2026-09-22T12:30:00Z') for i in range(3)]
    f.revisions[f.head] = deepcopy(f.files)
    f.head = 'f' * 40
    plan = m.inspect(f, datetime.fromisoformat('2026-09-23T02:00:00+08:00'))
    assert plan['head'] == 'f' * 40 and plan['retry_origin_run_id'] == '7'
    with pytest.raises(ValueError, match='RETRY_BUDGET'):
        m.act(f, plan, datetime.fromisoformat('2026-09-23T02:00:00+08:00'), execute=True)
    assert not f.posts
