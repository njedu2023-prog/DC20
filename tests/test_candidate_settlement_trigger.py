"""Only a verified skipped observer suppresses collection; unknown is an error."""
import copy
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("settlement_trigger", ROOT / "scripts/candidate_settlement_trigger.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def fixtures():
    run = dict(id=101, workflow_id=m.OBSERVER_ID, path=m.OBSERVER_PATH, name=m.OBSERVER_NAME,
        head_sha="a" * 40, head_branch="main", run_attempt=1, event="workflow_run",
        status="completed", conclusion="success", repository={"full_name":m.REPO},
        head_repository={"full_name":m.REPO})
    base = dict(run_id=101, run_attempt=1, head_sha=run["head_sha"], head_branch="main",
        workflow_name=m.OBSERVER_NAME, status="completed")
    step = lambda name:dict(name=name,status="completed",conclusion="success")
    jobs = [dict(base,id=201,name=m.VALIDATE,conclusion="success",steps=[step(m.CONTRACTS),step(m.TRIGGER)]),
            dict(base,id=202,name=m.OBSERVE,conclusion="skipped",steps=[])]
    reg = dict(id=m.OBSERVER_ID,path=m.OBSERVER_PATH,name=m.OBSERVER_NAME,state="active")
    return run,jobs,reg


class Client:
    def __init__(self, run, jobs, reg):
        self.run,self.jobs,self.reg,self.calls = run,jobs,reg,[]
    def get(self,path):
        self.calls.append(path)
        if path == f"/actions/workflows/{m.OBSERVER_ID}":return copy.deepcopy(self.reg)
        if path == '/actions/runs/101':return copy.deepcopy(self.run)
        if path == '/actions/runs/101/attempts/1/jobs?per_page=100&page=1':
            return dict(total_count=len(self.jobs),jobs=copy.deepcopy(self.jobs))
        raise AssertionError("Unexpected read route")


class ObserverGateTests(unittest.TestCase):
    def setUp(self):
        self.run,self.jobs,self.reg = fixtures()
        self.event = {"workflow_run":copy.deepcopy(self.run)}
    def inspect(self):
        return m.inspect_observer(self.event,Client(self.run,self.jobs,self.reg))
    def test_verified_noop_skips_with_no_authority_or_market_calls(self):
        result=self.inspect()
        self.assertFalse(result['needs_settlement'])
        self.assertEqual(result['status'],'VERIFIED_NOOP_OBSERVER_NO_SETTLEMENT')
        self.assertFalse(result['new_evidence_admission_issued'])
        self.assertEqual(result['market_requests'],0)
        self.assertEqual(result['remote_writes'],0)
    def test_executed_observer_delegates_without_claiming_publication(self):
        self.jobs[1].update(conclusion='success',steps=[dict(name=m.WORK,status='completed',conclusion='success')])
        result=self.inspect()
        self.assertTrue(result['needs_settlement'])
        self.assertEqual(result['status'],'DELEGATE_TO_EXISTING_SETTLEMENT_VALIDATION')
        self.assertFalse(result['new_evidence_admission_issued'])
    def test_source_identity_changes_fail_closed(self):
        for key,value in [('id',True),('workflow_id',1),('path','other.yml'),('name','other'),
            ('head_sha','z'*40),('head_branch','fork'),('run_attempt',2),('event','push'),
            ('status','in_progress'),('conclusion','failure'),('repository',{'full_name':'other/repo'}),
            ('head_repository',{'full_name':'other/repo'})]:
            with self.subTest(key=key):
                event=copy.deepcopy(self.event);event['workflow_run'][key]=value
                with self.assertRaises(ValueError):m.inspect_observer(event,Client(self.run,self.jobs,self.reg))
    def test_valid_but_different_api_head_rejected(self):
        self.run['head_sha']='b'*40
        with self.assertRaisesRegex(ValueError,'EVENT_API_IDENTITY_CHANGED'):self.inspect()
    def test_workflow_registration_rejected(self):
        for key,value in [('id',2),('path','bad'),('state','disabled_manually'),('name','bad')]:
            with self.subTest(key=key):
                reg=copy.deepcopy(self.reg);reg[key]=value
                with self.assertRaises(ValueError):m.inspect_observer(self.event,Client(self.run,self.jobs,reg))
    def test_job_identity_changes_rejected(self):
        for key,value in [('run_id',True),('run_attempt',2),('head_sha','b'*40),
            ('head_branch','fork'),('workflow_name','wrong'),('status','queued')]:
            with self.subTest(key=key):
                jobs=copy.deepcopy(self.jobs);jobs[1][key]=value
                with self.assertRaises(ValueError):m.inspect_observer(self.event,Client(self.run,jobs,self.reg))
    def test_missing_extra_duplicate_or_wrong_named_job_rejected(self):
        variants=[self.jobs[:1],self.jobs+self.jobs[:1],self.jobs+[dict(self.jobs[0],id=203)],
                  [self.jobs[0],dict(self.jobs[1],name='unrelated')]]
        for jobs in variants:
            with self.subTest(jobs=jobs),self.assertRaises(ValueError):
                m.inspect_observer(self.event,Client(self.run,jobs,self.reg))
    def test_invalid_validate_or_trigger_steps_rejected(self):
        for variant in ['failure','no_steps','missing_trigger','failed_trigger','duplicate_trigger']:
            with self.subTest(variant=variant):
                jobs=copy.deepcopy(self.jobs)
                if variant=='failure':jobs[0]['conclusion']='failure'
                elif variant=='no_steps':jobs[0]['steps']=[]
                elif variant=='missing_trigger':jobs[0]['steps']=jobs[0]['steps'][:1]
                elif variant=='failed_trigger':jobs[0]['steps'][1]['conclusion']='skipped'
                else:jobs[0]['steps'].append(copy.deepcopy(jobs[0]['steps'][1]))
                with self.assertRaises(ValueError):m.inspect_observer(self.event,Client(self.run,jobs,self.reg))
    def test_skipped_observer_with_steps_rejected(self):
        self.jobs[1]['steps']=[dict(name=m.WORK,status='completed',conclusion='skipped')]
        with self.assertRaisesRegex(ValueError,'SKIPPED_OBSERVER_HAS_STEPS'):self.inspect()
    def test_success_without_real_work_step_rejected(self):
        self.jobs[1]['conclusion']='success'
        with self.assertRaisesRegex(ValueError,'REQUIRED_STEP_NOT_SUCCESSFUL'):self.inspect()
    def test_unknown_observer_conclusion_rejected(self):
        for value in [None,'cancelled','failure','neutral']:
            with self.subTest(value=value):
                self.jobs[1]['conclusion']=value
                with self.assertRaises(ValueError):self.inspect()
    def test_api_errors_propagate_without_noop(self):
        client=Client(self.run,self.jobs,self.reg)
        with mock.patch.object(client,'get',side_effect=ValueError('TRIGGER_API_READ_FAILED')):
            with self.assertRaisesRegex(ValueError,'API_READ_FAILED'):m.inspect_observer(self.event,client)
    def test_complete_pagination_is_required(self):
        rows=[dict(self.jobs[0],id=i+1000) for i in range(101)]
        client=mock.Mock();client.get.side_effect=[dict(total_count=101,jobs=rows[:100]),dict(total_count=101,jobs=rows[100:])]
        self.assertEqual(len(m.complete_jobs(client,self.run)),101)
        self.assertTrue(client.get.call_args.args[0].endswith('page=2'))
    def test_incomplete_or_mutated_pages_rejected(self):
        rows=[dict(self.jobs[0],id=i+1000) for i in range(100)]
        cases=[[dict(total_count=3,jobs=self.jobs)],
               [dict(total_count=101,jobs=rows),dict(total_count=102,jobs=[])],
               [dict(total_count=101,jobs=rows),dict(total_count=101,jobs=[rows[0]])],
               [dict(total_count=1001,jobs=rows)], [dict(total_count=0,jobs=[])]]
        for docs in cases:
            with self.subTest(docs=len(docs)):
                client=mock.Mock();client.get.side_effect=docs
                with self.assertRaises(ValueError):m.complete_jobs(client,self.run)
    def test_read_only_http_fixed_version_and_error_redaction(self):
        response=mock.MagicMock();response.__enter__.return_value=response
        response.status=200;response.read.return_value=b'{}'
        opener=mock.Mock();opener.open.return_value=response
        with mock.patch.object(m.urllib.request,'build_opener',return_value=opener) as build:
            m.GitHubReadOnly('secret').get('/actions/runs/101')
        req=opener.open.call_args.args[0]
        self.assertEqual(req.method,'GET');self.assertIsNone(req.data)
        self.assertEqual(req.get_header('X-github-api-version'),'2026-03-10')
        self.assertEqual(build.call_args.args[0].proxies,{})
        self.assertIsInstance(build.call_args.args[1],m.NoRedirect)
        for route in ['/git/ref/heads/main','/actions/runs/101/cancel','https://evil.invalid/']:
            with self.subTest(route=route),self.assertRaisesRegex(ValueError,'GET_PATH_REJECTED'):
                m.GitHubReadOnly('secret').get(route)
    def test_redirect_cannot_forward_authorization(self):
        request=m.urllib.request.Request('https://api.github.com/repos/'+m.REPO+'/actions/runs/101',
            headers={'Authorization':'Bearer secret'})
        with self.assertRaisesRegex(ValueError,'TRIGGER_REDIRECT_REJECTED'):
            m.NoRedirect().redirect_request(request,None,302,'redirect',{},'https://other.invalid/')
    def test_main_errors_never_write_false_or_leak_private_message(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'output';output.write_text('')
            out=io.StringIO()
            with mock.patch.dict(os.environ,{'GITHUB_TOKEN':'secret','GITHUB_OUTPUT':str(output)}),\
                 mock.patch.object(m,'verify_current',side_effect=RuntimeError('secret raw private body')),\
                 contextlib.redirect_stdout(out):
                self.assertEqual(m.main(),1)
            self.assertEqual(output.read_text(),'')
            self.assertNotIn('secret',out.getvalue());self.assertNotIn('private',out.getvalue())
            self.assertEqual(json.loads(out.getvalue())['status'],'FAILED_CLOSED')
    def test_current_run_binding_checks_api_context(self):
        env=dict(GITHUB_ACTIONS='true',GITHUB_REPOSITORY=m.REPO,GITHUB_REF='refs/heads/main',
            GITHUB_EVENT_NAME='workflow_run',GITHUB_RUN_ATTEMPT='1',GITHUB_RUN_ID='301',GITHUB_SHA='b'*40,
            GITHUB_WORKFLOW_REF=m.REPO+'/'+m.WORKFLOW_PATH+'@refs/heads/main')
        reg=dict(id=m.WORKFLOW_ID,path=m.WORKFLOW_PATH,name=m.WORKFLOW_NAME,state='active')
        run=dict(self.run,id=301,workflow_id=m.WORKFLOW_ID,path=m.WORKFLOW_PATH,name=m.WORKFLOW_NAME,
            head_sha='b'*40,status='in_progress',conclusion=None)
        client=mock.Mock();client.get.side_effect=[reg,run];m.verify_current(env,client)
        for key,value in [('head_sha','c'*40),('run_attempt',2),('event','workflow_dispatch'),('id',302)]:
            with self.subTest(key=key):
                changed=dict(run);changed[key]=value
                client=mock.Mock();client.get.side_effect=[reg,changed]
                with self.assertRaises(ValueError):m.verify_current(env,client)
        for key,value in [('GITHUB_EVENT_NAME','schedule'),('GITHUB_RUN_ATTEMPT','2'),('GITHUB_REPOSITORY','fork/repo')]:
            with self.subTest(key=key):
                changed=dict(env);changed[key]=value
                with self.assertRaises(ValueError):m.verify_current(changed,mock.Mock())


if __name__=='__main__':
    unittest.main()
