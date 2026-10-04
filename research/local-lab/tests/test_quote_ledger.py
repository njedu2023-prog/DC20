import sys,json,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from learning import quote_ledger as q,store

class QuoteLedgerTests(unittest.TestCase):
 def build(self,change=None,at='2026-01-06T22:00:00+08:00'):
  p=dict(id=1,signal_date='20260102',code='A',name='A',model='m',p=.55,artifact_id=1,cutoff='2026-01-02T22:00:00+08:00',recorded_at='2026-01-02T22:01:00+08:00',entry_at='2026-01-05T09:25:00+08:00',exit_at='2026-01-06T10:00:00+08:00',payload=json.dumps({'entry_scenario_id':'GAP_0_3'}))
  idx={('stk_auction','A','20260105'):[(1,dict(price=11,pre_close=10,vol=1000))],('stk_mins','A','2026-01-06 10:00:00'):[(2,dict(close=12,vol=1000))]}
  for day in ('20260105','20260106'):
   idx['adj_factor','A',day]=[(3,dict(adj_factor=1))]
   idx['stk_limit','A',day]=[(4,dict(up_limit=13,down_limit=9))]
   idx['daily','A',day]=[(5,dict(open=11,close=12,high=13,low=10,vol=10000))]
  if change:change(idx)
  with patch.object(q,'canonical',return_value=[p]),patch.object(q,'archive_doc',return_value=({},'h')),patch.object(q,'raw_index',return_value=(idx,[])),patch.object(q,'frozen_feature',return_value={'bias20':.1}),patch.object(store,'get',return_value=[]):return q.build(None,at)
 def test_quote_pair_outside_old_gap_is_computed_not_calibrated(self):
  d=self.build();r=d['rows'][0];self.assertEqual(d['summary']['verified'],1);self.assertAlmostEqual(r['net_return'],12/11-1-.0045);self.assertFalse(r['original_scenario_applicable']);self.assertFalse(r['probability_eligible']);self.assertEqual(r['execution_state'],'NOT_VERIFIED')
 def test_wrong_minute_not_substituted(self):
  def change(i):i['stk_mins','A','2026-01-06 10:01:00']=i.pop(('stk_mins','A','2026-01-06 10:00:00'))
  r=self.build(change)['rows'][0];self.assertEqual(r['quote_status'],'NEEDS_DATA_REVIEW');self.assertIsNone(r['net_return'])
 def test_conflicting_quote_stops_calculation(self):
  r=self.build(lambda i:i['stk_auction','A','20260105'].append((6,dict(price=10.5,pre_close=10,vol=1000))))['rows'][0]
  self.assertIn('CONFLICT_stk_auction',r['missing']);self.assertIsNone(r['net_return'])
 def test_corporate_action_not_ignored(self):
  r=self.build(lambda i:i.update({('adj_factor','A','20260106'):[(6,dict(adj_factor=2))]}))['rows'][0]
  self.assertIn('CORPORATE_ACTION_REVIEW_REQUIRED',r['missing']);self.assertIsNone(r['net_return'])
 def test_future_result_not_used_even_when_present(self):
  d=self.build(at='2026-01-05T22:00:00+08:00');self.assertEqual(d['rows'][0]['quote_status'],'WAIT_EXIT');self.assertIsNone(d['rows'][0]['exit_price']);self.assertEqual(d['summary']['due'],0)
 def test_zero_volume_not_fill(self):
  r=self.build(lambda i:i.update({('stk_mins','A','2026-01-06 10:00:00'):[(6,dict(close=12,vol=0))]}))['rows'][0]
  self.assertIsNone(r['net_return'])
 def test_limit_quote_retained_with_risk(self):
  r=self.build(lambda i:i.update({('stk_limit','A','20260105'):[(6,dict(up_limit=11,down_limit=9))]}))['rows'][0]
  self.assertEqual(r['quote_status'],'VERIFIED_PRICE_PAIR');self.assertIn('ENTRY_AT_UP_LIMIT_QUEUE_UNKNOWN',r['execution_risks'])
