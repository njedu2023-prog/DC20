import importlib.util,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from contract_fixture import attach
spec=importlib.util.spec_from_file_location('lab',Path(__file__).resolve().parents[1]/'lab.py');lab=importlib.util.module_from_spec(spec);spec.loader.exec_module(lab)
class EvidenceTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.c=lab.connect(self.root/'db.sqlite3')
 def tearDown(self):self.c.close();self.tmp.cleanup()
 def file(self,name,data):
  p=self.root/name;p.write_text(json.dumps(data));return p
 def forecast(self,**kwargs):
  d=dict(signal_date='20260924',entry_at='2026-09-28T09:25:00+08:00',exit_at='2026-09-29T10:00:00+08:00',information_cutoff='2026-09-24T15:00:00+08:00',latest_data_available_at='2026-09-24T21:00:00+08:00',calendar_evidence='SSE published calendar snapshot',strategy=lab.STRATEGY,model='test-v1',cost=.0045,entry_scenario='0-3%',predictions=[dict(code='002909.SZ',name='集泰股份',p=.6,reason='test only',data_sources=['snapshot'])]);d.update(kwargs);return attach(self.c,self.root,d)
 def freeze(self,**kwargs):
  p=self.file('forecast.json',self.forecast(**kwargs))
  with patch.object(lab,'now',return_value='2026-09-24T22:00:00+08:00'):lab.freeze(self.c,p)
  return p
 def settle(self,**kwargs):
  d=dict(prediction_id=1,basis='QUOTE_PROXY',status='SETTLED',entry_price=10,exit_price=10.5,entry_at='2026-09-28T09:25:00+08:00',exit_at='2026-09-29T10:00:00+08:00',evidence='test sample only');d.update(kwargs)
  ref=__import__('json').loads(self.c.execute('select payload from predictions where id=1').fetchone()[0])['candidate_pool']
  d['execution_review']=dict(basis=d['basis'],reviewer='test only',previous_close=10,**{leg:dict(**ref,instrument='002909.SZ',at=d[leg+'_at'],price=d[leg+'_price'],executable=True,rationale='synthetic only',kind='REVIEWED_EXECUTABLE_QUOTE' if d['basis']=='QUOTE_PROXY' else 'ORDER_FILL') for leg in ('entry','exit')})
  with patch.object(lab,'now',return_value='2026-09-29T11:00:00+08:00'):lab.outcome(self.c,self.file('outcome'+str(kwargs.get('supersedes',''))+'.json',d))
 def test_immutable(self):
  self.freeze()
  for sql in ['UPDATE predictions SET p=.9','DELETE FROM predictions']:
   with self.assertRaises(sqlite3.IntegrityError):self.c.execute(sql)
 def test_freeze_idempotence(self):
  self.freeze();self.freeze();self.assertEqual(self.c.execute('select count(*) from predictions').fetchone()[0],1)
 def test_version_change_rejected(self):
  self.freeze()
  with self.assertRaises(ValueError):self.freeze(cost=.008)
 def test_late_freeze_rejected(self):
  p=self.file('late.json',self.forecast())
  with patch.object(lab,'now',return_value='2026-09-29T00:00:00+08:00'),self.assertRaises(ValueError):lab.freeze(self.c,p)
 def test_future_data_rejected(self):
  with self.assertRaises(ValueError):self.freeze(latest_data_available_at='2026-09-28T12:00:00+08:00')
 def test_wrong_exit_rejected(self):
  self.freeze()
  with self.assertRaises(ValueError):self.settle(exit_at='2026-09-29T15:00:00+08:00')
 def test_future_settlement_rejected(self):
  self.freeze();d=dict(prediction_id=1,basis='QUOTE_PROXY',status='SETTLED',entry_price=10,exit_price=10.5,entry_at='2026-09-28T09:25:00+08:00',exit_at='2026-09-29T10:00:00+08:00',evidence='test')
  with patch.object(lab,'now',return_value='2026-09-25T00:00:00+08:00'),self.assertRaises(ValueError):lab.outcome(self.c,self.file('future.json',d))
 def test_old_probability_not_calibrated_outside_original_window(self):
  self.freeze();self.settle(entry_price=11)
  r=__import__('learning.reconciliation',fromlist=['report']).report(self.c)['rows'][0]['outcomes']['QUOTE_PROXY']
  self.assertEqual(r['status'],'SETTLED');self.assertFalse(r['probability_eligible'])
  self.assertEqual(lab.evaluate(self.c,'test-v1','QUOTE_PROXY')['n'],0)
 def test_cost_and_basis(self):
  self.freeze();self.settle();e=lab.evaluate(self.c,'test-v1','QUOTE_PROXY');self.assertAlmostEqual(e['mean_net_return'],.0455);self.assertAlmostEqual(e['brier'],.16);self.assertEqual(lab.evaluate(self.c,'test-v1','ACTUAL')['n'],0)
 def test_unfilled_excluded(self):
  self.freeze();self.settle(status='UNFILLED',entry_price=None,exit_price=None);self.assertEqual(lab.evaluate(self.c,'test-v1','QUOTE_PROXY')['n'],0)
 def test_correction_append_only(self):
  self.freeze();self.settle();self.settle(exit_price=9,supersedes=1);e=lab.evaluate(self.c,'test-v1','QUOTE_PROXY');self.assertEqual(e['n'],1);self.assertEqual(e['win_rate'],0);self.assertEqual(self.c.execute('select count(*) from outcomes').fetchone()[0],2)
 def test_correction_requires_reference(self):
  self.freeze();self.settle()
  with self.assertRaises(ValueError):self.settle(exit_price=9)
 def test_historical_not_scored(self):
  ar=self.root/'archive';day=ar/'20260924';day.mkdir(parents=True);(day/'probabilities-v1.json').write_text(json.dumps({'profit_percent':{'002909.SZ':60}}));lab.import_archive(self.c,ar);r=self.c.execute('select * from predictions').fetchone();self.assertEqual(r['provenance'],'LEGACY_UNVERIFIED_FREEZE');self.assertEqual(lab.evaluate(self.c,r['model'],'QUOTE_PROXY')['n'],0)
 def test_source_bytes_retained(self):
  p=self.file('raw.json',{'a':1});lab.artifact(self.c,p);p.write_text('{"a":2}');lab.artifact(self.c,p);self.assertEqual(len(list((self.root/'blobs').iterdir())),2)
 def test_invalid_probability(self):
  d=self.forecast();d['predictions'][0]['p']=1.1
  with patch.object(lab,'now',return_value='2026-09-24T22:00:00+08:00'),self.assertRaises(ValueError):lab.freeze(self.c,self.file('bad.json',d))
 def test_linked_learning_result_basis_separated(self):
  self.freeze()
  linked={'rows':[{'prediction_id':1,'rank_eligible':True,'outcomes':{'QUOTE_PROXY':{'status':'SETTLED','entry_price':10,'exit_price':10.5},'ACTUAL':{'status':'MISSING'}}}]}
  self.assertEqual(lab.evaluate(self.c,'test-v1','QUOTE_PROXY',linked)['n'],1)
  self.assertEqual(lab.evaluate(self.c,'test-v1','ACTUAL',linked)['n'],0)
 def test_informationless_prediction_not_calibrated(self):
  self.freeze()
  linked={'rows':[{'prediction_id':1,'rank_eligible':False,'outcomes':{'QUOTE_PROXY':{'status':'SETTLED','entry_price':10,'exit_price':10.5}}}]}
  self.assertEqual(lab.evaluate(self.c,'test-v1','QUOTE_PROXY',linked)['n'],0)
if __name__=='__main__':unittest.main()
