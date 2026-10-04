import sys,tempfile,json,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]));import history_pairs as h
class PairTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.p=Path(self.t.name);self.c=self.p/'calendar.csv';self.c.write_text('cal_date,is_open,pretrade_date\n20260929,1,20260928\n')
  self.a=self.p/'auction.json';self.a.write_text(json.dumps({'fields':['ts_code','price','vol','amount'],'items':[['002909.SZ',10,100,1000]]}))
  self.rows=[dict(kind='auction',day='20260928',path=str(self.a),sha256='a',issues=['FILL_CAPACITY_UNVERIFIED']),dict(kind='minute',day='20260929',code='002909.SZ',sha256='m',point1000=[{'close':11}],issues=['BAR_TIME_SEMANTICS_UNCONFIRMED'])]
 def tearDown(self):self.t.cleanup()
 def run_case(self):
  f=self.p/'inventory.json';f.write_text(json.dumps(self.rows));return h.build(f,self.c,self.p/'result.json')
 def test_exploratory_only(self):
  r=self.run_case();self.assertEqual(r['price_pairs'],1);self.assertEqual(r['calibration_samples'],0)
 def test_conflict(self):
  self.rows.append(dict(self.rows[1],sha256='changed'));self.assertEqual(self.run_case()['price_pairs'],0)
 def test_bad_hash(self):
  self.rows[1]['issues'].append('HASH_UNVERIFIED');self.assertEqual(self.run_case()['price_pairs'],0)
 def test_no_calendar(self):
  self.c.write_text('cal_date,is_open,pretrade_date\n');self.assertEqual(self.run_case()['price_pairs'],0)
if __name__=='__main__':unittest.main()
