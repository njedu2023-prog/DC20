from datetime import datetime, timedelta, timezone

import pytest

from scripts import candidate_delivery_followup as monitor

w = monitor.delivery
START = datetime(2026, 11, 24, 16, 30, tzinfo=timezone.utc)
DAY = '20261124'
HEAD = 'a' * 40


def handoff(status='DISPATCHED_NOT_COMPLETED'):
    return {'signal_date': DAY, 'exec_date': '20261125', 'head': HEAD, 'workflow': w.NATURAL,
            'retry_not_before': '2026-11-24T11:27:33Z', 'status': status,
            'queue': {'checked_at': START.isoformat(), 'same_day_target_run_ids': [72]}}


class Fixture:
    def __init__(self, monkeypatch, start=START):
        self.now = start
        self.target = w.NATURAL
        self.posts = []
        self.runs = {}
        self.minutes = 0
        self.tick = lambda: None
        self.error = None
        self.advance_inside_act = False
        self.inspect_failures = []
        self.job_conclusion = 'skipped'
        monkeypatch.setattr(w, 'inspect', self.inspect)
        monkeypatch.setattr(w, 'act', self.act)
        monkeypatch.setattr(w, 'recent_target_runs', lambda client, target, day: self.runs.get(target, []))

    def request(self,path):
        return {'total_count': 1, 'jobs': [{'run_id': 73, 'name': 'Verify and preserve original publication evidence',
                                           'conclusion': self.job_conclusion}]}

    def inspect(self,client,now,*,wakeup_day):
        assert wakeup_day == DAY
        if self.inspect_failures:
            raise ValueError(self.inspect_failures.pop(0))
        if self.target == 'complete':
            return {'status': 'FORMAL_TOP2_LEDGER_AND_PAGE_PRESENT', 'profit_slots': [1,2], 'signal_date': DAY}
        if self.target == 'changed':
            return {'status': 'STALE_P0_COMPLETION_IGNORED'}
        return {'status':'NEEDS_WORK', 'workflow':self.target, 'signal_date':DAY}

    def act(self,client,plan,now,*,execute,clock):
        assert execute
        if self.advance_inside_act:
            self.now += timedelta(hours=1)
        clock()
        self.posts.append(plan['workflow'])
        if self.error:
            raise ValueError(self.error)
        return {'status':'DISPATCHED_NOT_COMPLETED'}

    def sleep(self,seconds):
        assert 0 < seconds <= 60
        self.minutes += 1
        self.now += timedelta(seconds=seconds)
        self.tick()

    def run(self, initial=None):
        return monitor.follow(initial or handoff(),lambda:self,lambda:self.now,self.sleep)

    def row(self, ident, status='completed', conclusion='success', event='workflow_dispatch'):
        return {'id':ident,'status':status,'conclusion':conclusion,'run_attempt':1,'head_sha':HEAD,
                'created_at':START.isoformat(),'event':event}


def test_registered_followup_completes_when_callbacks_are_absent(monkeypatch):
    f=Fixture(monkeypatch)
    def tick():
        if f.minutes == 2: f.target = w.OBSERVER
        if f.minutes == 6: f.target = w.PUBLISHER
        if f.minutes == 10: f.target = 'complete'
    f.tick=tick
    assert f.run()['profit_slots'] == [1,2]
    assert f.posts == [w.OBSERVER,w.PUBLISHER]


@pytest.mark.parametrize('event', ['workflow_run','workflow_dispatch'])
def test_callback_in_flight_is_followed_with_no_new_dispatch(monkeypatch,event):
    f=Fixture(monkeypatch)
    f.runs[w.NATURAL] = [f.row(72,'in_progress',None,event=event)]
    def tick():
        if f.minutes == 2:
            f.target = w.OBSERVER
            f.runs[w.NATURAL] = []
            f.runs[w.OBSERVER] = [f.row(73,'in_progress',None)]
        if f.minutes == 4:
            f.target = w.PUBLISHER
            f.runs[w.PUBLISHER] = [f.row(74,'in_progress',None,event='schedule')]
        if f.minutes == 6: f.target = 'complete'
    f.tick=tick
    assert f.run(handoff('UPSTREAM_OR_RECOVERY_ALREADY_RUNNING'))['profit_slots'] == [1,2]
    assert not f.posts


@pytest.mark.parametrize('kind', ['push','stale','unrelated','different_head'])
def test_unbound_running_job_does_not_start_monitor(monkeypatch,kind):
    f=Fixture(monkeypatch)
    r=f.row(72,'queued',None,event='workflow_run')
    if kind=='push': r['event']='push'
    if kind=='stale': r['created_at']='2026-11-20T00:00:00Z'
    if kind=='unrelated': r['id']=100
    if kind=='different_head': r['head_sha']='b'*40
    f.runs[w.NATURAL]=[r]
    assert f.run(handoff('UPSTREAM_OR_RECOVERY_ALREADY_RUNNING'))['status']=='NO_CONFIRMED_ACTIVE_RIDGE_RUN'
    assert not f.posts and not f.minutes


@pytest.mark.parametrize('initial', [{'status':'DRY_RUN_WOULD_DISPATCH','workflow':w.NATURAL},
                                     {'status':'DISPATCHED_NOT_COMPLETED','workflow':w.P0}])
def test_read_only_and_nonridge_origins_do_not_follow(monkeypatch,initial):
    f=Fixture(monkeypatch)
    assert f.run(initial)['status']=='NO_RIDGE_HANDOFF_TO_FOLLOW'
    assert not f.posts and not f.minutes


def test_registry_failure_rechecked_and_unknown_post_not_repeated(monkeypatch):
    f=Fixture(monkeypatch)
    f.target=w.OBSERVER
    f.inspect_failures=['GITHUB_READ_UNAVAILABLE']
    f.error='DISPATCH_OUTCOME_UNKNOWN_NO_BLIND_RETRY'
    result=f.run()
    assert result['status']=='DELIVERY_FOLLOWUP_ENDED_WITHOUT_PUBLIC_ACCEPTANCE'
    assert f.posts==[w.OBSERVER]


def test_unbound_terminal_failure_is_not_attributed_to_today_or_retried(monkeypatch):
    f=Fixture(monkeypatch)
    f.target=w.OBSERVER
    f.runs[w.OBSERVER]=[f.row(73,conclusion='failure')]
    result=f.run()
    assert result['status']=='DELIVERY_FOLLOWUP_ENDED_WITHOUT_PUBLIC_ACCEPTANCE'
    assert result['last_observation']=='RUN_OUTCOME_AWAITS_SAME_DAY_EVIDENCE'
    assert not f.posts


def test_green_validation_only_callback_does_not_hide_missing_observer(monkeypatch):
    f=Fixture(monkeypatch)
    f.target=w.OBSERVER
    f.runs[w.OBSERVER]=[f.row(73,conclusion='success',event='workflow_run')]
    f.tick=lambda: setattr(f,'target','complete') if f.minutes==5 else None
    assert f.run()['status']=='FORMAL_TOP2_LEDGER_AND_PAGE_PRESENT'
    assert f.posts==[w.OBSERVER]


@pytest.mark.parametrize('kind', ['real_cutoff','max_duration','day_changed','late_queue'])
def test_stops_honestly_at_boundary(monkeypatch,kind):
    start=START if kind!='real_cutoff' else datetime(2026,11,25,1,19,20,tzinfo=timezone.utc)
    f=Fixture(monkeypatch,start)
    if kind=='day_changed': f.target='changed'
    if kind=='late_queue':
        f.target=w.OBSERVER
        f.advance_inside_act=True
    result=f.run()
    assert result['status']=='DELIVERY_FOLLOWUP_ENDED_WITHOUT_PUBLIC_ACCEPTANCE' and not f.posts
    if kind=='max_duration':
        assert f.now == START + timedelta(minutes=40)
    if kind=='real_cutoff':
        assert f.now.hour == 1 and f.now.minute == 20


def test_unexpected_guard_error_is_reported(monkeypatch):
    f=Fixture(monkeypatch)
    f.inspect_failures=['P0_BYTES_CHANGED']
    with pytest.raises(ValueError,match='P0_BYTES_CHANGED'):
        f.run()
    assert not f.posts

@pytest.mark.parametrize('status',['failure','cancelled'])
def test_known_failure_requires_receipt_and_cancelled_run_waits_for_successor(monkeypatch,status):
    f=Fixture(monkeypatch)
    f.runs[w.NATURAL]=[f.row(72,conclusion=status)]
    initial={**handoff(),'dispatched_run_id':72}
    if status=='cancelled':
        def tick():
            if f.minutes==2: f.runs[w.NATURAL].append(f.row(75,'queued',None))
            if f.minutes==4: f.target='complete'
        f.tick=tick
    result=f.run(initial)
    if status=='cancelled': assert result['status']=='FORMAL_TOP2_LEDGER_AND_PAGE_PRESENT'
    else: assert result['status']=='DELIVERY_STAGE_FAILED' and result['run_ids']==[72]
    assert not f.posts


@pytest.mark.parametrize('prior',['failure','success'])
def test_unbound_terminal_run_does_not_hide_exact_current_day_acceptance(monkeypatch,prior):
    f=Fixture(monkeypatch)
    f.target=w.OBSERVER
    f.job_conclusion='success'
    f.runs[w.OBSERVER]=[f.row(73,conclusion=prior)]
    f.tick=lambda: setattr(f,'target','complete') if f.minutes==5 else None
    assert f.run()['status']=='FORMAL_TOP2_LEDGER_AND_PAGE_PRESENT'
    assert not f.posts


@pytest.mark.parametrize('kind',['canonical','old_no_content','wrong_url','unknown'])
def test_actual_dispatch_captures_only_api_receipt_id_and_does_not_resend(kind):
    from tests.test_candidate_delivery_watchdog import Fake,NOW
    f=Fake()
    plan=w.inspect(f,NOW)
    request=f.request
    def respond(path,body=None):
        value=request(path,body)
        if body is None or kind=='old_no_content': return value
        if kind=='unknown': raise ValueError('DISPATCH_OUTCOME_UNKNOWN_NO_BLIND_RETRY')
        return {'workflow_run_id':72, 'run_url':f'https://api.github.com/repos/{w.REPO}/actions/runs/72',
                'html_url':f"https://github.com/{w.REPO}/actions/runs/{73 if kind=='wrong_url' else 72}"}
    f.request=respond
    outcome=w.act(f,plan,NOW,execute=True)
    assert len(f.posts)==1
    if kind in ('wrong_url','unknown'):
        assert outcome['status']=='DISPATCH_OUTCOME_UNKNOWN_NO_BLIND_RETRY'
    else: assert outcome['status']=='DISPATCHED_NOT_COMPLETED'
    assert outcome.get('dispatched_run_id')==(72 if kind=='canonical' else None)

@pytest.mark.parametrize('workflow,setup',[(w.OBSERVER,'freeze'),(w.PUBLISHER,'observe')])
def test_downstream_budget_excludes_proven_prior_signal_failures_and_counts_potential_startups(workflow,setup):
    from tests.test_candidate_delivery_watchdog import Fake,NOW,run,raw
    f=Fake();getattr(f,setup)()
    prior='d'*40
    f.revisions[prior]={'outputs/decision/three_rank_index.json':raw({'latest_signal_date':'20260921'})}
    f.busy=[run(workflow,id=100+i,head_sha=prior,conclusion='failure',
                created_at='2026-09-22T00:32:00Z') for i in range(4)]
    f.busy.extend(run(workflow,id=200+i,head_sha=prior,conclusion='startup_failure',
                      created_at='2026-09-22T12:32:00Z') for i in range(2))
    p=w.inspect(f,NOW)
    result=w.act(f,p,NOW)
    assert result['status']=='DRY_RUN_WOULD_DISPATCH'
    assert result['excluded_prior_day_run_ids']==[100,101,102,103]
    assert result['potential_failed_run_ids']==[200,201]
    f.busy.append(run(workflow,id=202,head_sha=prior,conclusion='failure',created_at='2026-09-22T07:00:00Z'))
    with pytest.raises(ValueError,match='RETRY_BUDGET'):
        w.act(f,p,NOW)


@pytest.mark.parametrize('marker',['missing','missing_date','current','receipt_present'])
def test_unproven_preclose_failures_remain_in_budget(marker):
    from tests.test_candidate_delivery_watchdog import Fake,NOW,run,raw
    f=Fake();f.observe();head='e'*40
    files={}
    if marker!='missing':
        files['outputs/decision/three_rank_index.json']=raw({} if marker=='missing_date' else
                {'latest_signal_date':'20260922' if marker=='current' else '20260921'})
    if marker=='receipt_present': files['outputs/decision/primary_d_receipt_20260922.json']=b'not absent'
    f.revisions[head]=files
    f.busy=[run(w.PUBLISHER,id=200+i,head_sha=head,conclusion='failure',
                created_at='2026-09-22T01:30:00Z') for i in range(3)]
    with pytest.raises(ValueError,match='RETRY_BUDGET'):
        w.act(f,w.inspect(f,NOW),NOW)


@pytest.mark.parametrize('source', [w.OBSERVER, w.PUBLISHER])
@pytest.mark.parametrize('status', ['DISPATCHED_NOT_COMPLETED','DISPATCH_OUTCOME_UNKNOWN_NO_BLIND_RETRY',
                                    'UPSTREAM_OR_RECOVERY_ALREADY_RUNNING'])
def test_watchdog_reuses_initial_observer_or_publisher_and_waits_for_public_evidence(monkeypatch,source,status):
    f = Fixture(monkeypatch)
    initial = {**handoff(status), 'workflow':source}
    initial.pop('retry_not_before')  # Only a freeze plan contains an original P0 timestamp.
    f.runs[source] = [f.row(72,'queued',None)]
    f.target = source
    def tick():
        if f.minutes == 2:
            f.runs[source] = []
            f.target = w.PUBLISHER
        if f.minutes == 6:
            f.target = 'complete'
    f.tick = tick
    assert f.run(initial)['status'] == 'FORMAL_TOP2_LEDGER_AND_PAGE_PRESENT'
    assert f.posts == ([w.PUBLISHER] if source == w.OBSERVER else [])


def test_second_resolution_dispatch_receipt_failure_is_visible(monkeypatch):
    f = Fixture(monkeypatch)
    initial = {**handoff(), 'dispatched_run_id':72}
    initial['queue']['checked_at'] = (START + timedelta(microseconds=150)).isoformat()
    f.runs[w.NATURAL] = [f.row(72,conclusion='failure')]
    assert f.run(initial)['status'] == 'DELIVERY_STAGE_FAILED'


def test_returned_unknown_post_is_never_sent_again(monkeypatch):
    f = Fixture(monkeypatch)
    f.target = w.OBSERVER
    def unknown(client, plan, now, *, execute, clock):
        f.posts.append(plan['workflow'])
        return {'status':'DISPATCH_OUTCOME_UNKNOWN_NO_BLIND_RETRY'}
    monkeypatch.setattr(w, 'act', unknown)
    assert f.run()['status'] == 'DELIVERY_FOLLOWUP_ENDED_WITHOUT_PUBLIC_ACCEPTANCE'
    assert f.posts == [w.OBSERVER]


@pytest.mark.parametrize('target', [w.OBSERVER, w.PUBLISHER])
def test_terminal_run_predating_actual_dated_upstream_does_not_block_handoff(target):
    from tests.test_candidate_delivery_watchdog import Fake, run, raw
    f = Fake()
    upstream = w.NATURAL if target == w.OBSERVER else w.OBSERVER
    def upstream_run(path):
        assert path == '/actions/runs/79'
        return run(upstream, id=79, created_at=START.isoformat())
    f.request = upstream_run
    if target == w.OBSERVER:
        plan = {'workflow':target, 'status':'NEEDS_OBSERVER','inputs':{'freeze_run_id':'79'}}
    else:
        plan = {'workflow':target,'status':'NEEDS_PUBLICATION','signal_date':DAY,'head':HEAD}
        f.files[w.ROOT+f'candidate_natural_evidence/{DAY}/context.json'] = raw({'signal_date':DAY,'observer_run_id':79})
    stale = {'status':'completed','updated_at':(START-timedelta(seconds=1)).isoformat()}
    ambiguous = {'status':'completed','updated_at':START.isoformat()}
    assert monitor.after_prerequisite(f,plan,[stale,ambiguous]) == [ambiguous]
