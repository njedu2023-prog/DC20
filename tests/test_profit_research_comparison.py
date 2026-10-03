import copy
import json
import unittest
from scripts import profit_research_comparison as comparison


def record(code, promotion, profit, value, *, fill=1, eligible=True, date='20260914'):
    return {
        'signal_date': date, 'ts_code': code, 'promotion_rank': promotion, 'profit_rank': profit,
        'profit_eligible': eligible, 'provenance': 'FORMAL_FROZEN_REFERENCE',
        'feature_snapshot_present': True, 'training_pair_ready': True, 'model_sha256': 'a' * 64,
        'outcome': {
            'status': comparison.SETTLED if fill else 'NO_FILL_CAPACITY',
            'net_return': value, 'proxy_fill': fill, 'label_available_date': '20260916',
            'basis': 'COUNTERFACTUAL_NATIVE_POLICY', 'actual_execution_claimed': False,
        },
    }


def day(rows=None, date='20260914'):
    return {'date': date, 'provenance': 'FORMAL_FROZEN_REFERENCE', 'records': rows or [
        record('A', 1, 3, -.1, date=date), record('B', 2, 1, .2, date=date),
        record('C', 3, 2, 0, fill=0, date=date),
    ]}


def strategy(result, key):
    return next(s for s in result['strategies'] if s['id'] == key)


class ComparisonTests(unittest.TestCase):
    def test_cash_slots_do_not_become_trades_or_refill(self):
        result = comparison.build_comparison([day()])
        top2 = strategy(result, 'profit_top2')
        self.assertAlmostEqual(top2['mean_daily_slot_return'], .1)
        self.assertAlmostEqual(top2['mean_filled_trade_net_return'], .2)
        self.assertEqual(top2['filled_trades'], 1)
        self.assertEqual(top2['no_fill_slots'], 1)
        selected = result['days'][0]['strategies']['profit_top2']['selected']
        self.assertEqual([r['ts_code'] for r in selected], ['B', 'C'])
        self.assertIsNone(selected[1]['filled_trade_net_return'])
        self.assertEqual(selected[1]['slot_net_return'], 0)
        self.assertEqual(top2['trade_win_rate'], 1)
        self.assertEqual(top2['worst_filled_trade_net_return'], .2)

    def test_same_day_paired_differences(self):
        result = comparison.build_comparison([day()])
        self.assertAlmostEqual(result['comparisons'][0]['mean_daily_difference'], .3)
        self.assertAlmostEqual(result['comparisons'][1]['mean_daily_difference'], .05)
        self.assertAlmostEqual(strategy(result, 'eligible_equal_weight')['mean_daily_slot_return'], .1 / 3)
        self.assertEqual(result['counts']['settled_trades'], 2)
        self.assertEqual(result['counts']['no_fill_records'], 1)

    def test_pending_any_rank_excludes_whole_day(self):
        d = day()
        d['records'][0]['outcome'].update(status='PENDING_T1', net_return=None)
        d['records'][0]['training_pair_ready'] = False
        result = comparison.build_comparison([d])
        self.assertEqual(result['counts']['eligible_days'], 0)
        self.assertTrue(all(s['observed_days'] == 0 for s in result['strategies']))
        self.assertIsNone(result['comparisons'][0]['mean_daily_difference'])

    def test_missing_label_not_zero(self):
        d = day()
        d['records'][2]['outcome'].update(status='MISSING_COUNTERFACTUAL_LABEL', net_return=None)
        self.assertEqual(comparison.build_comparison([d])['counts']['eligible_days'], 0)

    def test_ineligible_excluded_from_common_pool_not_all_day(self):
        d = day()
        extra = record('X', 0, None, None, eligible=False)
        extra['outcome'] = {'status': 'MISSING_COUNTERFACTUAL_LABEL'}
        d['records'].append(extra)
        result = comparison.build_comparison([d])
        self.assertEqual(result['counts']['eligible_days'], 1)
        self.assertEqual(result['counts']['eligible_candidates'], 3)

    def test_each_day_equal_weight_not_each_trade(self):
        a = day([record('A', 1, 1, .4), record('B', 2, 2, .2)])
        date = '20260915'
        b = day([record(str(i), i + 1, i + 1, 0, date=date) for i in range(4)], date)
        result = comparison.build_comparison([a, b])
        self.assertAlmostEqual(strategy(result, 'eligible_equal_weight')['mean_daily_slot_return'], .15)
        self.assertAlmostEqual(strategy(result, 'eligible_equal_weight')['mean_filled_trade_net_return'], .1)

    def test_all_no_fill_produces_no_trade_stats(self):
        d = day([record('A', 1, 1, 0, fill=0), record('B', 2, 2, 0, fill=0)])
        result = comparison.build_comparison([d])
        for s in result['strategies']:
            self.assertEqual(s['mean_daily_slot_return'], 0)
            self.assertIsNone(s['mean_filled_trade_net_return'])
            self.assertIsNone(s['trade_win_rate'])
            self.assertIsNone(s['worst_filled_trade_net_return'])

    def test_malformed_nofill_unknown_status_and_nonfinite_rejected(self):
        for changes in ({'net_return': .1}, {'proxy_fill': 1}, {'status': 'NO_FILL_UNKNOWN'},
                        {'net_return': float('nan')}, {'proxy_fill': False}):
            with self.subTest(changes=changes):
                d = day(); d['records'][2]['outcome'].update(changes)
                self.assertEqual(comparison.build_comparison([d])['counts']['eligible_days'], 0)

    def test_frozen_and_label_basis_required(self):
        for changes in ({'provenance': 'RETROSPECTIVE_UNVERIFIED'},
                        {'feature_snapshot_present': False}, {'training_pair_ready': False}):
            d = day(); d['records'][0].update(changes)
            self.assertEqual(comparison.build_comparison([d])['counts']['eligible_days'], 0)
        for changes in ({'basis': 'OPEN_RETURN'}, {'actual_execution_claimed': True}, {'label_available_date': None}):
            d = day(); d['records'][0]['outcome'].update(changes)
            self.assertEqual(comparison.build_comparison([d])['counts']['eligible_days'], 0)

    def test_duplicate_or_invalid_ranks_rejected(self):
        for rank in (None, True, 0, 1.5, float('inf'), 1):
            d = day(); d['records'][0]['profit_rank'] = rank
            self.assertEqual(comparison.build_comparison([d])['counts']['eligible_days'], 0)

    def test_duplicate_day_or_stock_and_mismatched_date_rejected(self):
        self.assertEqual(comparison.build_comparison([day(), day()])['counts']['eligible_days'], 0)
        d = day(); d['records'].append(copy.deepcopy(d['records'][0]))
        self.assertEqual(comparison.build_comparison([d])['counts']['eligible_days'], 0)
        d = day(); d['records'][0]['signal_date'] = '20260915'
        self.assertEqual(comparison.build_comparison([d])['counts']['eligible_days'], 0)

    def test_two_candidate_minimum_common_for_every_strategy(self):
        result = comparison.build_comparison([day([record('A', 1, 1, .1)])])
        self.assertEqual(result['counts']['eligible_days'], 0)

    def test_model_identity_not_mixed(self):
        d = day(); d['records'][0]['model_sha256'] = 'b' * 64
        self.assertEqual(comparison.build_comparison([d])['counts']['eligible_days'], 0)
        a = day(); b = day(date='20260915')
        for r in b['records']: r['model_sha256'] = 'b' * 64
        result = comparison.build_comparison([a, b])
        self.assertEqual(result['counts']['eligible_days'], 0)
        self.assertTrue(all('MIXED_FROZEN_MODEL_VERSIONS' in d['reasons'] for d in result['excluded_days']))

    def test_insufficient_days_no_interval(self):
        result = comparison.build_comparison([day()])
        self.assertEqual(result['status'], 'EXPLORATORY_INSUFFICIENT_DAYS')
        for comp in result['comparisons']:
            self.assertEqual(comp['bootstrap']['status'], 'INSUFFICIENT_DAYS')
            self.assertIsNone(comp['bootstrap']['lower'])
            self.assertIsNone(comp['bootstrap']['upper'])

    def test_bootstrap_deterministic_paired_day_only(self):
        days = [day(date=f'202609{i + 1:02}') for i in range(20)]
        result = comparison.build_comparison(days)
        self.assertEqual(result, comparison.build_comparison(list(reversed(days))))
        interval = result['comparisons'][0]['bootstrap']
        self.assertEqual(interval['status'], 'DESCRIPTIVE_ONLY')
        self.assertAlmostEqual(interval['lower'], .3)
        self.assertAlmostEqual(interval['upper'], .3)
        self.assertFalse(result['independent_holdout'])
        self.assertFalse(result['training_allowed'])

    def test_input_not_mutated_and_json_finite(self):
        days = [day()]; before = copy.deepcopy(days)
        result = comparison.build_comparison(days)
        self.assertEqual(days, before)
        json.dumps(result, allow_nan=False)

    def test_empty_dataset_is_explicit_not_zero_performance(self):
        result = comparison.build_comparison([])
        self.assertEqual(result['counts']['available_days'], 0)
        self.assertTrue(all(s['mean_daily_slot_return'] is None for s in result['strategies']))
        self.assertEqual(result['status'], 'EXPLORATORY_INSUFFICIENT_DAYS')


if __name__ == '__main__':
    unittest.main()
