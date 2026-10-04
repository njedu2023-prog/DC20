import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import lab
from learning import settlement_queue as q,store,pipeline,reconciliation as rec

class QueueTests(unittest.TestCase):
 def snapshot(self,**kw):
  s=dict(id=1,day='20260101',method='daily_net_return_v1',pool_id=1,cutoff='2026-01-01T22:00:00+08:00',captured_at='2026-01-01T22:00:00+08:00',_recorded_at='2026-01-01T22:00:01+08:00',entry_at='2026-01-02T09:25:00+08:00',exit_at='2026-01-05T10:00:00+08:00',provenance='PROSPECTIVE_LOCAL',state='INCOMPLETE',expected_count=2,rows=[dict(code='A',state='VALID_NUMERIC_BASELINE'),dict(code='B',state='MISSING',quality={'error':'SHORT_HISTORY'})]);s.update(kw);return s
 def build(self,s=None,labels=None):
  data={'snapshot':s or [self.snapshot()],'label':labels or [],'capture':[]}
  with patch.object(store,'get',side_effect=lambda c,k:data[k]),patch.object(lab,'now',return_value='2026-01-06T22:00:00+08:00'):return q.build(None)
 def test_incomplete_all_members_remain(self):
  x=self.build();self.assertEqual(len(x['items']),4);self.assertEqual({r['code'] for r in x['items']},{'A','B'})
 def test_missing_label_remains_retryable(self):
  x=self.build(labels=[dict(id=8,snapshot_id=1,code='A',basis='QUOTE_PROXY',status='MISSING')]);r=next(r for r in x['items'] if r['code']=='A' and r['basis']=='QUOTE_PROXY');self.assertEqual(r['draft']['review']['supersedes'],8)
 def test_old_out_of_scenario_reopens_review(self):
  x=self.build(labels=[dict(id=8,snapshot_id=1,code='A',basis='QUOTE_PROXY',status='OUT_OF_SCENARIO')]);self.assertEqual(len(x['items']),4)
 def test_revision_selection_not_based_on_outcomes(self):
  ss=[self.snapshot(),self.snapshot(id=2,state='COMPLETE',cutoff='2026-01-01T23:00:00+08:00')];self.assertEqual([x['id'] for x in q.selected_snapshots(ss)],[2])
 def test_capture_incomplete_pool(self):
  with patch.object(store,'get',return_value=[self.snapshot()]),patch.object(store,'put',return_value=1),patch.object(lab,'now',return_value='2026-01-03T22:00:00+08:00'),patch.object(pipeline,'fetch',return_value={'id':4,'data':{'fields':[],'items':[]}}) as f:
   r=pipeline.capture_due(None);self.assertEqual(f.call_count,2);self.assertEqual({x['code'] for x in r['jobs']},{'A','B'})
 def test_time_and_pool_link_contract(self):
  s=self.snapshot();p=dict(signal_date='20260101',strategy=lab.STRATEGY,cost=.0045,entry_at=s['entry_at'],exit_at=s['exit_at'],cutoff='2026-01-01T23:00:00+08:00');payload={'entry_scenario_id':'GAP_0_3'}
  self.assertTrue(rec.compatible(p,payload,s,{'A','B'}))
  self.assertFalse(rec.compatible(p,payload,s,{'A'}))
  p['cutoff']='2026-01-01T21:00:00+08:00';self.assertFalse(rec.compatible(p,payload,s,{'A','B'}))
 def test_empty_report(self):
  with tempfile.TemporaryDirectory() as tmp:
   c=lab.connect(Path(tmp)/'r.db');r=rec.report(c);self.assertEqual(r['rows'],[]);self.assertEqual(r['comparisons'],[]);c.close()

class ScenarioAuditTests(unittest.TestCase):
 def test_raw_exclusion_idempotent_and_not_settled(self):
  from learning import quote_review
  with tempfile.TemporaryDirectory() as tmp:
   c=lab.connect(Path(tmp)/'r.db');s=QueueTests().snapshot();s['rows']=s['rows'][:1];s['expected_count']=1
   sid=store.put(c,'snapshot','s',{k:v for k,v in s.items() if k not in ('id','_recorded_at')})
   rid=store.put(c,'raw','auction',{'api':'stk_auction','data':{'fields':['ts_code','trade_date','price','pre_close','vol'],'items':[['A','20260102',11,10,1000]]}})
   store.put(c,'capture','c',{'jobs':[{'code':'A','at':s['entry_at'],'leg':'ENTRY','raw_id':rid,'state':'CAPTURED_UNREVIEWED'}]})
   with patch.object(lab,'now',return_value='2026-01-03T22:00:00+08:00'):
    r=quote_review.run(c);quote_review.run(c)
   ls=store.get(c,'label');self.assertEqual(len(ls),0);self.assertEqual(r['rows'][0]['state'],'NEEDS_EXECUTION_REVIEW');self.assertEqual(r['automatic_settlements'],0)
   c.close()
 def test_in_scenario_not_excluded(self):
  from learning import quote_review
  with patch.object(store,'raw',return_value={'api':'stk_auction','data':{'fields':['ts_code','trade_date','price','pre_close','vol'],'items':[['A','20260102',10.2,10,1000]]}}):
   _,gap=quote_review.auction(None,1,'A','2026-01-02T09:25:00+08:00');self.assertAlmostEqual(gap,.02)
