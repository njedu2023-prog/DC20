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
    return dict(id=id, workflow_id=m.IDS.get(workflow, 8), path='.github/workflows/'+workflow,
        run_attempt=1, head_branch='main', repository={'full_name':m.REPO},
        head_repository={'full_name':m.REPO}, event='workflow_dispatch', status='completed',
        conclusion='success', created_at='2026-09-22T12:00:00Z', **changes)

class Fake:
    def __init__(self):
        self.head='a'*40; self.posts=[]; self.busy=[]; self.live=True
        cal=b'exchange,cal_date,is_open\nSSE,20260921,1\nSSE,20260922,1\nSSE,20260923,1\nSSE,20260924,1\nSSE,20260925,1\n'
        p0=raw({'rows':[{'promotion_rank':i} for i in (1,2,3)]})
        self.files={'models/decision_candidate_profit_activation_v1.json':raw({'enabled':True}),
          'data/market/trade_cal_sse.csv':cal,
          'outputs/decision/three_rank_top10_20260922.json':p0,
          'outputs/decision/primary_d_receipt_20260922.json':raw(dict(signal_date='20260922',exec_date='20260923',exit_date='20260924',primary_status='READY',generation_mode='NATURAL',inputs={'calendar':{'sha256':m.digest(cal)}},outputs={'json_sha256':m.digest(p0)}))}
    def file(self,path,head): assert head==self.head; return self.files.get(path)
    def public_ready(self,*args): return self.live
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
            start=datetime.fromisoformat(query['created'][0][2:].replace('Z','+00:00'))
            rows=[r for r in self.busy if r.get('path')=='.github/workflows/'+workflow and
                  datetime.fromisoformat(r['created_at'].replace('Z','+00:00'))>=start]
            page=int(query['page'][0])
            return {'total_count':len(rows),'workflow_runs':deepcopy(rows[(page-1)*100:page*100])}
        if path.startswith('/actions/workflows/'+m.P0+'/runs'): return {'workflow_runs':[run(m.P0)]}
        if path.endswith('/jobs?per_page=100'): return {'jobs':[{'steps':[{'name':'Upload immutable P0 candidate','conclusion':'success'}]}]}
        if path=='/actions/runs/8': return run(m.NATURAL,8)
        if path=='/actions/runs/9': return run(m.OBSERVER,9)
        if path.endswith('/runs?per_page=50'): return {'workflow_runs':self.busy}
        raise AssertionError(path)
    def freeze(self):
        snap=raw(dict(signal_date='20260922',exec_date='20260923',exit_date='20260924'))
        self.files[m.ROOT+'candidate_natural_forward/day_20260922.json']=snap
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

