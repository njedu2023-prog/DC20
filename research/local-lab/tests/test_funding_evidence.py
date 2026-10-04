import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import funding_evidence as f
class FundingTests(unittest.TestCase):
 def data(self):
  days=[f'202609{x:02}' for x in range(1,21)]
  return dict(source='test',definition='test net flow',date_evidence='synthetic',unit='CNY_10K',scope='DAILY_SAME_DEFINITION',expected_sessions=days,as_of=days[-1],available_at='2026-09-24T15:00:00+08:00',captured_at='2026-09-24T16:00:00+08:00',daily=[dict(day=d,net=1) for d in days])
 def test_units_and_windows(self):
  r=f.review(self.data(),'2026-09-24T22:00:00+08:00');self.assertTrue(r['eligible']);self.assertEqual(r['metrics']['20']['net_cny'],200000)
 def test_missing_date_not_zero(self):
  d=self.data();d['daily'].pop();r=f.review(d,'2026-09-24T22:00:00+08:00');self.assertFalse(r['eligible']);self.assertEqual(r['metrics'],{})
 def test_rolling_not_daily(self):
  d=self.data();d['scope']='ROLLING_RANKING';self.assertFalse(f.review(d,'2026-09-24T22:00:00+08:00')['eligible'])
 def test_post_cutoff(self):self.assertFalse(f.review(self.data(),'2026-09-24T14:00:00+08:00')['eligible'])
