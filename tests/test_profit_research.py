import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts import build_profit_research as b
from scripts.publish_profit_research import allowed, sources_unchanged

class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.model=patch.object(b,'model_view',return_value={'sha256':b.MODEL_SHA});self.model.start();self.addCleanup(self.model.stop)
        self.day='20260914';self.code='000001.SZ'
        self.p0={'exec_date':'20260915','exit_date':'20260916','rows':[{'ts_code':self.code,'promotion_rank':1,'name':'<script>secret</script>'}]}
        pb=self.put('outputs/decision/three_rank_top10_20260914.json',self.p0)
        self.snap={'signal_date':self.day,'D_source_evidence':{'projection':{'rows':[{'ts_code':self.code,'promotion_rank':1,'board_stage':2,'ret_2d':.2}]}}}
        self.sp=f'{b.SNAPS}/day_{self.day}.json';sb=self.put(self.sp,self.snap)
        self.public={'signal_date':self.day,'model_canonical_sha256':b.MODEL_SHA,
          'snapshot_source':{'path':self.sp,'sha256':sb},'p0_source':{'sha256':pb},
          'pre_cas_freeze_at_utc':'2026-09-14T12:00:00Z','exec_date':'20260915','exit_date':'20260916',
          'rows':[{'ts_code':self.code,'candidate_rank':1,'candidate_score':-.01}]}
        self.dp='outputs/decision/candidate_profit_v1/day_20260914.json';dh=self.put(self.dp,self.public)
        self.index={'days':[{'signal_date':self.day,'path':self.dp,'sha256':dh}]};self.save_index()
        self.summary={'as_of_date':'20260916','model_canonical_sha256':b.MODEL_SHA,'entry_policy_id':'entry','exit_policy_id':'exit','round_trip_cost_rate':.0045,
          'groups':{'candidate_top1':{'daily_sequence':[{'signal_date':self.day,'ts_code':self.code,'snapshot_file_sha256':sb,
           'status':'SETTLED_1000_LIMIT_HOLD_MINUTE_PROXY','slot_net_return':.03,'proxy_fill':1,'label_available_date':'20260916'}]}}}
        self.save_summary()
        p=self.root/'data/market/trade_cal_sse.csv';p.parent.mkdir(parents=True);p.write_text('cal_date,is_open\n20260914,1\n20260915,0\n20260916,0\n')
    def put(self,p,x):
        f=self.root/p;f.parent.mkdir(parents=True,exist_ok=True);raw=b.encoded(x);f.write_bytes(raw);return b.sha(raw)
    def save_summary(self):self.put('outputs/decision/candidate_profit_v1/summary.json',self.summary)
    def save_index(self):self.put('outputs/decision/candidate_profit_v1/index.json',self.index)
    def doc(self):return b.build(self.root,'a'*40)[0]
    def test_ready_pair_and_day(self):
        d=self.doc();self.assertEqual(d['counts']['ready_pairs'],1);self.assertEqual(d['counts']['mature_training_days'],1)
    def test_pending_never_zero(self):
        r=self.summary['groups']['candidate_top1']['daily_sequence'][0];r.update(status='PENDING_T1',slot_net_return=0)
        self.save_summary();d=self.doc();self.assertIsNone(d['days'][0]['records'][0]['outcome']['net_return']);self.assertEqual(d['counts']['ready_pairs'],0)
    def test_future_label_excluded(self):
        self.summary['groups']['candidate_top1']['daily_sequence'][0]['label_available_date']='20260917';self.save_summary()
        self.assertEqual(self.doc()['counts']['ready_pairs'],0)
    def test_no_fill_valid_slot_not_fill(self):
        r=self.summary['groups']['candidate_top1']['daily_sequence'][0];r.update(status='NO_FILL_CAPACITY',proxy_fill=0,slot_net_return=0)
        self.save_summary();self.assertEqual(self.doc()['counts']['ready_pairs'],1)
    def test_unknown_no_fill_rejected(self):
        self.summary['groups']['candidate_top1']['daily_sequence'][0].update(status='NO_FILL_MADE_UP',slot_net_return=0);self.save_summary()
        self.assertEqual(self.doc()['counts']['ready_pairs'],0)
    def test_inconsistent_no_fill_rejected(self):
        self.summary['groups']['candidate_top1']['daily_sequence'][0].update(status='NO_FILL_CAPACITY',proxy_fill=0,slot_net_return=.1);self.save_summary()
        self.assertTrue(self.doc()['issues'])
    def test_counterfactual_binding_rejected(self):
        self.summary['groups']={};self.save_summary()
        self.put(b.OUT+'/labels/'+self.day+'/'+self.code+'.json',{'schema_version':'dc20_research_counterfactual_label_v1','snapshot_sha256':'bad'})
        self.assertTrue(self.doc()['issues'])
    def test_snapshot_tamper(self):
        self.put(self.sp,{**self.snap,'extra':1});d=self.doc();self.assertEqual(d['days'][0]['provenance'],'SOURCE_ERROR')
    def test_public_tamper(self):
        self.put(self.dp,{**self.public,'extra':1});self.assertTrue(self.doc()['issues'])
    def test_outcome_binding_rejected(self):
        self.summary['groups']['candidate_top1']['daily_sequence'][0]['snapshot_file_sha256']='f'*64;self.save_summary()
        self.assertTrue(self.doc()['issues'])
    def test_retrospective_not_formal(self):
        self.index['days']=[];self.save_index();self.summary['groups']={};self.save_summary()
        d=self.doc();self.assertEqual(d['days'][0]['provenance'],'RETROSPECTIVE_UNVERIFIED');self.assertEqual(d['counts']['ready_pairs'],0)
    def test_unknown_sensitive_fields_not_exported(self):
        self.p0['rows'][0]['token']='fake-secret';self.put('outputs/decision/three_rank_top10_20260914.json',self.p0)
        self.index['days']=[];self.save_index();self.summary['groups']={};self.save_summary()
        self.assertNotIn('fake-secret',json.dumps(self.doc()))
    def test_missing_all_candidate_label_blocks_day(self):
        self.summary['groups']={};self.save_summary();d=self.doc()
        self.assertEqual(d['counts']['missing_counterfactual_labels'],1);self.assertEqual(d['counts']['mature_training_days'],0)
    def test_no_training_or_promotion(self):
        gate=self.doc()['training_gate'];self.assertFalse(gate['training_enabled']);self.assertFalse(gate['automatic_promotion_enabled'])
    def test_immutable_snapshot_and_idempotence(self):
        b.write(self.root,'a'*40);b.write(self.root,'a'*40)
        path=self.root/b.OUT/'snapshots'/self.day/(self.code+'.json');path.write_text('{}')
        with self.assertRaisesRegex(ValueError,'IMMUTABLE'):b.write(self.root,'a'*40)
    def test_write_scope(self):
        self.assertTrue(allowed(b.OUT+'/labels/20260914/000001.SZ.json'))
        for p in ['decision.html','models/foo.json',b.OUT+'/../../evil.json',b.OUT+'/index.html']:
            self.assertFalse(allowed(p))
    def test_safe_unrelated_append(self):
        diff={'status':'ahead','total_commits':1,'files':[{'filename':'outputs/decision/primary_observation/summary.json'}]}
        self.assertTrue(sources_unchanged(diff,{'outputs/decision/candidate_profit_v1/summary.json'}))
    def test_source_or_research_change_rejects_rebase(self):
        for path in ['scripts/build_profit_research.py',b.OUT+'/latest.json','bound.json']:
            self.assertFalse(sources_unchanged({'status':'ahead','files':[{'filename':path}]},{'bound.json'}))
    def test_truncated_or_divergent_diff_rejected(self):
        self.assertFalse(sources_unchanged({'status':'diverged','files':[]},set()))
        self.assertFalse(sources_unchanged({'status':'ahead','files':[{'filename':'x'}]*300},set()))
    def test_public_html_contract(self):
        html=Path('outputs/decision/profit_research/index.html').read_text()
        for text in ['id="year"','id="month"','id="from"','id="to"','crypto.subtle.digest','模型透明档案','textContent']:
            self.assertIn(text,html)
        self.assertNotIn('innerHTML=JSON.stringify',html)
    def test_workflow_isolated(self):
        y=Path('.github/workflows/profit_research.yml').read_text()
        self.assertIn('continue-on-error: true',y);self.assertNotIn('git push',y);self.assertNotIn('pytest',y)
        self.assertIn('persist-credentials: false',y);self.assertIn('20 15 * * 1-5',y)

if __name__=='__main__':unittest.main()
