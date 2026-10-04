import copy,json,tempfile,unittest,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import lab,operations
from learning import enrichment as e,store,reliability,settlement_queue,experiments,risk,pipeline,trainer,history

class ActiveTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.c=lab.connect(self.root/'db.sqlite');store.schema(self.c)
 def tearDown(self):self.c.close();self.tmp.cleanup()
 def test_flow_units_and_windows(self):
  days=[f'202609{i:02}' for i in range(1,21)];rows=[dict(ts_code='X',trade_date=d,net_amount=1 if i%2 else -1) for i,d in enumerate(days)]
  r=e.flow_metrics(rows,'X',days);self.assertEqual(r['flow_net_cny_20'],0);self.assertEqual(r['flow_positive_ratio_20'],.5);self.assertEqual(r['flow_net_cny_1'],10000)
 def test_flow_missing_not_neutral(self):
  with self.assertRaises(ValueError):e.flow_metrics([], 'X',['20260901'])
 def test_flow_duplicates_rejected(self):
  with self.assertRaises(ValueError):e.series([dict(ts_code='X',trade_date='1',net_amount=2)]*2,'X',['1'],'net_amount')
 def test_index_returns(self):
  ds=[str(i).zfill(2) for i in range(21)];r=e.index_metrics([dict(ts_code='883900.TI',trade_date=d,close=100+i) for i,d in enumerate(ds)],'883900.TI',ds);self.assertAlmostEqual(r['883900_return_20'],.2)
 def test_index_wrong_identity(self):
  with self.assertRaises(ValueError):e.series([dict(ts_code='bad',trade_date='1',close=3)],'X',['1'],'close')
 def test_restore_roundtrip(self):
  store.put(self.c,'x','x',{'value':1});b=self.root/'backup';operations.backup(self.c,b);r=reliability.restore_drill(b);self.assertEqual(r['state'],'VERIFIED');self.assertEqual(r['mode'],'ISOLATED_COPY_ONLY')
 def test_integrity_blob_damage(self):
  i=store.put(self.c,'x','x',{'value':1});r=self.c.execute('select sha256 from artifacts').fetchone();(self.root/'blobs'/r[0]).write_text('broken');self.assertEqual(reliability.integrity(self.c)['state'],'FAILED')
 def test_no_registered_start(self):self.assertEqual(reliability.catchup(self.c,'20260927')['state'],'NO_REGISTERED_START')
 def test_catchup_real_calendar_not_weekday(self):
  store.put(self.c,'pool','p',dict(signal_date='20260924'))
  def q(a,p):return dict(fields=['exchange','cal_date','is_open'],items=[['SSE','20260924',1],['SSE','20260925',0],['SSE','20260926',0],['SSE','20260927',0]])
  with patch.object(pipeline,'run',return_value={'day':'20260924','state':'COMPLETE'}) as run:
   r=reliability.catchup(self.c,'20260927',q);self.assertEqual(run.call_count,1);self.assertEqual(r['remaining'],[])
 def test_catchup_rejects_partial_calendar(self):
  store.put(self.c,'pool','p',dict(signal_date='20260924'))
  with self.assertRaises(ValueError):reliability.catchup(self.c,'20260927',lambda a,p:{'fields':[],'items':[]})
 def test_future_queue_never_settles(self):
  store.put(self.c,'snapshot','s',dict(state='COMPLETE',day='20990101',rows=[{'code':'X'}],entry_at='2099-01-02T09:25:00+08:00',exit_at='2099-01-03T10:00:00+08:00',provenance='PROSPECTIVE_LOCAL'))
  r=settlement_queue.build(self.c);self.assertEqual(len(r['items']),2);self.assertTrue(all(x['state']=='WAIT_ENTRY' for x in r['items']));self.assertFalse(store.get(self.c,'label'))
 def test_ablation_no_data_blocks(self):
  r=experiments.run(self.c);self.assertTrue(all(x['report']['state']=='BLOCKED_INSUFFICIENT_DATA' for x in r['variants']));self.assertFalse(r['promotion']);self.assertEqual(experiments.run(self.c)['id'],r['id'])
 def test_enrichment_late_excluded(self):
  sid=5;store.put(self.c,'enrichment','e',dict(snapshot_id=sid,provenance='RETROSPECTIVE'))
  d={'rows':[dict(snapshot_id=sid,day='20260924',entry_at='2026-09-28T09:25:00+08:00')],'excluded':[]}
  self.assertEqual(experiments.attach(self.c,d)['rows'],[])
 def test_holm_controls_family(self):self.assertEqual(experiments.holm({'a':.001,'b':.03,'c':.04}),{'a':True,'b':False,'c':False})
 def test_bad_pvalue(self):
  with self.assertRaises(ValueError):experiments.holm({'a':-1})
 def test_tail_mean(self):self.assertEqual(risk.expected_shortfall(list(range(100))),2)
 def test_risk_insufficient_blocks(self):self.assertIn('MULTIPLE_COMPARISON_NOT_CONFIRMED',risk.assess({'days':[]})['reasons'])
 def test_forward_shadow_retry_same_id(self):
  key=store.digest([8,9]);i=store.put(self.c,'shadow',key,{'state':'SHADOW_ONLY'});self.assertEqual(trainer.shadow(self.c,8,9),i)
 def test_endpoint_cap(self):
  with self.assertRaises(ValueError):pipeline.fetch(self.c,'ths_daily',{},lambda a,p:dict(fields=['x'],items=[[1]]*3000))
 def test_field_variants_do_not_share_cache(self):
  calls=[]
  def q(a,p,f=''):calls.append(f);return dict(fields=['x'],items=[[1]])
  pipeline.fetch(self.c,'x',{},q,fields='one');pipeline.fetch(self.c,'x',{},q,fields='two');self.assertEqual(calls,['one','two'])
 def test_sector_money_units_and_tied_rank(self):
  ds=[str(i) for i in range(20)];rows=[dict(ts_code=c,trade_date=d,net_amount=1,name=c) for c in ['A','B'] for d in ds]
  # chronological strings must be sorted just like YYYYMMDD
  r=e.sector_summary(rows,sorted(ds));self.assertEqual(len(r['complete_series']),2)
  self.assertEqual(r['complete_series'][0]['windows']['20']['net_cny'],2e9)
  self.assertTrue(all(x['windows']['5']['rank']==1 for x in r['complete_series']))
 def test_history_does_not_extend_live_start(self):
  store.put(self.c,'pool','old',dict(signal_date='20260101'))
  store.put(self.c,'snapshot','live',dict(day='20260924',provenance='PROSPECTIVE_LOCAL',state='COMPLETE'))
  calls=[]
  def q(a,p):calls.append(p);return dict(fields=['exchange','cal_date','is_open'],items=[['SSE','20260924',1]])
  reliability.catchup(self.c,'20260924',q);self.assertEqual(calls[0]['start_date'],'20260924')
 def test_limit_queue_missing_quotes(self):
  store.put(self.c,'snapshot','s',dict(state='COMPLETE',day='20250101',rows=[{'code':'X'}],entry_at='2025-01-02T09:25:00+08:00',exit_at='2025-01-03T10:00:00+08:00',provenance='PROSPECTIVE_LOCAL'))
  r=settlement_queue.build(self.c);self.assertEqual({x['state'] for x in r['items']},{'MISSING_QUOTES','MISSING_BROKER_EVIDENCE'})
 def test_lifecycle_retry_and_reentry(self):
  m=store.put(self.c,'model','m',{'state':'TRAINED_RESEARCH_ONLY'})
  a=store.lifecycle(self.c,m,'SHADOW','approved');self.assertEqual(a,store.lifecycle(self.c,m,'SHADOW','approved'))
  store.lifecycle(self.c,m,'ROLLBACK','drift');b=store.lifecycle(self.c,m,'SHADOW','approved');self.assertGreater(b,a)
 def test_optional_feature_train_registry(self):
  rows=[{'x':{'extra':float(i),'return_5':float(i)}} for i in range(5)]
  self.assertEqual(trainer.preprocess_fit(rows,.5,['extra'])['keys'],['extra'])
  self.assertNotIn('extra',trainer.preprocess_fit(rows,.5)['keys'])
