import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import benchmark as b
class BenchTests(unittest.TestCase):
 def rows(self):return [dict(code=str(i),p=.55,rank_eligible=True,official_rank=i+1,status='SETTLED',entry_price=10,exit_price=11 if i<2 else 9) for i in range(3)]
 def test_ties_no_arbitrary_winner(self):self.assertEqual(b.selection(self.rows(),'p'),{'0':2/3,'1':2/3,'2':2/3})
 def test_missing_excludes_whole_day(self):
  rows=self.rows();rows[-1]['status']='MISSING';self.assertEqual(b.compare({'D':rows},'ACTUAL')['days'],[])
 def test_no_fill_not_zero_return(self):
  rows=self.rows();rows[-1]['status']='UNFILLED';r=b.compare({'D':rows},'ACTUAL')['days'][0]['costs'][1]['selections']['equal_pool'];self.assertAlmostEqual(r['conditional_mean_net'],.0955);self.assertEqual(r['filled_weight'],2)
 def test_unexited_no_cherry_pick(self):
  rows=self.rows();rows[-1]['status']='UNEXITED';self.assertFalse(b.compare({'D':rows},'ACTUAL')['days'])
 def test_baseline_and_cost(self):
  r=b.compare({'D':self.rows()},'ACTUAL');self.assertEqual(r['baseline_brier'],.25);self.assertEqual(len(r['days'][0]['costs']),3)

 def test_official_missing_does_not_block_local_vs_pool(self):
  rows=self.rows()
  for r in rows:r['official_rank']=None
  result=b.compare({'D':rows},'QUOTE_PROXY');day=result['days'][0]
  self.assertIn('independent_top2',day['costs'][1]['selections'])
  self.assertIn('equal_pool',day['costs'][1]['selections'])
  self.assertNotIn('official_top2',day['costs'][1]['selections'])
  self.assertEqual(result['paired_comparisons']['equal_pool']['n_days'],1)
  self.assertEqual(result['paired_comparisons']['official_top2']['n_days'],0)
  self.assertEqual(day['comparison_blocks'][0]['comparison'],'official_top2')

 def test_invalid_official_two_rank_one_not_used(self):
  rows=self.rows();rows[1]['official_rank']=1
  result=b.compare({'D':rows},'ACTUAL')
  self.assertNotIn('official_top2',result['days'][0]['costs'][1]['selections'])

 def test_old_condition_excludes_probability_not_returns(self):
  rows=self.rows()
  for r in rows:r['probability_eligible']=False
  result=b.compare({'D':rows},'QUOTE_PROXY');scores=result['days'][0]['costs'][1]
  self.assertIsNone(scores['brier']);self.assertIsNone(scores['log_loss'])
  self.assertEqual(scores['probability_n'],0)
  self.assertIsNotNone(scores['selections']['independent_top2']['conditional_mean_net'])
  self.assertIsNone(result['day_equal_brier'])
  self.assertIsNone(result['baseline_brier'])

 def test_probability_denominator_only_eligible_rows(self):
  rows=self.rows();rows[0]['probability_eligible']=True
  rows[1]['probability_eligible']=False;rows[2]['probability_eligible']=False
  result=b.compare({'D':rows},'ACTUAL');scores=result['days'][0]['costs'][1]
  self.assertEqual(scores['probability_n'],1)
  self.assertEqual(scores['probability_excluded'],2)
  self.assertAlmostEqual(scores['brier'],.45**2)
  self.assertEqual(scores['selections']['equal_pool']['filled_weight'],3)

 def test_quote_layer_never_validates_execution_probability(self):
  rows=self.rows()
  for row in rows:
   row.update(status='VERIFIED_PRICE_PAIR',probability_eligible=True)
  result=b.compare({'D':rows},'QUOTE_RETURN')
  self.assertFalse(result['execution_claim'])
  self.assertEqual(result['days'][0]['result_count_kind'],'VERIFIED_PRICE_PAIRS')
  self.assertEqual(result['probability_n'],0)
  self.assertIsNone(result['days'][0]['costs'][1]['brier'])
  self.assertTrue(all(not row['probability_eligible'] for row in result['observations']))
  self.assertFalse(b.compare({'D':rows},'ACTUAL')['days'])
  self.assertFalse(b.compare({'D':rows},'QUOTE_PROXY')['days'])

 def test_partial_cohort_keeps_individual_observations(self):
  rows=self.rows();rows[-1]['status']='MISSING'
  result=b.compare({'D':rows},'QUOTE_PROXY')
  self.assertFalse(result['days'])
  self.assertEqual(len(result['observations']),3)
  self.assertEqual(sum(r['price_verified'] for r in result['observations']),2)
  self.assertIsNone(result['observations'][-1]['net_at_base_cost'])

 def test_unrated_does_not_erase_pool_returns(self):
  rows=self.rows();rows[-1]['rank_eligible']=False
  result=b.compare({'D':rows},'QUOTE_PROXY');scores=result['days'][0]['costs'][1]
  self.assertIn('equal_pool',scores['selections'])
  self.assertNotIn('independent_top2',scores['selections'])
  self.assertEqual(scores['probability_n'],2)

 def test_invalid_price_not_silently_zero(self):
  for price in (0,-1,float('nan'),float('inf'),None):
   rows=self.rows();rows[-1]['exit_price']=price
   result=b.compare({'D':rows},'QUOTE_PROXY')
   self.assertFalse(result['days']);self.assertFalse(result['observations'][-1]['price_verified'])

 def test_frozen_probability_not_invented_expected_return_rank(self):
  rows=self.rows();rows[2].update(p=.8,expected_net_return=.5)
  result=b.compare({'D':rows},'ACTUAL')
  self.assertEqual(result['ranking_source'],'FROZEN_PROBABILITY_RANK')
  self.assertEqual(result['days'][0]['costs'][1]['selections']['independent_top2']['selected_codes'],{'2':1,'0':.5,'1':.5})

 def test_old_policy_and_new_policy_exact_compatibility(self):
  self.assertEqual(b.POLICY_V1['version'],'MATCHED_COHORT_V1')
  self.assertEqual(b.POLICY_V1['scenario'],'GAP_0_3')
  self.assertEqual(b.ACTIVE_POLICY['scenario'],'ANY_EXECUTABLE_AUCTION')
  for policy,scenario in ((b.POLICY_V1,'GAP_0_3'),(b.POLICY_V1,'ANY_EXECUTABLE_AUCTION'),(b.POLICY_V2,'ANY_EXECUTABLE_AUCTION')):
   self.assertTrue(b.policy_compatible(dict(comparison_policy=policy,entry_scenario_id=scenario)))
  self.assertFalse(b.policy_compatible(dict(comparison_policy=b.POLICY_V2,entry_scenario_id='GAP_0_3')))
  changed=dict(b.POLICY_V1,costs=[.0])
  self.assertFalse(b.policy_compatible(dict(comparison_policy=changed,entry_scenario_id='GAP_0_3')))

 def test_old_gap_probability_missing_evidence_fails_closed(self):
  row=self.rows()[0];row['entry_scenario_id']='GAP_0_3'
  self.assertFalse(b.probability_applicable(row))
  row.update(previous_close=10,entry_price=10.2)
  self.assertTrue(b.probability_applicable(row))
  row['entry_price']=10.5
  self.assertFalse(b.probability_applicable(row))
  row.update(entry_price=10.2,probability_eligible=False)
  self.assertFalse(b.probability_applicable(row))

 def test_day_equal_probability_does_not_include_unscored_day(self):
  d1=self.rows();d2=self.rows()
  for r in d2:r['probability_eligible']=False
  result=b.compare({'D1':d1,'D2':d2},'ACTUAL')
  self.assertEqual(result['n_days'],2);self.assertEqual(result['probability_days'],1)
  self.assertEqual(result['probability_n'],3)
  self.assertAlmostEqual(result['day_equal_brier'],b.compare({'D1':d1},'ACTUAL')['day_equal_brier'])

 def test_reject_unknown_basis(self):
  with self.assertRaises(ValueError):b.compare({'D':self.rows()},'MIXED')
