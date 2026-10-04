import unittest,sys,json,copy,tempfile,sqlite3
from pathlib import Path
from datetime import datetime,timedelta,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import adaptive_weights as w
import lab
class WeightTest(unittest.TestCase):
 def setUp(self):
  self.cfg=json.loads((w.ROOT/'weight-policy-v1.json').read_text());self.cfg['effective_at']='2020-01-01T00:00:00+00:00'
 def features(self):return {k:dict(score=.5,status='VERIFIED',evidence_ids=[k],rationale='test',available_at='2020-01-02T00:00:00Z',captured_at='2020-01-02T00:00:00Z') for k in self.cfg['weights']}
 def test_missing_not_neutral_or_renormalized(self):
  x=w.score(self.cfg,{},'2020-01-03T00:00:00Z');self.assertIsNone(x['score']);self.assertEqual(x['score_bounds'],[-1,1]);self.assertIsNone(x['probability'])
 def test_full_is_not_probability(self):
  x=w.score(self.cfg,self.features(),'2020-01-03T00:00:00Z');self.assertAlmostEqual(x['score'],.5);self.assertIsNone(x['probability'])
 def test_future_evidence_excluded(self):
  x=self.features();x['market']['captured_at']='2021-01-01T00:00:00Z';self.assertIn('market',w.score(self.cfg,x,'2020-01-03T00:00:00Z')['missing_weights'])
 def test_duplicate_evidence_rejected(self):
  x=self.features();x['theme']['evidence_ids']=['market']
  with self.assertRaises(ValueError):w.score(self.cfg,x,'2020-01-03T00:00:00Z')
 def test_version_not_retroactive(self):
  with self.assertRaises(ValueError):w.score(self.cfg,{},'2019-01-01T00:00:00Z')
 def test_bad_weights_rejected(self):
  self.cfg['weights']['market']=float('nan')
  with self.assertRaises(ValueError):w.validate(self.cfg)
 def test_registry_immutable(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'cfg.json';p.write_text(json.dumps(self.cfg));c=lab.connect(Path(d)/'test.db');w.register(c,p);w.register(c,p)
   self.cfg['status']='changed';p.write_text(json.dumps(self.cfg))
   with self.assertRaises(ValueError):w.register(c,p)
   with self.assertRaises(sqlite3.IntegrityError):c.execute('DELETE FROM research_weight_versions')
   c.close()
 def test_no_samples_blocks(self):self.assertEqual(w.propose(self.cfg,[])['state'],'BLOCKED_INSUFFICIENT_ELIGIBLE_RESULTS')
 def test_holdout_cannot_choose_weights(self):
  rows=[]
  for i in range(90):
   day=datetime(2020,1,1,tzinfo=timezone.utc)+timedelta(days=i*3)
   for j in range(3):rows.append(dict(id=len(rows),day=day.date().isoformat(),entry_at=(day+timedelta(days=1)).isoformat(),exit_at=(day+timedelta(days=2)).isoformat(),net_return=j/10,x={k:(j-1 if k=='market' else 0) for k in self.cfg['weights']}))
  a=w.propose(self.cfg,rows);changed=copy.deepcopy(rows)
  for r in changed[-60:]:r['net_return']=-r['net_return']
  b=w.propose(self.cfg,changed);self.assertEqual(a['proposed_weights'],b['proposed_weights']);self.assertNotEqual(a['holdout_challenger'],b['holdout_challenger']);self.assertEqual(a['promotion'],'DISABLED')
if __name__=='__main__':unittest.main()
