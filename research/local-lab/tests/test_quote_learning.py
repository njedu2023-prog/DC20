import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lab
from learning import features, quote_learning as ql, quote_ledger, store


class QuoteLearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.c = lab.connect(self.root / 'db.sqlite')
        self.clock = patch.object(lab, 'now', return_value='2026-01-07T22:00:00+08:00')
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.c.close()
        self.tmp.cleanup()

    def put(self, kind, key, data, at):
        with patch.object(lab, 'now', return_value=at):
            return store.put(self.c, kind, key, data)

    def artifact(self, name, data, at='2026-01-02T20:00:00+08:00'):
        p = self.root / (name + '.json')
        p.write_text(json.dumps(data))
        with patch.object(lab, 'now', return_value=at):
            aid = lab.artifact(self.c, p)
        meta = self.c.execute('select * from artifacts where id=?', (aid,)).fetchone()
        return dict(artifact_id=aid, sha256=meta['sha256'])

    def raw(self, key, api, rows, captured='2026-01-02T20:30:00+08:00', recorded=None):
        fields = list(rows[0])
        return self.put('raw', key, dict(api=api, params={'freq': '1min'} if api == 'stk_mins' else {},
                        data=dict(fields=fields, items=[[r[k] for k in fields] for r in rows]),
                        captured_at=captured, state='OK'), recorded or captured)

    def fixture(self, snapshot=True, snapshot_change=None, raw_recorded=None):
        self.codes = ['000001.SZ', '000002.SZ']
        self.entry = '2026-01-05T09:25:00+08:00'
        self.exit = '2026-01-06T10:00:00+08:00'
        self.cutoff = '2026-01-02T22:00:00+08:00'
        source = self.artifact('pool-source', {'data': self.codes})
        pool = dict(signal_date='20260102', expected_count=2, captured_at='2026-01-02T20:00:00+08:00',
                    source='TEST_ONLY', source_ref=source,
                    candidates=[dict(code=c, name=c, board_stage=2) for c in self.codes])
        poolref = self.artifact('pool', pool)
        self.pool_id = self.put('pool', 'pool', pool, '2026-01-02T20:01:00+08:00')
        forecast = dict(signal_date='20260102', information_cutoff=self.cutoff, entry_at=self.entry,
                        exit_at=self.exit, candidate_pool=poolref,
                        predictions=[dict(code=c, p=.5, metrics={'bias20_pct': 5}) for c in self.codes])
        fr = self.artifact('forecast', forecast, '2026-01-02T22:01:00+08:00')
        lab.model(self.c, 'TEST_ONLY')
        self.predictions = []
        for n, code in enumerate(self.codes):
            payload = dict(forecast['predictions'][n], candidate_pool=poolref,
                           entry_scenario_id='ANY_EXECUTABLE_AUCTION', comparison_official_rank=n+1)
            cur = self.c.execute('insert into predictions(signal_date,code,name,model,strategy,p,cost,entry_at,exit_at,cutoff,recorded_at,provenance,artifact_id,payload) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                ('20260102', code, code, 'TEST_ONLY', lab.STRATEGY, .5, .0045,
                                 self.entry, self.exit, self.cutoff, '2026-01-02T22:01:00+08:00',
                                 'PROSPECTIVE_LOCAL', fr['artifact_id'], json.dumps(payload)))
            self.predictions.append(cur.lastrowid)
        self.c.commit()
        feature_raw = self.raw('feature', 'daily', [dict(ts_code=c, trade_date='20260102', close=10) for c in self.codes], recorded=raw_recorded)
        if snapshot:
            snap = dict(method='daily_net_return_v1', day='20260102', pool_id=self.pool_id,
                        expected_count=2, feature_schema=features.VERSION, provenance='PROSPECTIVE_LOCAL', state='COMPLETE',
                        captured_at='2026-01-02T21:00:00+08:00', cutoff='2026-01-02T21:01:00+08:00',
                        entry_at=self.entry, exit_at=self.exit, shared_raw_ids=[feature_raw],
                        rows=[dict(code=c, state='VALID_NUMERIC_BASELINE', raw_ids=[feature_raw],
                                   features={k: .1 + n*.01 for k in features.FEATURES}) for n, c in enumerate(self.codes)])
            if snapshot_change:
                snapshot_change(snap)
            self.snapshot_id = self.put('snapshot', 'snapshot', snap, '2026-01-02T21:02:00+08:00')
        quote_at = '2026-01-06T18:00:00+08:00'
        self.entry_raw = self.raw('entry', 'stk_auction', [dict(ts_code=c, trade_date='20260105', price=10, pre_close=9.9, vol=1000) for c in self.codes], quote_at)
        self.exit_raw = self.raw('exit', 'stk_mins', [dict(ts_code=c, trade_time='2026-01-06 10:00:00', close=11, vol=1000) for c in self.codes], quote_at)
        self.raw('adjustments', 'adj_factor', [dict(ts_code=c, trade_date=d, adj_factor=1) for c in self.codes for d in ('20260105', '20260106')], quote_at)
        self.raw('quote-daily', 'daily', [dict(ts_code=c, trade_date=d, open=10, high=12, low=9, close=11, vol=10000) for c in self.codes for d in ('20260105', '20260106')], quote_at)
        with patch.object(lab, 'now', return_value='2026-01-06T22:00:00+08:00'):
            self.ledger = quote_ledger.run(self.c)

    def test_full_day_binds_features_label_and_time_without_settlement(self):
        self.fixture()
        d = ql.dataset(self.c)
        self.assertEqual(d['summary']['eligible_days'], 1)
        self.assertEqual(len(d['rows']), 2)
        for r in d['rows']:
            self.assertEqual(r['status'], 'VERIFIED_PRICE_PAIR')
            self.assertEqual(r['snapshot_id'], self.snapshot_id)
            self.assertEqual(r['label_id'], self.ledger['record_id'])
            self.assertEqual(r['label_available_at'], '2026-01-06T22:00:00+08:00')
            self.assertEqual(r['cutoff'], self.cutoff)
            self.assertAlmostEqual(r['gross'], .1)
        self.assertEqual(self.c.execute('select count(*) from outcomes').fetchone()[0], 0)
        self.assertEqual(store.get(self.c, 'label'), [])

    def test_bias_only_is_descriptive_not_fabricated_numeric_training(self):
        self.fixture(snapshot=False)
        d = ql.dataset(self.c)
        self.assertEqual(d['rows'], [])
        self.assertEqual(d['summary']['verified_quote_rows'], 2)
        self.assertEqual(d['candidates'][0]['frozen_descriptive_features'], {'bias20': .05})
        self.assertIn('NO_PRE_CUTOFF_NUMERIC_SNAPSHOT', d['candidates'][0]['reasons'])

    def test_postcut_snapshot_and_raw_have_specific_reasons(self):
        self.fixture(raw_recorded='2026-01-03T00:00:00+08:00')
        d = ql.dataset(self.c)
        self.assertEqual(d['rows'], [])
        self.assertIn('POST_CUTOFF_FEATURE_RAW', d['candidates'][0]['reasons'])

    def test_snapshot_future_capture_rejected(self):
        self.fixture(snapshot_change=lambda s: s.update(captured_at='2026-01-03T00:00:00+08:00'))
        self.assertIn('POST_CUTOFF_SNAPSHOT', ql.dataset(self.c)['candidates'][0]['reasons'])

    def test_partial_feature_day_is_whole_day_excluded(self):
        self.fixture(snapshot_change=lambda s: s['rows'][0]['features'].pop('return_5'))
        d = ql.dataset(self.c)
        self.assertEqual(d['summary']['eligible_rows'], 0)
        self.assertEqual(d['days'][0]['expected_count'], 2)
        self.assertEqual(len(d['candidates']), 2)

    def test_snapshot_pool_identity_rejected(self):
        self.fixture(snapshot_change=lambda s: s.update(expected_count=1))
        self.assertIn('SNAPSHOT_POOL_OR_CONTRACT_MISMATCH', ql.dataset(self.c)['candidates'][0]['reasons'])

    def test_later_conflicting_quote_invalidates_old_verified_ledger(self):
        self.fixture()
        self.raw('conflict', 'stk_auction', [dict(ts_code=self.codes[0], trade_date='20260105', price=10.5, pre_close=9.9, vol=1000)], '2026-01-07T10:00:00+08:00')
        d = ql.dataset(self.c)
        self.assertEqual(d['rows'], [])
        self.assertIn('CONFLICT_stk_auction', d['candidates'][0]['reasons'])
        self.assertEqual(d['summary']['verified_quote_rows'], 1)

    def test_corrupted_price_raw_invalidates_ledger(self):
        self.fixture()
        r = self.c.execute('select a.sha256 from artifacts a join learning_records l on a.id=l.artifact_id where l.id=?', (self.exit_raw,)).fetchone()
        (self.root / 'blobs' / r[0]).write_text('corrupt')
        d = ql.dataset(self.c)
        self.assertEqual(d['rows'], [])
        self.assertIn('CORRUPT_QUOTE_EVIDENCE', d['candidates'][0]['reasons'])

    def test_result_not_available_until_verification(self):
        self.fixture()
        d = ql.dataset(self.c, at='2026-01-06T20:00:00+08:00')
        self.assertEqual(d['rows'], [])
        self.assertIn('FUTURE_LABEL_AVAILABILITY', d['candidates'][0]['reasons'])

    def test_immutable_dataset_fingerprint_and_readonly_inspection(self):
        self.fixture()
        before = self.c.total_changes
        a = ql.dataset(self.c)
        b = ql.dataset(self.c)
        self.assertEqual(a, b)
        self.assertEqual(self.c.total_changes, before)

    def test_run_reports_zero_or_small_samples_and_separate_model_kind(self):
        self.fixture()
        r = ql.run(self.c, '20260107')
        self.assertEqual(r['training']['state'], 'BLOCKED_INSUFFICIENT_DATA')
        self.assertEqual(r['summary']['eligible_rows'], 2)
        models = store.get(self.c, 'quote_model')
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]['scope'], 'QUOTE_RETURN_RESEARCH_ONLY')
        self.assertFalse(models[0]['production_promotion'])
        self.assertEqual(store.get(self.c, 'model'), [])
        self.assertEqual(store.get(self.c, 'label'), [])
        retry = ql.run(self.c, '20260107')
        self.assertFalse(retry['training']['attempted'])
        self.assertEqual(len(store.get(self.c, 'quote_model')), 1)
        self.assertEqual(len(store.get(self.c, 'quote_dataset')), 1)

    def test_cadence_requires_both_seven_days_and_five_new_days(self):
        self.fixture()
        ql.run(self.c, '20260107')
        original = ql.dataset(self.c)
        # Synthetic readiness changes exercise scheduling without creating real evidence.
        changed = copy.deepcopy(original)
        changed['fingerprint'] = 'TEST_ONLY_NEW_DATES'
        changed['rows'] = [dict(original['rows'][0], day=f'202601{d:02d}') for d in range(8, 13)]
        with patch.object(ql, 'dataset', return_value=changed):
            self.assertFalse(ql.run(self.c, '20260108')['training']['attempted'])
            self.assertTrue(ql.run(self.c, '20260114')['training']['attempted'])
        self.assertEqual(len(store.get(self.c, 'quote_training_cycle')), 2)

    def test_corrupted_snapshot_blob_excludes_full_date(self):
        self.fixture()
        r = self.c.execute('select a.sha256 from artifacts a join learning_records l on a.id=l.artifact_id where l.id=?', (self.snapshot_id,)).fetchone()
        (self.root / 'blobs' / r[0]).write_text('corrupt')
        d = ql.dataset(self.c)
        self.assertEqual(d['rows'], [])
        self.assertEqual(d['days'][0]['expected_count'], 2)
        self.assertIn('CORRUPT_EVIDENCE', d['candidates'][0]['reasons'])

    def test_missing_original_prediction_preserves_full_pool_denominator(self):
        self.fixture()
        canonical = quote_ledger.canonical(self.c)
        with patch.object(quote_ledger, 'canonical', return_value=canonical[:1]):
            d = ql.dataset(self.c)
        self.assertEqual(d['rows'], [])
        self.assertEqual(d['days'][0]['expected_count'], 2)
        self.assertEqual(len(d['candidates']), 2)
        self.assertIn('MISSING_ORIGINAL_FROZEN_PREDICTION', d['candidates'][1]['reasons'])

    def test_quote_shadow_is_new_prospective_record_and_separate_probability(self):
        from learning import trainer
        self.fixture()
        old = next(s for s in ql._records(self.c, 'snapshot') if s['id'] == self.snapshot_id)
        snap = {k: v for k, v in old.items() if not k.startswith('_') and k not in ('id', 'key')}
        pool = dict(signal_date='20260108', expected_count=2, candidates=[dict(code=c, name=c, board_stage=2) for c in self.codes])
        pid = self.put('pool', 'future-pool', pool, '2026-01-08T20:00:00+08:00')
        snap.update(day='20260108', pool_id=pid, cutoff='2026-01-08T21:00:00+08:00',
                    captured_at='2026-01-08T21:00:00+08:00', entry_at='2026-01-09T09:25:00+08:00',
                    exit_at='2026-01-12T10:00:00+08:00')
        sid = self.put('snapshot', 'future-snapshot', snap, '2026-01-08T21:01:00+08:00')
        model = dict(method=ql.METHOD, basis=ql.BASIS, state='TRAINED_RESEARCH_ONLY',
                     created_at='2026-01-07T21:00:00+08:00', test_days=['20260102'],
                     bundle=dict(last_fit_available_at='2026-01-06T22:00:00+08:00'))
        mid = self.put('quote_model', 'TEST_ONLY_MODEL', model, model['created_at'])
        pred = [dict(code=c, day='20260108', profit_probability=.6, expected_net=.02) for c in self.codes]
        before = self.c.execute('select count(*) from predictions').fetchone()[0]
        with patch.object(lab, 'now', return_value='2026-01-08T21:02:00+08:00'), patch.object(trainer, 'predict', return_value=pred):
            rid = ql.shadow(self.c, mid, sid)
            self.assertEqual(rid, ql.shadow(self.c, mid, sid))
        shadow = store.raw(self.c, rid)
        self.assertEqual(shadow['state'], 'QUOTE_SHADOW_UNVALIDATED')
        self.assertFalse(shadow['calibrates_original_probability'])
        self.assertEqual(self.c.execute('select count(*) from predictions').fetchone()[0], before)
        with self.assertRaisesRegex(ValueError, 'QUOTE_SHADOW_NOT_PRE_ENTRY'):
            with patch.object(lab, 'now', return_value='2026-01-09T10:00:00+08:00'):
                ql.shadow(self.c, mid, sid)

    def test_thresholds_and_probability_scope_preserved(self):
        p = ql.policy()
        self.assertEqual((p['train_days'], p['calibration_days'], p['test_days'], p['min_rows']), (60, 20, 20, 300))
        self.assertEqual(p['costs'], [.002, .0045, .008])
        self.assertFalse(p['calibrates_original_probability'])
        self.assertTrue(p['strict_cutoff'])

    def test_quote_shadow_blocks_untrained_and_unknown_models(self):
        self.fixture()
        report = ql.run(self.c, '20260107')
        with self.assertRaisesRegex(ValueError, 'QUOTE_SHADOW_NOT_READY'):
            ql.shadow(self.c, report['training']['model_id'], self.snapshot_id)
        with self.assertRaisesRegex(ValueError, 'QUOTE_MODEL_OR_SNAPSHOT_MISSING'):
            ql.shadow(self.c, 99999, self.snapshot_id)


if __name__ == '__main__':
    unittest.main()
