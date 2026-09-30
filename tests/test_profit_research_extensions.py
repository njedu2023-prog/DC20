import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from scripts import profit_research_extensions as x
from scripts.publish_profit_research import allowed,sources_unchanged

class ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.day='20260929';self.code='000678.SZ';self.now='2026-09-30T03:00:00+00:00'
        self.rt=f'outputs/decision/primary_d_runtime_features_{self.day}.csv'
        self.rc=f'outputs/decision/primary_d_receipt_{self.day}.json'
        self.p0=f'outputs/decision/three_rank_top10_{self.day}.json'
        self.sp=f'work/profit_1000_upgrade/candidate_natural_forward/day_{self.day}.json'
        self.row=dict(ts_code=self.code,signal_date=self.day,generated_at_utc='2026-09-29T12:56:30+00:00',predicted_promotion_probability=.6,gap_open=.02,limit_first_time_minutes=570,limit_open_times=0,limit_seal_to_amount=1,path_label='持续强势',token='secret')
        self.make()
    def put(self,path,raw):
        p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
        return {'path':path,'sha256':x.sha(raw)}
    def make(self):
        out=io.StringIO();w=csv.DictWriter(out,fieldnames=list(self.row));w.writeheader();w.writerow(self.row)
        rt=self.put(self.rt,out.getvalue().encode());rc=self.put(self.rc,x.encoded({'signal_date':self.day}))
        pb=self.put(self.p0,x.encoded({'signal_date':self.day,'rows':[dict(ts_code=self.code,predicted_promotion_probability=.6)]}))
        sb=self.put(self.sp,x.encoded({'D_source_evidence':{'projection':{'four_file_declarations':{'runtime_features':rt,'receipt':rc}}}}))
        self.record={'ts_code':self.code,'signal_date':self.day,'exec_date':'20260930','features':{'promotion_probability':None},'training_pair_ready':True,'outcome':{'net_return':.1}}
        self.doc={'model':{'features':['promotion_probability','path_change','path_持续强势','ret_2d']},'days':[{'date':self.day,'records':[self.record],'provenance':'FORMAL_FROZEN_REFERENCE','source_bindings':[pb,sb],'mature_training_day':True}]}
    def run_enrich(self):return x.enrich(self.root,self.doc,'a'*40,self.now)
    def test_extension_values_leave_old_features_scores_outcomes_unchanged(self):
        before=json.dumps({k:self.record[k] for k in ('features','outcome','training_pair_ready')})
        d=self.run_enrich();r=d['days'][0]['records'][0]
        self.assertEqual(r['research_extensions']['values']['predicted_promotion_probability'],.6)
        self.assertEqual(r['research_extensions']['values']['limit_open_times'],0)
        self.assertEqual(before,json.dumps({k:r[k] for k in ('features','outcome','training_pair_ready')}))
        self.assertNotIn('secret',json.dumps(d));self.assertFalse(d['research_data_quality']['training_allowed'])
    def test_hash_tamper_rejected_and_gap_retained(self):
        (self.root/self.rt).write_text('bad')
        d=self.run_enrich();r=d['days'][0]['records'][0]
        self.assertEqual(r['research_extensions']['source_status'],'SOURCE_UNAVAILABLE_OR_REJECTED')
        self.assertIsNone(r['research_extensions']['values']['gap_open'])
        self.assertEqual(d['research_data_quality']['complete_core_extension_days'],0)
    def test_future_runtime_not_accepted(self):
        self.row['generated_at_utc']='2026-10-01T12:00:00+00:00';self.make()
        self.assertIsNone(self.run_enrich()['days'][0]['records'][0]['research_extensions']['values']['gap_open'])
    def test_duplicate_member_rejected(self):
        raw=(self.root/self.rt).read_bytes();self.put(self.rt,raw+raw.splitlines(keepends=True)[1])
        # Bind the modified bytes too: identity rejection still applies.
        snap=json.loads((self.root/self.sp).read_bytes());snap['D_source_evidence']['projection']['four_file_declarations']['runtime_features']['sha256']=x.sha((self.root/self.rt).read_bytes())
        sb=self.put(self.sp,x.encoded(snap));self.doc['days'][0]['source_bindings'][-1]=sb
        self.assertIsNone(self.run_enrich()['days'][0]['records'][0]['research_extensions']['values']['gap_open'])
    def test_late_night_generation_kept_before_entry(self):
        self.row['generated_at_utc']='2026-09-29T17:51:57+00:00';self.make()
        d=self.run_enrich();r=d['days'][0]['records'][0]['research_extensions']
        self.assertEqual(r['source_status'],'HASH_BOUND_LATE_GENERATED_D_ARTIFACT')
        self.assertTrue(r['source_timestamp_before_entry'])
        self.assertEqual(d['research_data_quality']['mature_extended_training_days'],1)
    def test_post_entry_generation_not_training_ready(self):
        self.row['generated_at_utc']='2026-09-30T02:00:00+00:00';self.make()
        d=self.run_enrich()
        self.assertIsNotNone(d['days'][0]['records'][0]['research_extensions']['values']['gap_open'])
        self.assertEqual(d['research_data_quality']['mature_extended_training_days'],0)
    def test_repeated_collection_keeps_original_time_and_file(self):
        self.run_enrich();files=list((self.root/x.ROOT/'extensions').rglob('*.json'));raw=files[0].read_bytes()
        self.now='2026-10-02T03:00:00+00:00';self.run_enrich()
        self.assertEqual(files[0].read_bytes(),raw);self.assertEqual(len(list((self.root/x.ROOT/'extensions').rglob('*.json'))),1)
    def test_source_revision_creates_new_version_preserves_old(self):
        self.run_enrich();old=list((self.root/x.ROOT/'extensions').rglob('*.json'))[0];raw=old.read_bytes()
        self.row['gap_open']=.03;self.make();self.run_enrich()
        self.assertEqual(old.read_bytes(),raw);self.assertEqual(len(list((self.root/x.ROOT/'extensions').rglob('*.json'))),2)
    def test_old_extension_tamper_fails(self):
        self.run_enrich();p=list((self.root/x.ROOT/'extensions').rglob('*.json'))[0];e=json.loads(p.read_bytes());e['payload']['source_status']='tamper';p.write_bytes(x.encoded(e))
        with self.assertRaises(ValueError):self.run_enrich()
    def test_numeric_invalid_is_not_zero(self):
        self.assertEqual(x.numeric('oops'),(None,'INVALID_NUMERIC'))
        self.assertEqual(x.numeric('2',0,1),(None,'OUT_OF_RANGE'))
    def test_retro_records_never_upgraded(self):
        self.doc['days'][0]['provenance']='RETROSPECTIVE_UNVERIFIED'
        r=self.run_enrich()['days'][0]['records'][0]
        self.assertIsNone(r['research_extensions']['values']['predicted_promotion_probability'])
        self.assertFalse(r['research_extensions']['point_in_time_independently_verified'])
    def test_bound_extension_changes_block_publish(self):
        self.assertTrue(allowed(x.ROOT+'/extensions/20260929/abc.json'))
        self.assertFalse(sources_unchanged({'status':'ahead','files':[{'filename':self.rt}]},{self.rt}))

if __name__=='__main__':unittest.main()
