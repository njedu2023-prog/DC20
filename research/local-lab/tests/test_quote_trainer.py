"""Synthetic-only acceptance of the independent quote-return temporal contract."""
import copy
import math
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lab
from learning import features, quote_learning, trainer


class QuoteTrainerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            trainer.npmod()
        except ImportError:
            raise unittest.SkipTest('Bundled Python with NumPy is required')

    def data(self, n=110):
        rows = []
        origin = datetime(2025, 1, 1, tzinfo=timezone(timedelta(hours=8)))
        for i in range(n):
            day = origin + timedelta(days=i)
            for j in range(4):
                signal = (j - 1.5) * .03 + math.sin(i) * .006
                rows.append(dict(day=day.strftime('%Y%m%d'), code=f'{j:06d}.SZ',
                                 cutoff=day.replace(hour=20).isoformat(),
                                 entry_at=(day + timedelta(days=1)).replace(hour=9, minute=25).isoformat(),
                                 exit_at=(day + timedelta(days=2)).replace(hour=10).isoformat(),
                                 label_available_at=(day + timedelta(days=2)).replace(hour=22).isoformat(),
                                 prediction_id=i*4+j, snapshot_id=i, label_id=10000+i,
                                 x={k: signal * (z+1) for z, k in enumerate(features.FEATURES)},
                                 gross=signal+.0045, status='VERIFIED_PRICE_PAIR', official_rank=j+1))
        return rows

    def dataset(self, rows=None):
        return dict(rows=self.data() if rows is None else rows, basis='QUOTE_RETURN',
                    fingerprint='SYNTHETIC_TEST_ONLY', excluded=[])

    def policy(self, small=False):
        p = quote_learning.policy()
        if small:
            p.update(train_days=4, calibration_days=5, test_days=3, min_rows=20)
        return p

    def test_strict_split_uses_prediction_cutoff_and_label_availability(self):
        rows = self.data(20)
        p = self.policy(small=True)
        tr, ca, te = trainer.split(rows, p)
        test_cut = min(lab.stamp(r['cutoff']) for r in te)
        cal_cut = min(lab.stamp(r['cutoff']) for r in ca)
        self.assertLess(max(lab.stamp(r['label_available_at']) for r in ca), test_cut)
        self.assertLess(max(lab.stamp(r['label_available_at']) for r in tr), cal_cut)
        self.assertLess(max(lab.stamp(r['exit_at']) for r in ca), test_cut)
        self.assertLess(max(lab.stamp(r['exit_at']) for r in tr), cal_cut)
        # A label arriving after the prediction but before the trade is unavailable.
        delayed_day = sorted({r['day'] for r in ca})[1]
        late = (test_cut + timedelta(hours=1)).isoformat()
        self.assertLess(lab.stamp(late), min(lab.stamp(r['entry_at']) for r in te))
        next(r for r in rows if r['day'] == delayed_day)['label_available_at'] = late
        _, purged, _ = trainer.split(rows, p)
        self.assertNotIn(delayed_day, {r['day'] for r in purged})
        self.assertEqual(len(ca)-len(purged), 4)

    def test_training_purge_excludes_whole_date_for_one_late_label(self):
        rows = self.data(20)
        p = self.policy(small=True)
        tr, ca, _ = trainer.split(rows, p)
        target = tr[8]['day']
        cutoff = min(lab.stamp(r['cutoff']) for r in ca)
        next(r for r in rows if r['day'] == target)['label_available_at'] = cutoff.isoformat()
        changed, _, _ = trainer.split(rows, p)
        self.assertNotIn(target, {r['day'] for r in changed})
        self.assertEqual(len(tr)-len(changed), 4)

    def test_exit_at_cutoff_is_also_whole_day_purged(self):
        rows = self.data(20)
        p = self.policy(small=True)
        tr, ca, _ = trainer.split(rows, p)
        target = tr[8]['day']
        cutoff = min(lab.stamp(r['cutoff']) for r in ca)
        row = next(r for r in rows if r['day'] == target)
        row['exit_at'] = cutoff.isoformat()
        row['label_available_at'] = (cutoff + timedelta(minutes=1)).isoformat()
        changed, _, _ = trainer.split(rows, p)
        self.assertNotIn(target, {r['day'] for r in changed})

    def test_missing_temporal_metadata_is_rejected_without_exit_fallback(self):
        for field in ('cutoff', 'label_available_at'):
            with self.subTest(field=field):
                rows = self.data(20)
                rows[0].pop(field)
                with self.assertRaisesRegex(ValueError, 'PREDICTION_AND_LABEL_AVAILABILITY_REQUIRED'):
                    trainer.split(rows, self.policy(small=True))
                with self.assertRaisesRegex(ValueError, 'INVALID_QUOTE_TEMPORAL_CONTRACT'):
                    trainer.fit_dataset(self.dataset(rows), self.policy())

    def test_invalid_quote_contract_rejected_even_below_minimum_size(self):
        bad_values = [('status', 'SETTLED'), ('cutoff', '2025-01-02T09:25:00+08:00'),
                      ('label_available_at', '2025-01-03T09:59:00+08:00')]
        for field, value in bad_values:
            with self.subTest(field=field):
                rows = self.data(2)
                rows[0][field] = value
                with self.assertRaisesRegex(ValueError, 'INVALID_QUOTE_TEMPORAL_CONTRACT'):
                    trainer.fit_dataset(self.dataset(rows), self.policy())

    def test_quote_policy_cannot_mix_with_execution_basis_or_status(self):
        for field, value in (('basis', 'QUOTE_PROXY'), ('observation_status', 'SETTLED'), ('strict_cutoff', False)):
            with self.subTest(field=field):
                p = self.policy()
                p[field] = value
                with self.assertRaisesRegex(ValueError, 'QUOTE_POLICY_REQUIRED'):
                    trainer.fit_dataset(self.dataset(self.data(2)), p)
        d = self.dataset(self.data(2))
        d['basis'] = 'QUOTE_PROXY'
        with self.assertRaisesRegex(ValueError, 'BASIS_POLICY_MISMATCH'):
            trainer.fit_dataset(d, self.policy())

    def test_quote_full_fit_predict_evaluate_is_research_only(self):
        report = trainer.fit_dataset(self.dataset(), self.policy())
        self.assertEqual(report['state'], 'TRAINED_RESEARCH_ONLY')
        self.assertEqual(report['basis'], 'QUOTE_RETURN')
        self.assertEqual(report['promotion'], 'DISABLED_QUOTE_RESEARCH_ONLY')
        self.assertTrue(report['folds'])
        for fold in report['folds']:
            a, b, c = (set(fold[k]) for k in ('train_dates', 'calibration_dates', 'test_dates'))
            self.assertFalse(a & b or a & c or b & c)
            self.assertLess(max(a), min(b))
            self.assertLess(max(b), min(c))
            for r in fold['predictions']:
                self.assertEqual(r['status'], 'QUOTE_SHADOW_UNVALIDATED')
                self.assertEqual(r['basis'], 'QUOTE_RETURN')
                self.assertTrue(0 < r['profit_probability'] < 1)
            ev = fold['evaluation']
            self.assertEqual(ev['promotion'], 'DISABLED_QUOTE_RESEARCH_ONLY')
            self.assertIn('no fill', ev['scope'])
            self.assertIn('worst_20pct_day_mean', ev['tail']['independent_top2'])
            self.assertEqual(ev['gains']['equal_pool']['n_days'], len(fold['test_dates']))
            for day in ev['days']:
                self.assertEqual(day['unobserved'], 0)
                self.assertNotIn('unfilled', day)
                for fee in day['costs']:
                    self.assertIn('observed_weight', fee['selections']['independent_top2'])
                    self.assertNotIn('filled_weight', fee['selections']['independent_top2'])

    def test_test_labels_do_not_change_fitted_model_or_predictions(self):
        rows = self.data()
        baseline = trainer.fit_dataset(self.dataset(rows), self.policy())
        test_dates = set(baseline['test_days'])
        changed = copy.deepcopy(rows)
        for r in changed:
            if r['day'] in test_dates:
                r['gross'] = .5 - r['gross']
        after = trainer.fit_dataset(self.dataset(changed), self.policy())
        self.assertEqual(baseline['bundle'], after['bundle'])
        self.assertEqual(baseline['folds'][0]['predictions'], after['folds'][0]['predictions'])
        self.assertNotEqual(baseline['folds'][0]['evaluation'], after['folds'][0]['evaluation'])

    def test_calibration_cannot_refit_training_coefficients_or_preprocessing(self):
        train, cal, _ = trainer.split(self.data(), self.policy())
        before = trainer.train_bundle(train, cal, self.policy())
        changed = copy.deepcopy(cal)
        for r in changed:
            r['gross'] = -r['gross']
        after = trainer.train_bundle(train, changed, self.policy())
        for field in ('preprocess', 'return_coefficients', 'probability_coefficients', 'constant_return', 'constant_probability'):
            self.assertEqual(before[field], after[field])
        self.assertNotEqual(before['platt'], after['platt'])
        self.assertNotEqual(before['residual_band'], after['residual_band'])

    def evaluation_fixture(self):
        rows = self.data(1)
        grosses = [.04, .02, -.02, -.04]
        scores = [.2, .1, .1, -.2]
        for r, g, momentum in zip(rows, grosses, [.3, .2, .2, -.3]):
            r['gross'] = g
            r['x']['return_5'] = momentum
        pred = [dict(day=r['day'], code=r['code'], expected_net=s, profit_probability=.5,
                     return_band=[-.3, .3]) for r, s in zip(rows, scores)]
        bundle = dict(constant_probability=.5, constant_return=0)
        return rows, pred, bundle

    def test_matched_baselines_ties_and_all_three_costs(self):
        rows, pred, bundle = self.evaluation_fixture()
        ev = trainer.evaluate(rows, pred, bundle, self.policy())
        self.assertEqual([r['cost'] for r in ev['days'][0]['costs']], [.002, .0045, .008])
        for costs in ev['days'][0]['costs']:
            fee, selections = costs['cost'], costs['selections']
            self.assertEqual(set(selections), {'independent_top2', 'equal_pool', 'momentum_top2', 'official_top2'})
            self.assertAlmostEqual(selections['independent_top2']['mean_net'], .02-fee)
            self.assertAlmostEqual(selections['momentum_top2']['mean_net'], .02-fee)
            self.assertAlmostEqual(selections['official_top2']['mean_net'], .03-fee)
            self.assertAlmostEqual(selections['equal_pool']['mean_net'], -fee)
            self.assertEqual(selections['independent_top2']['selected_weight'], 2)
            self.assertEqual(selections['independent_top2']['observed_weight'], 2)
            self.assertAlmostEqual(selections['independent_top2']['worst_net'], -.02-fee)
        self.assertAlmostEqual(ev['gains']['equal_pool']['mean'], .02)
        self.assertAlmostEqual(ev['gains']['official_top2']['mean'], -.01)
        self.assertAlmostEqual(ev['gains']['momentum_top2']['mean'], 0)

    def test_missing_momentum_skips_baseline_instead_of_zero_imputation(self):
        for missing in (None, float('nan')):
            rows, pred, bundle = self.evaluation_fixture()
            rows[0]['x']['return_5'] = missing
            ev = trainer.evaluate(rows, pred, bundle, self.policy())
            self.assertEqual(ev['gains']['momentum_top2']['n_days'], 0)
            self.assertIsNone(ev['gains']['momentum_top2']['mean'])
            self.assertIn('MOMENTUM_FEATURE_MISSING', ev['days'][0]['baseline_missing'])
            self.assertTrue(all('momentum_top2' not in c['selections'] for c in ev['days'][0]['costs']))
            if missing is None:
                self.assertIsNone(rows[0]['x']['return_5'])

    def test_baseline_comparison_denominator_uses_only_matched_dates(self):
        rows, pred, bundle = self.evaluation_fixture()
        second = copy.deepcopy(rows)
        second_pred = copy.deepcopy(pred)
        for r in second:
            r['day'] = '20250102'
            r['official_rank'] = None
            r['x']['return_5'] = None
        for r in second_pred:
            r['day'] = '20250102'
        ev = trainer.evaluate(rows+second, pred+second_pred, bundle, self.policy())
        self.assertEqual(ev['gains']['equal_pool']['n_days'], 2)
        self.assertEqual(ev['gains']['official_top2']['n_days'], 1)
        self.assertEqual(ev['gains']['momentum_top2']['n_days'], 1)
        self.assertEqual([d['n'] for d in ev['days']], [4, 4])

    def test_each_date_has_equal_training_weight(self):
        rows = self.data(2)
        rows = rows[:1] + rows[4:]
        weights = trainer.weights(rows)
        self.assertAlmostEqual(float(weights[0]), .5)
        self.assertAlmostEqual(float(sum(weights[1:])), .5)


if __name__ == '__main__':
    unittest.main()
