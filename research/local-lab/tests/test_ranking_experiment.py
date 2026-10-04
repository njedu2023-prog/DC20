import copy
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from learning import ranking_experiment as e


class RankingExperimentTests(unittest.TestCase):
    def data(self):
        common = dict(day='20260924', pool_count=3, rank_eligible=True,
            prediction_cutoff='2026-09-24T22:00:00+08:00',
            feature_captured_at='2026-09-24T21:00:00+08:00',
            feature_cutoff='2026-09-24T21:30:00+08:00', feature_day='20260924',
            entry_at='2026-09-25T09:25:00+08:00', exit_at='2026-09-28T10:00:00+08:00',
            quote_status='VERIFIED_PRICE_PAIR', entry_price=10., original_scenario_applicable=False)
        rows = [dict(common, code='A', p=.60, bias20=.30, exit_price=9.),
                dict(common, code='B', p=.55, bias20=.10, exit_price=11.),
                dict(common, code='C', p=.50, bias20=.20, exit_price=10.5)]
        return dict(as_of='2026-10-04T12:00:00+08:00', ledger_sha='fixture', rows=rows)

    def test_manual_matched_returns_and_costs(self):
        r = e.run(self.data())
        d = r['days'][0]
        self.assertEqual(d['weights']['old_top2'], {'A': 1., 'B': 1.})
        self.assertEqual(d['weights']['candidate_top2'], {'B': 1., 'C': 1.})
        self.assertAlmostEqual(d['costs'][1]['old_top2'], -.0045)
        self.assertAlmostEqual(d['costs'][1]['candidate_top2'], .0705)
        self.assertAlmostEqual(d['costs'][1]['equal_cohort'], (.05 / 3) - .0045)
        self.assertEqual([x['cost'] for x in d['costs']], [.002, .0045, .008])

    def test_fractional_boundary_ties_do_not_pick_by_code(self):
        rows = self.data()['rows']
        for row in rows:
            row['p'] = .55
            row['bias20'] = .2
        self.assertEqual(e.selection(rows, 'p'), {'A': 2/3, 'B': 2/3, 'C': 2/3})
        self.assertEqual(e.selection(rows, 'bias20', True), e.selection(list(reversed(rows)), 'bias20', True))

    def test_partial_day_is_retained_and_same_cohort_used(self):
        data = self.data()
        data['rows'][2]['bias20'] = None
        r = e.run(data)
        self.assertEqual(len(r['days']), 1)
        self.assertEqual(r['days'][0]['cohort'], 'PARTIAL_MATCHED_COHORT')
        self.assertEqual(r['days'][0]['coverage'], 2/3)
        self.assertEqual(r['complete_pools_only']['independent_dates'], 0)
        for weights in r['days'][0]['weights'].values():
            self.assertEqual(set(weights), {'A', 'B'})
        self.assertIn('MISSING_FROZEN_BIAS20', r['excluded'][0]['reasons'])

    def test_late_feature_and_future_price_are_not_accepted(self):
        data = self.data()
        data['rows'][0]['feature_cutoff'] = '2026-09-25T09:26:00+08:00'
        data['rows'][1]['exit_at'] = '2026-10-09T10:00:00+08:00'
        r = e.run(data)
        self.assertEqual(r['days'], [])
        reasons = {x for row in r['excluded'] for x in row['reasons']}
        self.assertIn('FEATURE_NOT_AVAILABLE_AT_PREDICTION', reasons)
        self.assertIn('EXIT_NOT_DUE', reasons)

    def test_uninformed_and_nonfinite_not_used(self):
        data = self.data()
        data['rows'][0]['rank_eligible'] = False
        data['rows'][1]['bias20'] = None
        r = e.run(data)
        self.assertFalse(r['days'])
        self.assertIn('OLD_RANK_UNINFORMED_OR_MISSING', r['excluded'][0]['reasons'])
        row = self.data()['rows'][0]
        row['bias20'] = math.inf
        self.assertIn('MISSING_FROZEN_BIAS20', e.exclusions(row, e.stamp(self.data()['as_of'])))

    def test_duplicate_version_requires_explicit_selection(self):
        data = self.data()
        data['rows'].append(copy.deepcopy(data['rows'][0]))
        with self.assertRaisesRegex(ValueError, 'DUPLICATE_D_STOCK'):
            e.run(data)

    def test_unverified_quotes_not_counted_as_zero(self):
        data = self.data()
        data['rows'][0]['quote_status'] = 'MISSING'
        r = e.run(data)
        self.assertEqual(r['days'][0]['codes'], ['B', 'C'])
        self.assertAlmostEqual(r['days'][0]['costs'][1]['equal_cohort'], .0705)

    def test_scenario_never_filters_unrestricted_quote_study(self):
        data = self.data()
        r = e.run(data)
        self.assertEqual(r['days'][0]['observed_count'], 3)
        self.assertEqual(r['days'][0]['original_probability_outside_scenario'], ['A', 'B', 'C'])
        self.assertEqual(r['new_forward_days'], 0)
        self.assertFalse(r['promotion'])
        self.assertEqual(r['decision'], 'REJECT_PRODUCTION_PROMOTION')

    def test_date_cluster_is_not_stock_sample_count(self):
        r = e.run(self.data())
        aggregate = r['all_observed_cohorts']
        self.assertEqual(aggregate['independent_dates'], 1)
        ci = aggregate['costs'][1]['candidate_minus']['old_top2']['uncertainty']
        self.assertIsNone(ci['interval'])
        self.assertEqual(ci['reason'], 'FEWER_THAN_FIVE_DATES')
        self.assertEqual(e.day_interval([.01, -.02, .03, .02, -.01]), e.day_interval([.01, -.02, .03, .02, -.01]))

    def test_input_is_unchanged_and_render_labels_exploration(self):
        data = self.data()
        before = copy.deepcopy(data)
        r = e.run(data)
        self.assertEqual(data, before)
        self.assertIn('拒绝生产晋升', e.markdown(r))
        self.assertIn('至少80个完整新日期', e.markdown(r))

    def test_missing_pool_members_prevent_full_pool_claim(self):
        data = self.data()
        for row in data['rows']:
            row['pool_count'] = 5
        r = e.run(data)
        self.assertEqual(r['days'][0]['cohort'], 'PARTIAL_MATCHED_COHORT')
        self.assertEqual(r['days'][0]['coverage'], .6)

    def test_timezone_and_same_day_feature_guard(self):
        row = self.data()['rows'][0]
        row['feature_day'] = '20260925'
        row['feature_cutoff'] = '2026-09-24T21:30:00'
        reasons = e.exclusions(row, e.stamp(self.data()['as_of']))
        self.assertIn('FEATURE_DAY_MISSING_OR_FUTURE', reasons)
        self.assertIn('MISSING_OR_INVALID_TIMELINE', reasons)


if __name__ == '__main__':
    unittest.main()
