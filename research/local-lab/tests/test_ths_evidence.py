import importlib.util,json,unittest
from pathlib import Path
s=importlib.util.spec_from_file_location('ths',Path(__file__).resolve().parents[1]/'ths_evidence.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class EvidenceTests(unittest.TestCase):
 def fixture(self):
  rows=[f'{h:02}{n:02},10,0,10,0' for h in range(9,16) for n in range(60) if '0930'<=f'{h:02}{n:02}'<='1130' or '1301'<=f'{h:02}{n:02}'<='1500']
  return {'hs_000001':{'date':'20260924','data':';'.join(rows),'pre':'10','name':'Test'}}
 def wrap(self,d):return 'callback('+json.dumps(d)+')'
 def test_valid(self):self.assertEqual(m.minute(self.wrap(self.fixture()),'hs_000001','20260924')['n'],241)
 def test_date_fallback_rejected(self):
  with self.assertRaises(ValueError):m.minute(self.wrap(self.fixture()),'hs_000001','20260922')
 def test_duplicate(self):
  d=self.fixture();d['hs_000001']['data']+=';1500,10,0,10,0'
  with self.assertRaises(ValueError):m.minute(self.wrap(d),'hs_000001','20260924')
 def test_missing1000(self):
  d=self.fixture();d['hs_000001']['data']=d['hs_000001']['data'].replace('1000,10,0,10,0;','')
  with self.assertRaises(ValueError):m.minute(self.wrap(d),'hs_000001','20260924')
 def test_aftermarket_excluded(self):
  d=self.fixture();d['hs_000001']['data']+=';1505,99,0,10,0';r=m.minute(self.wrap(d),'hs_000001','20260924');self.assertEqual(r['last'],10);self.assertEqual(r['excluded_times'],['1505'])
 def test_break_window(self):
  d={'data':'20260922,1,1,1,10000;20260923,1,1,1,10;20260924,1,1,1,11'};r=m.daily(self.wrap(d),'20260924');self.assertEqual(r['latest_segment_n'],2);self.assertIsNone(r['ma']['5'])
 def test_empty_daily(self):
  with self.assertRaises(ValueError):m.daily(self.wrap({'data':''}),'20260924')
 def test_jsonp_not_executed(self):
  with self.assertRaises(ValueError):m.unwrap('callback({}); evil()')
if __name__=='__main__':unittest.main()
