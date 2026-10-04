import sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lab, operations
from learning import store, run_tracking as runs, research_agent as agent

class RunTrackingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.c=lab.connect(self.root/'research.sqlite3'); store.schema(self.c)
        self.evidence=store.put(self.c,'research_diagnostic','synthetic',{'synthetic':True})
        self.task=agent.task(self.c,'TEST',[self.evidence],'Check synthetic evidence')
    def tearDown(self):
        self.c.close(); self.tmp.cleanup()
    def handoff(self, origin='ENGINEERING_ACCEPTANCE', trigger=None, rid=None):
        context=runs.begin(self.c,rid,origin,trigger)
        report={'state':'NEEDS_EVIDENCE','steps':{'learning':{'active_research':{'worker':{'due':True,'tasks':[{'id':self.task}],'signature':'test'}}}}}
        runs.maintenance_finished(self.c,context,report)
        return context['run_id']
    def test_scheduled_requires_actual_trigger_identifier(self):
        with self.assertRaisesRegex(ValueError,'TRIGGER_ID'):runs.begin(self.c,origin='SCHEDULED')
        self.assertEqual(runs.dashboard(self.c)['scheduled_linked_completed'],0)
    def test_generation_and_maintenance_are_not_research_completion(self):
        rid=self.handoff()
        self.assertEqual(runs.latest(self.c,rid)['phase'],'AWAITING_AGENT')
        self.assertFalse(runs.latest(self.c,rid)['research_completed'])
        with self.assertRaisesRegex(ValueError,'SESSION'):runs.complete_agent(self.c,rid,[self.task])
    def test_old_unlinked_disposition_cannot_complete_new_session(self):
        agent.event(self.c,self.task,'BLOCKED','old evidence',[self.evidence])
        rid=self.handoff(); runs.agent_start(self.c,rid,[self.task])
        with self.assertRaisesRegex(ValueError,'FRESH'):agent.finish_worker(self.c,'20261004',[self.task],rid)
    def test_start_dispose_finish_and_idempotent_retry(self):
        rid=self.handoff(); runs.agent_start(self.c,rid,[self.task])
        eid=agent.event(self.c,self.task,'BLOCKED','Need new dates; revisit after new data',[self.evidence],rid)
        self.assertEqual(eid,agent.event(self.c,self.task,'BLOCKED','Need new dates; revisit after new data',[self.evidence],rid))
        wid=agent.finish_worker(self.c,'20261004',[self.task],rid)
        self.assertEqual(wid,agent.finish_worker(self.c,'20261004',[self.task],rid))
        self.assertEqual(runs.dashboard(self.c)['engineering_completed'],1)
        self.assertEqual(runs.dashboard(self.c)['scheduled_linked_completed'],0)
        self.assertTrue(runs.begin(self.c,rid,'ENGINEERING_ACCEPTANCE')['reused'])
    def test_wrong_run_or_task_rejected(self):
        rid=self.handoff()
        with self.assertRaisesRegex(ValueError,'NOT_STARTED'):agent.event(self.c,self.task,'BLOCKED','x',[self.evidence],rid)
        with self.assertRaisesRegex(ValueError,'INVALID_AGENT_TASKS'):runs.agent_start(self.c,rid,[self.task,self.task])
        with self.assertRaisesRegex(ValueError,'MISMATCH'):runs.begin(self.c,rid,'SCHEDULED','not-the-original-trigger')
    def test_agent_resume_preserves_original_watermark(self):
        rid=self.handoff(); started=runs.agent_start(self.c,rid,[self.task])
        agent.event(self.c,self.task,'COMPLETED','Investigation performed; no alpha claim',[self.evidence],rid)
        with self.assertRaisesRegex(ValueError,'RESUME_REQUIRED'):runs.agent_start(self.c,rid,[self.task])
        resumed=runs.agent_start(self.c,rid,[self.task],True)
        self.assertEqual(resumed['id'],started['id'])
        self.assertEqual(runs.complete_agent(self.c,rid,[self.task])['phase'],'COMPLETED_AGENT')
    def test_failed_maintenance_resume_and_trigger_preserved(self):
        context=runs.begin(self.c,origin='SCHEDULED',trigger_id='test-fixture-not-live')
        runs.maintenance_finished(self.c,context,{'state':'FAILED','error':'network'})
        with self.assertRaisesRegex(ValueError,'RESUME_REQUIRED'):runs.begin(self.c,context['run_id'],'SCHEDULED','test-fixture-not-live')
        resumed=runs.begin(self.c,context['run_id'],'SCHEDULED','test-fixture-not-live',True)
        self.assertEqual(resumed['attempt'],2)
        self.assertEqual(resumed['trigger_id'],context['trigger_id'])
    def test_abrupt_maintenance_stop_records_interruption(self):
        context=runs.begin(self.c)
        runs.begin(self.c,context['run_id'],resume=True)
        self.assertEqual([x['phase'] for x in runs.events(self.c,context['run_id'])],['STARTED','INTERRUPTED','RESUMED'])
    def test_corrupt_disposition_evidence_blocks_finish(self):
        rid=self.handoff();runs.agent_start(self.c,rid,[self.task])
        agent.event(self.c,self.task,'COMPLETED','Read evidence',[self.evidence],rid)
        row=self.c.execute('select a.sha256 from learning_records l join artifacts a on a.id=l.artifact_id where l.id=?',(self.evidence,)).fetchone()
        (self.root/'blobs'/row[0]).write_text('corrupt')
        with self.assertRaises(ValueError):runs.complete_agent(self.c,rid,[self.task])
    def test_engineering_acceptance_does_not_consume_weekly_agent_budget(self):
        rid=self.handoff();runs.agent_start(self.c,rid,[self.task])
        agent.event(self.c,self.task,'COMPLETED','engineering only',[self.evidence],rid)
        agent.finish_worker(self.c,'20261004',[self.task],rid)
        agent.task(self.c,'REAL_NEW',[self.evidence],'Different research task')
        self.assertTrue(agent.worker_due(self.c,'20261004')['due'])
    def test_maintenance_replay_does_not_repeat_capture(self):
        with patch('result_capture.run',return_value={'jobs':[],'results':[]}) as capture:
            a=operations.run(self.c,self.root/'run1',run_id='idempotency')
            b=operations.run(self.c,self.root/'run2',run_id='idempotency')
        self.assertEqual(capture.call_count,1)
        self.assertEqual(b['state'],'ALREADY_RECORDED')
        self.assertEqual(a['tracking']['phase'],'COMPLETED_MAINTENANCE')
    def test_old_interrupted_session_survives_dashboard_history_limit(self):
        original=self.handoff('SCHEDULED','fixture-only')
        for i in range(22):
            ctx=runs.begin(self.c)
            runs.maintenance_finished(self.c,ctx,{'state':'WAITING','steps':{}})
        self.assertNotIn(original,[r['run_id'] for r in runs.dashboard(self.c)['runs']])
        self.assertEqual(agent.worker_due(self.c,'20261004')['resume_run_id'],original)

    def test_real_nightly_orchestration_handoff_and_finish_on_isolated_database(self):
        from learning import pipeline
        def provider(api,params,fields=''):
            if api!='trade_cal':raise AssertionError('Unexpected provider request: '+api)
            self.assertEqual(params['start_date'],params['end_date'])
            return {'fields':['exchange','cal_date','is_open'],'items':[['SSE',params['start_date'],0]]}
        with patch.object(pipeline.tushare_access,'query',side_effect=provider):
            result=operations.run(self.c,self.root/'integration',fetch=True,trigger_origin='ENGINEERING_ACCEPTANCE')
        self.assertNotEqual(result['state'],'FAILED',result.get('error'))
        self.assertEqual(result['steps']['learning']['quote_learning']['training']['state'],'BLOCKED_INSUFFICIENT_DATA')
        self.assertEqual(result['tracking']['phase'],'AWAITING_AGENT')
        run_id=result['run_context']['run_id'];task_ids=result['tracking']['task_ids'][:3]
        runs.agent_start(self.c,run_id,task_ids)
        for task_id in task_ids:
            task=store.raw(self.c,task_id)
            agent.event(self.c,task_id,'BLOCKED','Synthetic test: insufficient results, await new evidence',task['evidence_ids'],run_id)
        agent.finish_worker(self.c,'20261004',task_ids,run_id)
        self.assertEqual(runs.latest(self.c,run_id)['phase'],'COMPLETED_AGENT')
        self.assertEqual(self.c.execute('select count(*) from outcomes').fetchone()[0],0)

    def test_resuming_agent_does_not_create_second_handoff(self):
        original=self.handoff('SCHEDULED','fixture-only')
        context=runs.begin(self.c)
        report={'state':'NEEDS_EVIDENCE','steps':{'learning':{'active_research':{'worker':{'due':True,'resume_run_id':original}}}}}
        result=runs.maintenance_finished(self.c,context,report)
        self.assertEqual(result['phase'],'COMPLETED_MAINTENANCE')
        self.assertEqual(result['resume_run_id'],original)
        self.assertEqual(runs.latest(self.c,original)['phase'],'AWAITING_AGENT')

if __name__=='__main__':unittest.main()
