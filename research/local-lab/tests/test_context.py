import sys,unittest,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import context
class ContextTest(unittest.TestCase):
 def row(self):return dict(family='leader_indices',instrument='883958',source='test',evidence='test fixture',as_of='2026-09-24T15:00:00+08:00',available_at='2026-09-24T16:00:00+08:00',captured_at='2026-09-24T17:00:00+08:00',status='VERIFIED',values=dict(last=105,open=108,previous_close=100,high=110,low=104))
 def checkrow(self,r):return context.review({'observations':[r]},'2026-09-24T22:00:00+08:00')['observations'][0]
 def test_gap_is_not_intraday_profit(self):
  r=self.checkrow(self.row());self.assertTrue(r['eligible']);self.assertGreater(r['predictive_metrics']['gap_pct'],0);self.assertLess(r['predictive_metrics']['open_to_last_pct'],0)
 def test_undated_quarantined(self):
  r=self.row();r['as_of']=None;v=self.checkrow(r);self.assertFalse(v['eligible']);self.assertFalse(v['predictive_metrics']);self.assertTrue(v['diagnostic_metrics'])
 def test_future_captured_excluded(self):
  r=self.row();r['captured_at']='2026-09-25T00:00:00+08:00';self.assertFalse(self.checkrow(r)['eligible'])
 def test_bad_ohlc_excluded(self):
  r=self.row();r['values']['high']=101;self.assertFalse(self.checkrow(r)['eligible'])
 def test_partial_members_not_breadth(self):
  r=self.row();r.update(members=[dict(code='A',return_pct=10)],expected_members=2,membership_as_of=r['as_of']);v=self.checkrow(r);self.assertFalse(v['eligible']);self.assertNotIn('median_return_pct',v['diagnostic_metrics'])
 def test_full_members_breadth(self):
  r=self.row();r.update(members=[dict(code='A',return_pct=10),dict(code='B',return_pct=-6)],expected_members=2,membership_as_of=r['as_of']);v=self.checkrow(r);self.assertEqual(v['predictive_metrics']['positive_ratio'],.5);self.assertEqual(v['predictive_metrics']['loss5_ratio'],.5)
 def test_both_indices_needed(self):
  report=context.review({'observations':[self.row()]},'2026-09-24T22:00:00+08:00');self.assertEqual(context.plan(report)[0]['state'],'NEEDS_EVIDENCE')
 def test_nonfinite_rejected(self):
  r=self.row();r['values']['last']=float('nan')
  with self.assertRaises(ValueError):self.checkrow(r)
