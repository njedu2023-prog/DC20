"""Quote hypotheses and interrupted research use isolated synthetic evidence stores."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lab
from learning import features, quote_learning as ql, research_agent as agent, run_tracking, store


class QuoteResearchAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.c = lab.connect(self.root / 'db.sqlite')
        self.clock = patch.object(lab, 'now', return_value='2026-01-05T20:00:00+08:00')
        self.clock.start()
        store.schema(self.c)

    def tearDown(self):
        self.clock.stop()
        self.c.close()
        self.tmp.cleanup()

    def proposal(self):
        eid = store.put(self.c, 'research_diagnostic', 'synthetic', {'synthetic': True})
        tid = agent.task(self.c, 'QUOTE_TEST', [eid], 'Synthetic-only quote hypothesis')
        return dict(task_id=tid, evidence_ids=[eid], basis='QUOTE_RETURN', hypothesis='synthetic',
                    mechanism='short versus medium return', falsification='no forward top-two uplift',
                    missing_policy='preserve unknown', primary_metric='TOP2_NET_UPLIFT_WITH_TAIL_AND_COVERAGE',
                    risk_limits='original cost and tail gates', features=['return_5', 'return_20'],
                    recipes=[dict(name='derived_spread', op='difference', left='return_5', right='return_20')])

    def snapshot(self, key, captured, recorded=None):
        with patch.object(lab, 'now', return_value=recorded or captured):
            return store.put(self.c, 'snapshot', key, {'captured_at': captured})

    def row(self, day, code, sid):
        return dict(day=day, code=code, snapshot_id=sid, label_id=9000, prediction_id=1,
                    status='VERIFIED_PRICE_PAIR', cutoff='2026-01-08T22:00:00+08:00',
                    entry_at='2026-01-09T09:25:00+08:00', exit_at='2026-01-12T10:00:00+08:00',
                    label_available_at='2026-01-12T22:00:00+08:00', gross=.02,
                    official_rank=1, x={k: .1 if k=='return_5' else .02 for k in features.FEATURES})

    def data(self, rows):
        return dict(method=ql.METHOD, basis=ql.BASIS, fingerprint='SYNTHETIC_TEST_ONLY', rows=rows,
                    excluded=[], summary=dict(eligible_rows=len(rows), eligible_days=len({r['day'] for r in rows})))

    def test_quote_preregistration_preserves_policy_and_rejects_unproven_enrichment(self):
        proposal = self.proposal()
        pid = agent.register(self.c, proposal)
        saved = store.raw(self.c, pid)
        self.assertEqual(saved['evaluation_policy'], ql.policy())
        self.assertFalse(saved['production_promotion'])
        self.assertEqual(saved['evaluation_policy']['min_rows'], 300)
        self.assertEqual(pid, agent.register(self.c, proposal))
        proposal['features'].append('flow_net_cny_5')
        with self.assertRaisesRegex(ValueError, 'INVALID_FEATURE_SET'):
            agent.register(self.c, proposal)

    def test_confirmation_excludes_entire_day_with_one_old_snapshot(self):
        pid = agent.register(self.c, self.proposal())
        old = self.snapshot('old', '2026-01-04T20:00:00+08:00')
        new = self.snapshot('new', '2026-01-08T20:00:00+08:00')
        data = self.data([self.row('20260108', 'A', old), self.row('20260108', 'B', new),
                          self.row('20260109', 'A', new), self.row('20260109', 'B', new)])
        with patch.object(ql, 'dataset', return_value=data), patch.object(agent.experiments, 'attach', side_effect=AssertionError('Quote branch must not attach enrichment')):
            trial = agent.evaluate(self.c, pid)
        self.assertEqual(trial['days'], ['20260109'])
        self.assertEqual(trial['result']['eligible_rows'], 2)
        self.assertIn('PRE_REGISTRATION_COHORT_NOT_CONFIRMATION', trial['result']['excluded'][0]['reason'])
        self.assertEqual(data['rows'][0]['x']['return_5'], .1)
        self.assertNotIn('derived_spread', data['rows'][2]['x'])
        self.assertEqual(store.get(self.c, 'model'), [])

    def test_captured_but_not_recorded_after_registration_is_not_confirmation(self):
        pid = agent.register(self.c, self.proposal())
        sid = self.snapshot('bad-time', '2026-01-08T20:00:00+08:00', '2026-01-04T20:00:00+08:00')
        with patch.object(ql, 'dataset', return_value=self.data([self.row('20260108', 'A', sid)])):
            trial = agent.evaluate(self.c, pid)
        self.assertEqual(trial['result']['eligible_rows'], 0)
        self.assertFalse(trial['promotion'])

    def test_trained_quote_proposal_saves_only_independent_quote_model(self):
        pid = agent.register(self.c, self.proposal())
        sid = self.snapshot('fresh', '2026-01-08T20:00:00+08:00')
        data = self.data([self.row('20260108', 'A', sid)])
        result = dict(state='TRAINED_RESEARCH_ONLY', basis='QUOTE_RETURN', bundle={}, test_days=['20260108'])
        with patch.object(ql, 'dataset', return_value=data), patch.object(agent.trainer, 'fit_dataset', return_value=result) as fit:
            trial = agent.evaluate(self.c, pid)
            retry = agent.evaluate(self.c, pid)
        self.assertEqual(trial['id'], retry['id'])
        self.assertEqual(fit.call_count, 1)
        passed_data, passed_policy = fit.call_args.args
        self.assertAlmostEqual(passed_data['rows'][0]['x']['derived_spread'], .08)
        self.assertEqual(passed_policy['feature_names'], ['return_5', 'return_20', 'derived_spread'])
        self.assertTrue(passed_policy['strict_cutoff'])
        model = store.raw(self.c, trial['model_id'])
        self.assertEqual(model['method'], 'quote_return_v1')
        self.assertEqual(model['model_kind'], 'quote_model')
        self.assertFalse(model['calibrates_original_probability'])
        self.assertFalse(model['production_promotion'])
        self.assertEqual(model['promotion'], 'DISABLED_QUOTE_RESEARCH_ONLY')
        self.assertEqual(len(store.get(self.c, 'quote_model')), 1)
        self.assertEqual(store.get(self.c, 'model'), [])

    def test_quote_recipe_shadow_uses_numeric_evidence_only_and_code_hash(self):
        pid = agent.register(self.c, self.proposal())
        model = dict(research_proposal_id=pid, basis='QUOTE_RETURN', method='quote_return_v1', code_sha=agent.implementation_sha())
        snapshot = dict(day='20260108', captured_at='2026-01-08T20:00:00+08:00',
                        rows=[dict(code='A', features={k: .1 if k=='return_5' else .02 for k in features.FEATURES})])
        with patch.object(agent.experiments, 'attach', side_effect=AssertionError('Enrichment forbidden')):
            rows = agent.shadow_rows(self.c, model, snapshot)
        self.assertAlmostEqual(rows[0]['x']['derived_spread'], .08)
        self.assertEqual(set(rows[0]['x']), set(features.FEATURES) | {'derived_spread'})
        model['code_sha'] = 'OLD_CODE'
        with self.assertRaisesRegex(ValueError, 'IMPLEMENTATION_CHANGED'):
            agent.shadow_rows(self.c, model, snapshot)
        model['code_sha'] = agent.implementation_sha()
        snapshot['captured_at'] = '2026-01-04T20:00:00+08:00'
        with self.assertRaisesRegex(ValueError, 'SNAPSHOT_NOT_FRESH'):
            agent.shadow_rows(self.c, model, snapshot)

    def test_quote_recipe_preserves_missing_values_and_rejects_overflow(self):
        p = self.proposal()
        self.assertIsNone(agent.apply_recipes({'return_5': None, 'return_20': .1}, p)['derived_spread'])
        p['recipes'][0]['op'] = 'product'
        with self.assertRaisesRegex(ValueError, 'NONFINITE_DERIVED_FEATURE'):
            agent.apply_recipes({'return_5': 1e308, 'return_20': 1e308}, p)

    def test_quote_scan_only_reacts_to_changed_saved_dataset_fingerprint(self):
        data = self.data([])
        data['fingerprint'] = 'first'
        store.put(self.c, 'quote_dataset', 'first', data)
        agent.scan(self.c)
        quote_tasks = lambda: [t for t in store.get(self.c, 'research_task') if t['topic'].endswith('_QUOTE_RETURN')]
        self.assertEqual(len(quote_tasks()), 1)
        self.assertEqual(quote_tasks()[0]['topic'], 'LABEL_READINESS_QUOTE_RETURN')
        # A repeated snapshot of the same dataset is not fresh scientific evidence.
        store.put(self.c, 'quote_dataset', 'same-fingerprint', dict(data, report_metadata='same data'))
        agent.scan(self.c)
        self.assertEqual(len(quote_tasks()), 1)
        changed = copy.deepcopy(data)
        changed['fingerprint'] = 'second'
        changed['excluded'] = [{'day': '20260108', 'reason': 'NEW_MISSING_EVIDENCE'}]
        store.put(self.c, 'quote_dataset', 'second', changed)
        agent.scan(self.c)
        self.assertEqual(len(quote_tasks()), 2)
        self.assertEqual(len([d for d in store.get(self.c, 'research_diagnostic') if d['basis']=='QUOTE_RETURN']), 2)

    def handoff(self, rid, tasks, origin='SCHEDULED'):
        context = run_tracking.begin(self.c, rid, origin, 'synthetic-trigger' if origin=='SCHEDULED' else None)
        report = dict(state='COMPLETED', steps={'learning': {'active_research': {'worker': {'due': True, 'tasks': tasks}}}})
        run_tracking.maintenance_finished(self.c, context, report)
        return context

    def test_unfinished_agent_resumes_original_run_before_weekly_gate(self):
        p = self.proposal()
        second = agent.task(self.c, 'SECOND', p['evidence_ids'], 'second investigation')
        tasks = [t for t in store.get(self.c, 'research_task') if t['id'] in (p['task_id'], second)]
        self.handoff('original-trigger-run', tasks)
        run_tracking.agent_start(self.c, 'original-trigger-run', [t['id'] for t in tasks])
        agent.event(self.c, p['task_id'], 'COMPLETED', 'first task done', p['evidence_ids'], 'original-trigger-run')
        # An unrelated completed worker today must not suppress this recovery.
        store.put(self.c, 'research_worker', 'other', dict(day='20260105', task_ids=[t['id'] for t in tasks], origin='SCHEDULED'))
        count = len(store.get(self.c, 'research_run_event'))
        due = agent.worker_due(self.c, '20260105')
        self.assertTrue(due['due'])
        self.assertEqual(due['reason'], 'RESUME_UNFINISHED_AGENT')
        self.assertEqual(due['resume_run_id'], 'original-trigger-run')
        self.assertEqual({t['id'] for t in due['tasks']}, {t['id'] for t in tasks})
        self.assertEqual(due['trigger_id'], 'synthetic-trigger')
        self.assertEqual(len(store.get(self.c, 'research_run_event')), count)
        self.assertEqual(run_tracking.dashboard(self.c)['scheduled_linked_completed'], 0)
        next_context = run_tracking.begin(self.c, 'next-maintenance', 'SCHEDULED', 'new-synthetic-trigger')
        report = dict(state='COMPLETED', steps={'learning': {'active_research': {'worker': due}}})
        finished = run_tracking.maintenance_finished(self.c, next_context, report)
        self.assertEqual(finished['phase'], 'COMPLETED_MAINTENANCE')
        self.assertEqual(finished['resume_run_id'], 'original-trigger-run')
        self.assertEqual(run_tracking.latest(self.c, 'original-trigger-run')['phase'], 'AGENT_STARTED')
        self.assertFalse(finished['research_completed'])
        self.assertEqual(run_tracking.dashboard(self.c)['scheduled_linked_completed'], 0)

    def test_awaiting_agent_resumes_but_engineering_acceptance_does_not(self):
        p = self.proposal()
        tasks = store.get(self.c, 'research_task')
        self.handoff('engineering', tasks, 'ENGINEERING_ACCEPTANCE')
        self.assertNotIn('resume_run_id', agent.worker_due(self.c, '20260105'))
        self.handoff('awaiting', tasks)
        due = agent.worker_due(self.c, '20260105')
        self.assertEqual(due['resume_run_id'], 'awaiting')
        self.assertEqual(run_tracking.latest(self.c, 'awaiting')['phase'], 'AWAITING_AGENT')
        self.assertEqual(run_tracking.dashboard(self.c)['scheduled_linked_completed'], 0)

    def test_latest_quote_report_is_readonly_and_checks_integrity(self):
        self.assertEqual(ql.latest(self.c)['state'], 'NOT_RUN')
        rid = store.put(self.c, 'quote_learning_report', 'test-report', dict(day='20260105', summary={'eligible_rows': 0}))
        before = self.c.total_changes
        report = ql.latest(self.c)
        self.assertEqual(report['record_id'], rid)
        self.assertEqual(self.c.total_changes, before)
        raw = self.c.execute('select a.sha256 from artifacts a join learning_records l on l.artifact_id=a.id where l.id=?', (rid,)).fetchone()
        (self.root / 'blobs' / raw[0]).write_text('corrupt')
        self.assertEqual(ql.latest(self.c)['state'], 'EVIDENCE_INVALID')


if __name__ == '__main__':
    unittest.main()
