import json,tempfile,unittest,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_lab as fixtures
import lab,research_contract
class ContractTests(unittest.TestCase):
 setUp=fixtures.EvidenceTest.setUp
 tearDown=fixtures.EvidenceTest.tearDown
 file=fixtures.EvidenceTest.file
 forecast=fixtures.EvidenceTest.forecast
 def attempt(self,d):
  with patch.object(lab,'now',return_value='2026-09-24T22:00:00+08:00'):lab.freeze(self.c,self.file('contract.json',d))
 def test_contract_mandatory(self):
  d=self.forecast();d.pop('research_contract')
  with self.assertRaisesRegex(ValueError,'契约'):self.attempt(d)
 def test_pool_missing_member(self):
  d=self.forecast();d['predictions'].append({**d['predictions'][0],'code':'600802.SH'})
  with self.assertRaisesRegex(ValueError,'完整候选'):self.attempt(d)
 def test_weekend_exit_rejected(self):
  d=self.forecast();d['exit_at']='2026-09-30T10:00:00+08:00'
  with self.assertRaisesRegex(ValueError,'后续两个'):self.attempt(d)
 def test_missing_not_zero(self):
  d=self.forecast();d['predictions'][0]['weighted_features']['theme']['score']=0
  with self.assertRaisesRegex(ValueError,'空分'):self.attempt(d)
 def test_hash_tampering(self):
  d=self.forecast();d['candidate_pool']['sha256']='bad'
  with self.assertRaisesRegex(ValueError,'SHA'):self.attempt(d)
 def test_uninformed_excluded(self):
  d=self.forecast();d['predictions'][0].update(p=.5,probability_method='UNINFORMED');self.attempt(d)
  p=json.loads(self.c.execute('select payload from predictions').fetchone()[0]);self.assertFalse(p['independent_rank_eligible']);self.assertIsNone(p['weighted_review']['score'])
 def test_fake_calibration_rejected(self):
  d=self.forecast();d['predictions'][0]['probability_method']='CALIBRATED'
  with self.assertRaisesRegex(ValueError,'校准'):self.attempt(d)
 def test_alias_and_future_evidence(self):
  d=self.forecast();x=d['predictions'][0];ref=d['candidate_pool']
  x['evidence_refs']={'a':{**ref,'source':'test','as_of':'2026-09-24T13:00:00+08:00','available_at':'2026-09-24T14:00:00+08:00','captured_at':'2026-09-24T14:00:00+08:00'}}
  x['weighted_features']['market']=dict(status='VERIFIED',score=.5,rationale='test',evidence_ids=['a'],available_at='2026-09-24T14:00:00+08:00',captured_at='2026-09-24T14:00:00+08:00')
  x['weighted_features']['theme']=dict(x['weighted_features']['market'])
  with self.assertRaisesRegex(ValueError,'重复加权'):self.attempt(d)
  x['weighted_features']['theme']=dict(status='MISSING',score=None,missing_reason='missing');x['evidence_refs']['a']['available_at']='2026-09-25T14:00:00+08:00'
  with self.assertRaisesRegex(ValueError,'时点'):self.attempt(d)
 def test_false_executable_quote_rejected(self):
  d=self.forecast();self.attempt(d)
  o=dict(prediction_id=1,basis='QUOTE_PROXY',status='SETTLED',entry_price=10,exit_price=11,entry_at=d['entry_at'],exit_at=d['exit_at'],evidence='only quote')
  with patch.object(lab,'now',return_value='2026-09-29T11:00:00+08:00'),self.assertRaisesRegex(ValueError,'执行证据'):lab.outcome(self.c,self.file('out.json',o))
