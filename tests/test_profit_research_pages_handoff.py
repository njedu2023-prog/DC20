"""Real child identity and fail-closed Pages handoff; no network or writes."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import research_pages_handoff as m

HEAD, SOURCE, PRIOR = 'a'*40, 'b'*40, 'c'*40


def run(path=m.CHILD_PATH, name=m.CHILD_NAME, run_id=22, head=HEAD,
        event='workflow_dispatch', status='completed', conclusion='success'):
    return dict(path=path, name=name, id=run_id, head_sha=head, event=event,
                head_branch='main', run_attempt=1, status=status, conclusion=conclusion,
                workflow_id=99, repository={'full_name':m.REPO},
                head_repository={'full_name':m.REPO}, display_title=m.marker(HEAD,11),
                html_url='https://github.com/'+m.REPO+'/actions/runs/'+str(run_id))


class Clock:
    def __init__(self): self.value=0; self.sleeps=[]
    def now(self): return self.value
    def sleep(self, seconds): self.sleeps.append(seconds); self.value+=seconds


class API:
    def __init__(self):
        self.calls=[]; self.created=False; self.unknown=False; self.never=False
        self.head=HEAD; self.child=run()
        self.parent=run(path='.github/workflows/publish_candidate_profit.yml',
            name=m.PARENTS['.github/workflows/publish_candidate_profit.yml'],
            run_id=11,head=PRIOR,event='workflow_run',conclusion='failure')
        self.jobs=[dict(id=1,run_id=11,name='Update and accept the profit research dashboard / accumulate',
            status='completed',conclusion='success',
            steps=[dict(name='Write only research evidence through GitHub API',conclusion='success')])]
        self.statuses=[{'status':'succeed'}]
    def request(self,path,body=None):
        self.calls.append((path,copy.deepcopy(body)))
        if body is not None:
            self.created=True
            if self.unknown: raise ValueError('DISPATCH_OUTCOME_UNKNOWN')
            return None
        if path=='/actions/runs/11': return copy.deepcopy(self.parent)
        if path.startswith('/actions/runs/11/jobs?'): return {'total_count':len(self.jobs),'jobs':copy.deepcopy(self.jobs)}
        if path=='/actions/workflows/'+m.CHILD:
            return dict(id=99,state='active',path=m.CHILD_PATH,name=m.CHILD_NAME)
        if path=='/actions/workflows/publish_candidate_profit.yml':
            return dict(id=99,state='active',path=self.parent['path'],name=self.parent['name'])
        if path.startswith('/actions/workflows/'+m.CHILD+'/runs?'):
            items=[copy.deepcopy(self.child)] if self.created and not self.never else []
            return {'total_count':len(items),'workflow_runs':items}
        if path=='/actions/runs/22': return copy.deepcopy(self.child)
        if path=='/git/ref/heads/main': return {'ref':'refs/heads/main','object':{'sha':self.head}}
        if path.startswith('/pages/deployments/'):
            if len(self.statuses)>1:return self.statuses.pop(0)
            return self.statuses[0]
        raise AssertionError(path)


class HandoffTests(unittest.TestCase):
    def dispatch(self,api,**kwargs):
        clock=Clock()
        return m.dispatch_and_wait(api,HEAD,'20261109',SOURCE,11,PRIOR,SOURCE,
            monotonic=clock.now,sleep=clock.sleep,timeout=120,**kwargs)

    def test_one_real_child_after_exact_main_and_existing_research_cas(self):
        api=API();result=self.dispatch(api)
        self.assertEqual(result['id'],22)
        posts=[c for c in api.calls if c[1] is not None]
        self.assertEqual(len(posts),1)
        self.assertEqual(posts[0][0],'/actions/workflows/'+m.CHILD+'/dispatches')
        self.assertEqual(posts[0][1],{'ref':'main','inputs':dict(expected_head=HEAD,
            signal_date='20261109',source_revision=SOURCE,source_run_id='11',
            wait_for_deployment=PRIOR,wait_expected_head=SOURCE)})
        self.assertTrue(all('/cancel' not in path for path,_ in api.calls))

    def test_aggregate_failure_does_not_discard_successful_research_cas(self):
        self.assertEqual(m.source_run(API(),11)['conclusion'],'failure')

    def test_failed_research_cas_prevents_dispatch(self):
        api=API();api.jobs[0]['steps'][0]['conclusion']='failure'
        with self.assertRaisesRegex(ValueError,'CAS_NOT_SUCCESSFUL'):self.dispatch(api)
        self.assertFalse(api.created)

    def test_source_workflow_registration_and_job_run_id_are_exact(self):
        api=API();api.parent['workflow_id']=100
        with self.assertRaisesRegex(ValueError,'SOURCE_REGISTRATION_MISMATCH'):
            self.dispatch(api)
        self.assertFalse(api.created)
        api=API();api.jobs[0]['run_id']=99
        with self.assertRaisesRegex(ValueError,'CAS_NOT_SUCCESSFUL'):
            self.dispatch(api)
        self.assertFalse(api.created)

    def test_existing_matching_child_is_reused_without_post(self):
        api=API();api.created=True;self.dispatch(api)
        self.assertFalse(any(body is not None for _,body in api.calls))

    def test_real_api_dynamic_run_name_and_canonical_name_are_both_strictly_bound(self):
        for name in (m.CHILD_NAME,m.marker(HEAD,11)):
            api=API();api.child['name']=name
            self.assertEqual(self.dispatch(api)['id'],22)
            api.child['status']='in_progress'
            with patch.object(m,'capsule'),patch.object(m,'source_cas',return_value=HEAD):
                self.assertEqual(m.guard(api,self.env(),Path('.'))['status'],'NO_PRIOR_DEPLOYMENT')
        for change in ({'name':'unrelated'},{'display_title':m.marker(HEAD,12)}):
            api=API();api.child.update(change);api.child['status']='in_progress'
            with patch.object(m,'capsule'),self.assertRaises(ValueError):
                m.guard(api,self.env(),Path('.'))

    def test_ambiguous_child_never_dispatches(self):
        api=API();original=api.request
        def request(path,body=None):
            if '/runs?head_sha=' in path:
                other=copy.deepcopy(api.child);other['id']=23
                return {'total_count':2,'workflow_runs':[api.child,other]}
            return original(path,body)
        api.request=request
        with self.assertRaisesRegex(ValueError,'AMBIGUOUS'):self.dispatch(api)
        self.assertFalse(api.created)

    def test_main_moved_before_post_is_not_dispatched(self):
        api=API();api.head='f'*40
        with self.assertRaisesRegex(ValueError,'MAIN_MOVED'):self.dispatch(api)
        self.assertFalse(api.created)

    def test_wrong_real_child_head_is_never_accepted(self):
        api=API();api.child['head_sha']='f'*40
        with self.assertRaisesRegex(ValueError,'RUN_HEAD_MISMATCH'):self.dispatch(api)
        self.assertEqual(len([1 for _,b in api.calls if b is not None]),1)

    def test_unknown_post_outcome_discovers_child_without_resend(self):
        api=API();api.unknown=True
        self.assertEqual(self.dispatch(api)['id'],22)
        self.assertEqual(len([1 for _,b in api.calls if b is not None]),1)

    def test_unknown_post_outcome_times_out_without_resend(self):
        api=API();api.unknown=True;api.never=True
        with self.assertRaisesRegex(ValueError,'WAIT_TIMEOUT'):self.dispatch(api)
        self.assertEqual(len([1 for _,b in api.calls if b is not None]),1)

    def test_failed_cancelled_child_is_explicit_not_automatically_retried(self):
        for outcome in ('failure','cancelled','timed_out'):
            with self.subTest(outcome=outcome):
                api=API();api.created=True;api.child['conclusion']=outcome
                with self.assertRaisesRegex(ValueError,'CHILD_'+outcome.upper()):self.dispatch(api)
                self.assertFalse(any(body is not None for _,body in api.calls))

    def test_in_progress_child_waits_in_sixty_second_intervals(self):
        api=API();api.created=True;api.child['status']='in_progress';api.child['conclusion']=None
        clock=Clock()
        def sleep(seconds):
            clock.sleep(seconds);api.child.update(status='completed',conclusion='success')
        m.dispatch_and_wait(api,HEAD,'20261109',SOURCE,11,monotonic=clock.now,sleep=sleep)
        self.assertEqual(clock.sleeps,[60])
        self.assertFalse(any(body is not None for _,body in api.calls))

    def test_paginated_results_require_complete_unique_stable_inventory(self):
        api=API();calls=[]
        def request(path,body=None):
            calls.append(path)
            page=1 if path.endswith('page=1') else 2
            return {'total_count':101,'jobs':[{'id':n} for n in (range(100) if page==1 else [100])]}
        api.request=request
        self.assertEqual(len(m.all_items(api,'/jobs','jobs')),101)
        self.assertEqual(len(calls),2)
        for value in ({'total_count':2,'jobs':[{'id':1}]},
                      {'total_count':2,'jobs':[{'id':1},{'id':1}]},
                      {'jobs':[]}):
            api.request=lambda *args,v=value,**kw:v
            with self.assertRaises(ValueError):m.all_items(api,'/jobs','jobs')

    def test_wait_old_succeed_requires_exact_latest_public_head(self):
        api=API();clock=Clock();observed=iter(['d'*40,SOURCE])
        result=m.wait_backend(api,PRIOR,SOURCE,read_public=lambda:next(observed),
            monotonic=clock.now,sleep=clock.sleep,timeout=120)
        self.assertEqual(result['status'],'PRIOR_BACKEND_AND_PUBLIC_CONFIRMED')
        self.assertEqual(clock.sleeps,[60])
        self.assertFalse(any(body is not None for _,body in api.calls))

    def test_wait_backend_uses_official_active_states(self):
        for state in m.ACTIVE:
            with self.subTest(state=state):
                api=API();api.statuses=[{'status':state},{'status':'succeed'}];clock=Clock()
                m.wait_backend(api,PRIOR,SOURCE,read_public=lambda:SOURCE,
                    monotonic=clock.now,sleep=clock.sleep)
                self.assertEqual(clock.sleeps,[60])

    def test_explicit_backend_failure_allows_fresh_identity_without_cancel(self):
        for state in m.FAILED:
            api=API();api.statuses=[{'status':state}]
            result=m.wait_backend(api,PRIOR,SOURCE,read_public=lambda:self.fail('No old public required'))
            self.assertEqual(result['status'],'PRIOR_BACKEND_TERMINAL_FAILURE')
            self.assertFalse(any(body is not None for _,body in api.calls))

    def test_unknown_missing_api_error_never_clear(self):
        for value in ({},{'status':'deployed'},{'status':'new_unknown'},None):
            api=API();api.statuses=[value]
            with self.assertRaises(ValueError):m.wait_backend(api,PRIOR,SOURCE)
        api=API();api.request=lambda path:(_ for _ in ()).throw(ValueError('GITHUB_HTTP_404'))
        with self.assertRaisesRegex(ValueError,'GITHUB_HTTP_404'):m.wait_backend(api,PRIOR,SOURCE)

    def test_old_public_never_converges_times_out_without_any_post(self):
        api=API();clock=Clock()
        with self.assertRaisesRegex(ValueError,'BACKEND_WAIT_TIMEOUT'):
            m.wait_backend(api,PRIOR,SOURCE,read_public=lambda:'d'*40,
                monotonic=clock.now,sleep=clock.sleep,timeout=120)
        self.assertEqual(clock.sleeps,[60,60])
        self.assertFalse(any(body is not None for _,body in api.calls))

    def test_no_prior_identity_is_not_invented_for_standalone_research(self):
        self.assertEqual(m.wait_backend(API(),'','')['status'],'NO_PRIOR_DEPLOYMENT')
        with self.assertRaises(ValueError):m.wait_backend(API(),'',SOURCE)

    def fixture(self,root):
        data={'source_revision':SOURCE,'as_of_date':'20261109','days':[{'date':'20261109'}]}
        raw=json.dumps(data).encode();digest=hashlib.sha256(raw).hexdigest()
        pointer=dict(source_revision=SOURCE,as_of_date='20261109',
                     path='versions/'+digest+'.json',sha256=digest)
        paths={m.delivery.PREFIX+'latest.json':json.dumps(pointer).encode(),
               m.delivery.PREFIX+pointer['path']:raw,
               'outputs/decision/candidate_profit_v1/index.json':b'{"latest_signal_date":"20261109"}',
               'outputs/decision/primary_d_runtime_index.json':b'{"latest_signal_date":"20261109"}'}
        for path,value in paths.items():
            target=root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(value)
        return paths,pointer

    def test_existing_capsule_is_readonly_exact_day_source_hash_and_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);paths,pointer=self.fixture(root)
            with patch.object(m.subprocess,'check_output',return_value=HEAD+'\n'):
                self.assertEqual(m.capsule(root,HEAD,'20261109',SOURCE),pointer)
                for head,day,source in [('f'*40,'20261109',SOURCE),
                                        (HEAD,'20261106',SOURCE),(HEAD,'20261109','f'*40)]:
                    with self.assertRaises(ValueError):m.capsule(root,head,day,source)
                target=root/m.delivery.PREFIX/pointer['path'];target.write_bytes(target.read_bytes()+b' ')
                with self.assertRaisesRegex(ValueError,'FINGERPRINT'):
                    m.capsule(root,HEAD,'20261109',SOURCE)
            for path,raw in paths.items():
                if path!=m.delivery.PREFIX+pointer['path']:
                    self.assertEqual((root/path).read_bytes(),raw)

    def test_duplicate_public_noop_requires_head_pointer_and_version_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);paths,pointer=self.fixture(root)
            public=dict(paths)
            def get(url):return public[url.removeprefix('https://njedu2023-prog.github.io/DC20/')]
            with patch.object(m,'public_head',return_value=HEAD),patch.object(m.delivery,'get',get):
                self.assertTrue(m.already_public(root,HEAD))
                public[m.delivery.PREFIX+pointer['path']]+=b' '
                self.assertFalse(m.already_public(root,HEAD))
            with patch.object(m,'public_head',return_value='f'*40):
                self.assertFalse(m.already_public(root,HEAD))

    def env(self):
        return dict(GITHUB_ACTIONS='true',GITHUB_REPOSITORY=m.REPO,GITHUB_REF='refs/heads/main',
            GITHUB_RUN_ATTEMPT='1',GITHUB_EVENT_NAME='workflow_dispatch',
            GITHUB_WORKFLOW_REF=m.REPO+'/'+m.CHILD_PATH+'@refs/heads/main',
            GITHUB_SHA=HEAD,GITHUB_RUN_ID='22',EXPECTED_RESEARCH_HEAD=HEAD,
            EXPECTED_RESEARCH_SOURCE=SOURCE,EXPECTED_RESEARCH_D='20261109',SOURCE_RUN_ID='11')

    def test_child_guard_requires_genuine_actual_head_before_deployment(self):
        api=API();api.child['status']='in_progress'
        with patch.object(m,'capsule') as capsule,patch.object(m,'source_cas',return_value=HEAD):
            self.assertEqual(m.guard(api,self.env(),Path('.'))['status'],'NO_PRIOR_DEPLOYMENT')
            self.assertEqual(capsule.call_count,2)
        for key,value in [('GITHUB_SHA','f'*40),('GITHUB_REF','refs/heads/fork'),
                          ('GITHUB_RUN_ATTEMPT','2'),('GITHUB_EVENT_NAME','workflow_run')]:
            env=self.env();env[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                m.guard(api,env,Path('.'))

    def test_main_advances_during_backend_wait_prevents_deploy(self):
        api=API();api.child['status']='in_progress'
        def wait(*args):api.head='f'*40;return {}
        with patch.object(m,'capsule'),patch.object(m,'source_cas',return_value=HEAD),patch.object(m,'wait_backend',wait):
            with self.assertRaisesRegex(ValueError,'MAIN_MOVED_DURING'):
                m.guard(api,self.env(),Path('.'))

    def test_readonly_backend_and_dispatch_client_allowlist(self):
        client=m.Client('not-a-real-token')
        with self.assertRaisesRegex(ValueError,'DISPATCH_TARGET_REJECTED'):
            client.request('/pages/deployments/'+PRIOR+'/cancel',{})
        with self.assertRaisesRegex(ValueError,'DISPATCH_TARGET_REJECTED'):
            client.request('/git/refs/heads/main',{'sha':HEAD})
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):return None
            def read(self,*args):return b'{"status":"succeed"}'
        seen=[]
        def opened(request,timeout):seen.append(request);return Response()
        with patch.object(m.urllib.request,'urlopen',opened):
            client.request('/pages/deployments/'+PRIOR)
            client.request('/contents/outputs/decision/profit_research/latest.json?ref='+HEAD)
        self.assertEqual(seen[0].get_header('X-github-api-version'),'2026-03-10')
        self.assertEqual(seen[0].get_method(),'GET')
        self.assertEqual(seen[0].get_header('Accept'),'application/vnd.github+json')
        self.assertEqual(seen[1].get_header('Accept'),'application/vnd.github.object+json')

    def test_workflow_separates_real_run_and_retains_existing_acceptance(self):
        root=Path(__file__).resolve().parents[1]
        child=(root/'.github/workflows/profit_research_pages.yml').read_text()
        parent=(root/'.github/workflows/profit_research.yml').read_text()
        publisher=(root/'.github/workflows/publish_candidate_profit.yml').read_text()
        self.assertNotIn('  workflow_run:',child)
        self.assertNotIn('  schedule:',child)
        self.assertNotIn('GITHUB_SHA:',child+parent+publisher)
        self.assertIn('needs: guard',child)
        self.assertIn('uses: ./.github/workflows/deploy_dc20_pages.yml',child)
        self.assertIn('expected_head: ${{ inputs.expected_head }}',child)
        self.assertIn('profit_research_delivery.py verify',child)
        self.assertIn('profit_research_delivery.py verify',parent)
        self.assertIn('needs: [accumulate, deploy]',parent)
        self.assertNotIn('uses: ./.github/workflows/deploy_dc20_pages.yml',parent)
        self.assertIn('python -m scripts.research_pages_handoff dispatch',parent)
        self.assertIn('actions: write',publisher.split('  research:',1)[1])
        self.assertIn('needs: [publish, deploy]',publisher.split('  research:',1)[1])
        self.assertIn('prior_public_head:',publisher)
        self.assertIn("if: github.event_name == 'push'",parent)
        self.assertIn("if: github.event_name != 'push' &&",parent)
        self.assertIn("-p 'test_profit_research*.py'",parent)
        self.assertIn('tests/test_profit_research_pages_handoff.py',parent)
        self.assertIn('tests/test_profit_research_source_cas.py',parent)
