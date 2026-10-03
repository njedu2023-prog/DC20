"""Cross-layer guards: enrichment never rewrites frozen scoring evidence."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.profit_research_extensions import enrich, ENRICHMENT_FIELDS
from scripts.publish_profit_research import allowed, sources_unchanged


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.row={'signal_date':'20260921','ts_code':'000504.SZ','exec_date':'20260922',
            'features':{'promotion_probability':None},'profit_rank':None,'score':None,
            'training_pair_ready':False,'outcome':{'net_return':None,'status':'MISSING_COUNTERFACTUAL_LABEL'}}
        self.doc={'model':{'features':['promotion_probability']},'days':[
            {'date':'20260921','records':[self.row],'provenance':'RETROSPECTIVE_UNVERIFIED',
             'source_bindings':[],'mature_training_day':False}]}
        self.envelope={'first_collected_at_utc':'2026-10-04T01:00:00+00:00',
            'source_binding':{'path':'outputs/decision/profit_research/enrichment/20260921/000504.SZ/'+'a'*64+'.json','sha256':'b'*64},
            'payload':{'signal_date':'20260921','ts_code':'000504.SZ','rule_version':'test_rule',
                'collection_kind':'HISTORICAL_BACKFILL','point_in_time_independently_verified':False,
                'values':dict(zip(ENRICHMENT_FIELDS,[565,.002,.01,-.02])),
                'missing_reasons':dict.fromkeys(ENRICHMENT_FIELDS),
                'field_sources':{'limit_last_time_minutes':{'source_path':'data/market/raw/2026/20260921/limit_list_d.csv','raw_sha256':'c'*64}}}}

    def run_enrich(self):
        with patch('scripts.profit_research_enrichment.read_record',return_value=self.envelope):
            return enrich(self.root,self.doc,'d'*40,'2026-10-04T01:00:00+00:00')

    def test_research_overlay_cannot_change_original_scores_labels_or_maturity(self):
        before=copy.deepcopy(self.row)
        self.run_enrich()
        for key,value in before.items():self.assertEqual(self.row[key],value)
        ext=self.row['research_extensions']
        self.assertIsNone(ext['original_values']['minute_realized_vol'])
        self.assertEqual(ext['values']['minute_realized_vol'],.002)
        self.assertFalse(ext['point_in_time_independently_verified'])
        self.assertFalse(self.doc['days'][0]['mature_training_day'])

    def test_quality_never_admits_backfill_as_training(self):
        q=self.run_enrich()['research_data_quality']
        self.assertEqual(q['enrichment']['minute_complete_records'],1)
        self.assertEqual(q['enrichment']['last_seal_recovered_records'],1)
        self.assertEqual(q['enrichment']['historical_backfill_records'],1)
        self.assertEqual(q['enrichment']['before_entry_records'],0)
        self.assertEqual(q['readiness']['training_admitted_days'],0)
        self.assertFalse(q['training_allowed'])

    def test_overlay_binds_raw_sources_and_sidecar_for_publication_cas(self):
        d=self.run_enrich();paths={b['path'] for b in d['extension_source_bindings']}
        raw='data/market/raw/2026/20260921/limit_list_d.csv'
        self.assertIn(raw,paths)
        self.assertIn(self.envelope['source_binding']['path'],paths)
        self.assertFalse(sources_unchanged({'status':'ahead','files':[{'filename':raw}]},paths))

    def test_public_allowlist_excludes_raw_minute_and_private_cache(self):
        self.assertTrue(allowed(self.envelope['source_binding']['path']))
        self.assertTrue(allowed('outputs/decision/profit_research/operations/receipts/20261004/'+'a'*64+'.json'))
        self.assertTrue(allowed('outputs/decision/profit_research/operations/calendars/'+'a'*64+'.json'))
        for path in ['data/market/minute_1m/2026/20260921/000504.SZ.csv',
                     'outputs/decision/profit_research/private_cache/source.json',
                     'outputs/decision/profit_research/operations/raw/source.json',
                     'outputs/decision/profit_research/operations/storage-secrets.json',
                     'outputs/decision/profit_research/enrichment/../secret.json']:
            self.assertFalse(allowed(path))

    def test_workflow_records_after_collection_and_publishes_failed_acceptance(self):
        workflow=Path('.github/workflows/profit_research.yml').read_text()
        self.assertIn('RESEARCH_RUN_STARTED_AT_UTC=',workflow)
        self.assertIn('scripts/profit_research_operations.py',workflow)
        self.assertLess(workflow.index('Bounded research-only D feature enrichment'),
                        workflow.index('Append read-only collection acceptance receipt'))
        record=workflow.split('Append read-only collection acceptance receipt',1)[1]
        self.assertIn('continue-on-error: true',record.split('- name:',1)[0])
        self.assertLess(record.index('python -m scripts.profit_research_operations --record'),
                        record.index('python -m scripts.build_profit_research'))
        self.assertNotIn('upload-artifact',workflow)


if __name__=='__main__':unittest.main()
