import sys,tempfile,unittest,copy
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import lab
from learning import store,research_agent as a

class ResearchAgentTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.c=lab.connect(Path(self.tmp.name)/'db.sqlite');store.schema(self.c)
 def tearDown(self):self.c.close();self.tmp.cleanup()
 def proposal(self):
  evidence=store.put(self.c,'research_diagnostic','test',{'synthetic':True})
  tid=a.task(self.c,'TEST',[evidence],'test question')
  return dict(task_id=tid,evidence_ids=[evidence],hypothesis='test',mechanism='test',falsification='no uplift',missing_policy='preserve',primary_metric='TOP2_NET_UPLIFT_WITH_TAIL_AND_COVERAGE',risk_limits='existing gates',basis='QUOTE_PROXY',features=['return_5','return_20'],recipes=[dict(name='derived_spread',op='difference',left='return_5',right='return_20')])
 def test_scan_idempotent_and_no_false_success(self):
  a.scan(self.c);n=len(store.get(self.c,'research_task'));a.scan(self.c)
  self.assertEqual(n,len(store.get(self.c,'research_task')));self.assertEqual(n,2)
  self.assertFalse(a.dashboard(self.c)['promotion'])
 def test_preregister_immutable_and_dedup(self):
  p=self.proposal();i=a.register(self.c,p);self.assertEqual(i,a.register(self.c,p))
  self.assertEqual(store.raw(self.c,i)['confirmation'],'ONLY_SNAPSHOTS_CAPTURED_AFTER_REGISTRATION')
 def test_budget_across_families(self):
  p=self.proposal();a.register(self.c,p);p['hypothesis']='second';a.register(self.c,p);p['hypothesis']='third'
  with self.assertRaisesRegex(ValueError,'BUDGET'):a.register(self.c,p)
 def test_forbid_official_score_and_arbitrary_code(self):
  p=self.proposal();p['features'].append('official_profit_score')
  with self.assertRaises(ValueError):a.register(self.c,p)
  p=self.proposal();p['recipes'][0]['op']='eval'
  with self.assertRaises(ValueError):a.register(self.c,p)
 def test_no_evidence_no_completion(self):
  p=self.proposal()
  with self.assertRaises(ValueError):a.event(self.c,p['task_id'],'COMPLETED','done',[])
  with self.assertRaises(ValueError):a.finish_worker(self.c,'20260927',[p['task_id']])
 def test_worker_cadence_and_new_evidence(self):
  p=self.proposal();self.assertTrue(a.worker_due(self.c,'20260927')['due'])
  a.event(self.c,p['task_id'],'BLOCKED','needs outcomes',p['evidence_ids']);a.finish_worker(self.c,'20260927',[p['task_id']])
  self.assertFalse(a.worker_due(self.c,'20261005')['due'])
  a.task(self.c,'NEXT',p['evidence_ids'],'new');self.assertFalse(a.worker_due(self.c,'20260928')['due']);self.assertTrue(a.worker_due(self.c,'20261004')['due'])
 def test_empty_trial_blocked_no_model(self):
  i=a.register(self.c,self.proposal());r=a.evaluate(self.c,i)
  self.assertEqual(r['state'],'BLOCKED_INSUFFICIENT_DATA');self.assertFalse(r['promotion']);self.assertEqual(store.get(self.c,'model'),[])
  self.assertEqual(r['id'],a.evaluate(self.c,i)['id'])
 def test_old_outcomes_excluded(self):
  sid=store.put(self.c,'snapshot','old',{'captured_at':'2020-01-01T00:00:00+00:00'})
  i=a.register(self.c,self.proposal());data=dict(rows=[dict(snapshot_id=sid)],excluded=[],basis='QUOTE_PROXY',fingerprint='old')
  with patch.object(a.store,'dataset',return_value=data), patch.object(a.experiments,'attach',return_value=data):
   r=a.evaluate(self.c,i)
  self.assertEqual(r['result']['eligible_rows'],0)
 def test_all_completed_finish(self):
  p=self.proposal();a.event(self.c,p['task_id'],'COMPLETED','examined',p['evidence_ids'])
  a.finish_worker(self.c,'20260927',[p['task_id']]);self.assertFalse(a.worker_due(self.c,'20261010')['due'])

 def test_baseline_training_weekly_budget(self):
  data=dict(rows=[],fingerprint='empty',excluded=[],basis='QUOTE_PROXY')
  with patch.object(a.store,'dataset',return_value=data), patch.object(a.trainer,'train',return_value={'id':1,'state':'BLOCKED_INSUFFICIENT_DATA'}) as train, patch.object(a.experiments,'run',return_value={'id':2}):
   a.scheduled_training(self.c,'20260927');a.scheduled_training(self.c,'20260928')
   self.assertEqual(train.call_count,2)
 def test_candidate_code_change_rejected(self):
  with self.assertRaisesRegex(ValueError,'IMPLEMENTATION_CHANGED'):
   a.shadow_rows(self.c,{'code_sha':'old'}, {})
 def test_partial_worker_does_not_lose_other_task(self):
  p=self.proposal();a.task(self.c,'SECOND',p['evidence_ids'],'other')
  a.event(self.c,p['task_id'],'BLOCKED','wait',p['evidence_ids']);a.finish_worker(self.c,'20260927',[p['task_id']])
  self.assertTrue(a.worker_due(self.c,'20261004')['due'])

if __name__=='__main__':unittest.main()
