import copy,json,math,sys,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import lab
from learning import store,features,pipeline,trainer

class StoreTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.c=lab.connect(Path(self.tmp.name)/'db.sqlite');store.schema(self.c)
 def tearDown(self):self.c.close();self.tmp.cleanup()
 def test_immutable_and_idempotent(self):
  i=store.put(self.c,'x','key',{'a':1});self.assertEqual(i,store.put(self.c,'x','key',{'a':1}))
  with self.assertRaises(ValueError):store.put(self.c,'x','key',{'a':2})
  with self.assertRaises(Exception):self.c.execute('delete from learning_records')
 def test_corrupt_blob(self):
  i=store.put(self.c,'x','key',{'a':1});r=self.c.execute('select a.sha256 from artifacts a join learning_records l on l.artifact_id=a.id where l.id=?',(i,)).fetchone();(Path(self.tmp.name)/'blobs'/r[0]).write_text('broken')
  with self.assertRaises(ValueError):store.raw(self.c,i)
 def test_empty_block(self):
  r=trainer.train(self.c,'daily_net_return_v1','QUOTE_PROXY');self.assertEqual(r['state'],'BLOCKED_INSUFFICIENT_DATA');self.assertEqual(r['eligible_rows'],0)
  self.assertEqual(r['id'],trainer.train(self.c,'daily_net_return_v1','QUOTE_PROXY')['id'])
 def test_policy_immutable(self):
  p=store.register_policy(self.c);p['cost']=0
  with self.assertRaises(ValueError):store.put(self.c,'policy',p['version'],p)
 def test_no_upgrade_untrained(self):
  i=store.put(self.c,'model','m',{'state':'BLOCKED_INSUFFICIENT_DATA'})
  with self.assertRaises(ValueError):store.lifecycle(self.c,i,'SHADOW','test')
  with self.assertRaises(ValueError):store.lifecycle(self.c,i,'PROMOTE','test')
 def test_labels_future_rejected(self):
  i=store.put(self.c,'snapshot','s',{'rows':[{'code':'000001.SZ'}],'entry_at':'2099-01-01T09:25:00+08:00','exit_at':'2099-01-02T10:00:00+08:00'})
  with self.assertRaises(ValueError):store.label(self.c,i,'000001.SZ','QUOTE_PROXY','SETTLED',{'reviewer':'test','reason':'test','evidence_ids':[i]})
 def test_basis_invalid(self):
  with self.assertRaises(ValueError):store.dataset(self.c,'m','MIXED')
 def test_raw_cache_resume(self):
  calls=[]
  def q(api,p):calls.append(p);return {'fields':['x'],'items':[[1]]}
  a=pipeline.fetch(self.c,'daily',{'day':'20260924'},q);b=pipeline.fetch(self.c,'daily',{'day':'20260924'},q)
  self.assertEqual(a['id'],b['id']);self.assertEqual(len(calls),1)
 def test_empty_not_permanent_cache(self):
  n=[]
  def q(a,p):n.append(1);return {'fields':['x'],'items':[]}
  pipeline.fetch(self.c,'daily',{},q);pipeline.fetch(self.c,'daily',{},q);self.assertEqual(len(n),2)
 def test_truncation(self):
  with self.assertRaises(ValueError):pipeline.fetch(self.c,'daily',{},lambda a,p:{'fields':['x'],'items':[[1]]*5000})

class FeatureTests(unittest.TestCase):
 def fixture(self):
  dates=[(datetime(2026,1,1)+timedelta(days=i)).strftime('%Y%m%d') for i in range(80)]
  a=[{'ts_code':'000001.SZ','trade_date':d,'open':10+i*.1,'close':10+i*.1,'high':11+i*.1,'low':9+i*.1,'vol':100+i} for i,d in enumerate(dates)];adj=[dict(ts_code='000001.SZ',trade_date=d,adj_factor=1) for d in dates];return dates,a,adj
 def test_reproducible(self):
  ds,a,adj=self.fixture();x,meta=features.compute(a,adj,[],[],'000001.SZ',ds[-1],ds);self.assertAlmostEqual(x['return_5'],17.9/17.4-1);self.assertIsNone(x['turnover']);self.assertEqual(meta['n'],80)
 def test_wrong_or_future_bar(self):
  ds,a,adj=self.fixture();a[-1]['ts_code']='wrong'
  with self.assertRaises(ValueError):features.compute(a,adj,[],[],'000001.SZ',ds[-1],ds)
 def test_missing_session(self):
  ds,a,adj=self.fixture();a.pop(-20)
  with self.assertRaises(ValueError):features.compute(a,adj,[],[],'000001.SZ',ds[-1],ds)
 def test_missing_adj(self):
  ds,a,adj=self.fixture()
  with self.assertRaises(ValueError):features.compute(a,adj[:-1],[],[],'000001.SZ',ds[-1],ds)
 def test_split_adjustment(self):
  ds,a,adj=self.fixture()
  for i in range(40,80):
   adj[i]['adj_factor']=2
   for k in ('open','close','high','low'):a[i][k]/=2
  x,_=features.compute(a,adj,[],[],'000001.SZ',ds[-1],ds);self.assertLess(abs(x['return_20']),.2)
 def test_pool_dynamic(self):
  for n in (2,9,20,31):pipeline.validate_pool({'signal_date':'20260924','expected_count':n,'candidates':[{'code':f'{i:06d}.SZ','board_stage':2} for i in range(n)]},'20260924')
 def test_pool_date_count_duplicate(self):
  p={'signal_date':'20260924','expected_count':2,'candidates':[{'code':'000001.SZ','board_stage':2}]*2}
  with self.assertRaises(ValueError):pipeline.validate_pool(p,'20260924')
 def test_bad_response(self):
  with self.assertRaises(ValueError):features.records({'fields':['a'],'items':[[1,2]]})

class TrainerTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  try:trainer.npmod()
  except ImportError:raise unittest.SkipTest('Run bundled Python with NumPy for training tests')
 def data(self,n=130):
  rs=[]
  for i in range(n):
   date=datetime(2025,1,1)+timedelta(days=i)
   for j in range(4):
    x=(j-1.5)*.02+math.sin(i)*.01
    rs.append(dict(day=date.strftime('%Y%m%d'),code=f'{j:06d}.SZ',entry_at=(date+timedelta(days=1)).replace(hour=9,minute=25,tzinfo=timezone.utc).isoformat(),exit_at=(date+timedelta(days=2)).replace(hour=10,tzinfo=timezone.utc).isoformat(),x={k:x*(z+1) for z,k in enumerate(features.FEATURES)},status='SETTLED',gross=x+.005,official_rank=j+1))
  return rs
 def policy(self):return json.loads((store.ROOT/'policy.json').read_text())
 def test_purge(self):
  tr,ca,te=trainer.split(self.data(),self.policy());self.assertLess(max(lab.stamp(r['exit_at']) for r in tr),min(lab.stamp(r['entry_at']) for r in ca));self.assertLess(max(lab.stamp(r['exit_at']) for r in ca),min(lab.stamp(r['entry_at']) for r in te))
 def test_train_only_transform(self):
  r=self.data(10);pp=trainer.preprocess_fit(r,.5);before=copy.deepcopy(pp);r[0]['x']['return_5']=1000;trainer.transform(r,pp);self.assertEqual(pp,before)
 def test_all_missing_rejected(self):
  r=self.data(2)
  for x in r:x['x']={}
  with self.assertRaises(ValueError):trainer.preprocess_fit(r,.5)
 def test_ties(self):self.assertEqual(trainer.select([{'code':str(i),'p':.5} for i in range(4)],'p'),{str(i):.5 for i in range(4)})
 def test_full_training_and_no_promotion(self):
  data={'rows':self.data(),'basis':'QUOTE_PROXY','fingerprint':'SYNTHETIC_TEST_ONLY','excluded':[]};r=trainer.fit_dataset(data,self.policy());self.assertEqual(r['state'],'TRAINED_RESEARCH_ONLY');self.assertEqual(r['promotion'],'NEW_FORWARD_SHADOW_REQUIRED');self.assertTrue(r['folds']);ps=r['folds'][-1]['predictions'];self.assertTrue(all(0<p['profit_probability']<1 for p in ps));self.assertIn('official_top2',r['folds'][-1]['evaluation']['gains'])
 def test_test_labels_do_not_fit(self):
  rows=self.data();tr,ca,te=trainer.split(rows,self.policy());a=trainer.train_bundle(tr,ca,self.policy())
  for r in te:r['gross']=99
  b=trainer.train_bundle(tr,ca,self.policy());self.assertEqual(a,b)
 def test_train_minimum(self):
  r=trainer.fit_dataset({'rows':self.data(5),'basis':'ACTUAL','fingerprint':'test','excluded':[]},self.policy());self.assertEqual(r['state'],'BLOCKED_INSUFFICIENT_DATA')
 def test_single_class_rejected(self):
  tr,ca,te=trainer.split(self.data(),self.policy())
  for r in tr:r['gross']=1
  with self.assertRaises(ValueError):trainer.train_bundle(tr,ca,self.policy())

if __name__=='__main__':unittest.main()

class ReviewedLabelTests(unittest.TestCase):
 setUp=StoreTests.setUp
 tearDown=StoreTests.tearDown
 def fixture_label(self):
  s=dict(rows=[dict(code='000001.SZ')],entry_at='2026-01-05T09:25:00+08:00',exit_at='2026-01-06T10:00:00+08:00')
  sid=store.put(self.c,'snapshot','s',s)
  e=store.put(self.c,'raw','e',dict(api='stk_auction',data=dict(fields=['ts_code','trade_date','price','pre_close'],items=[['000001.SZ','20260105',10.1,10]])))
  x=store.put(self.c,'raw','x',dict(api='stk_mins',data=dict(fields=['ts_code','trade_time','close'],items=[['000001.SZ','2026-01-06 10:00:00',10.5]])))
  r=dict(reviewer='test',reason='synthetic only',evidence_ids=[e,x],entry_at=s['entry_at'],exit_at=s['exit_at'],entry_price=10.1,exit_price=10.5,previous_close=10,kind='REVIEWED_EXECUTABLE_QUOTE',entry_executable=True,exit_executable=True,corporate_action_checked=True,price_semantics='AUCTION_PRICE_AND_1000_MINUTE_CLOSE_PROXY')
  return sid,r
 def test_large_gap_can_settle_only_with_execution_review(self):
  for price in (9,11):
   sid,r=self.fixture_label()
   eid=store.put(self.c,'raw','gap'+str(price),dict(api='stk_auction',data=dict(fields=['ts_code','trade_date','price','pre_close'],items=[['000001.SZ','20260105',price,10]])))
   r['entry_price']=price;r['evidence_ids'][0]=eid
   previous=store.get(self.c,'label')
   if previous:r['supersedes']=previous[-1]['id']
   r['entry_executable']=False
   with self.assertRaisesRegex(ValueError,'NO_EXECUTION_REVIEW'):store.label(self.c,sid,'000001.SZ','QUOTE_PROXY','SETTLED',r)
   r['entry_executable']=True
   lid=store.label(self.c,sid,'000001.SZ','QUOTE_PROXY','SETTLED',r)
   self.assertAlmostEqual(store.raw(self.c,lid)['gross_return'],10.5/price-1)
 def test_price_mismatch_rejected(self):
  sid,r=self.fixture_label();r['exit_price']=11
  with self.assertRaisesRegex(ValueError,'RAW_PRICE_BINDING'):store.label(self.c,sid,'000001.SZ','QUOTE_PROXY','SETTLED',r)
 def test_quote_not_actual(self):
  sid,r=self.fixture_label();r['kind']='ORDER_FILL'
  with self.assertRaisesRegex(ValueError,'BROKER_FILL'):store.label(self.c,sid,'000001.SZ','ACTUAL','SETTLED',r)
 def test_correction_explicit(self):
  sid,r=self.fixture_label();i=store.label(self.c,sid,'000001.SZ','QUOTE_PROXY','SETTLED',r)
  with self.assertRaisesRegex(ValueError,'CORRECTION'):store.label(self.c,sid,'000001.SZ','QUOTE_PROXY','SETTLED',r)
  r['supersedes']=i;r['reason']='reviewed correction';j=store.label(self.c,sid,'000001.SZ','QUOTE_PROXY','SETTLED',r);self.assertGreater(j,i)
