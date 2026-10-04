import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import lab

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.p=dict(provenance='PROSPECTIVE_LOCAL',entry_at='2026-09-28T09:25:00+08:00',exit_at='2026-09-29T10:00:00+08:00',outcomes=[])
    def test_time_does_not_imply_fill_or_settlement(self):
        self.assertEqual(lab.research_stage(self.p,'2026-09-27T22:00:00+08:00'),'WAITING_ENTRY')
        self.assertEqual(lab.research_stage(self.p,'2026-09-28T09:25:00+08:00'),'WAITING_EXIT_FILL_UNCONFIRMED')
        self.assertEqual(lab.research_stage(self.p,'2026-09-29T10:00:00+08:00'),'DUE_FOR_OUTCOME_EVIDENCE')
        self.assertEqual(self.p['outcomes'],[])
    def test_old_prediction_not_promoted(self):
        self.p['provenance']='LEGACY_UNVERIFIED_FREEZE'
        self.assertEqual(lab.research_stage(self.p,'2026-10-01T22:00:00+08:00'),'LEGACY_REQUIRES_EVIDENCE_AUDIT')
    def test_recorded_missing_not_settled(self):
        self.p['outcomes']=[dict(status='MISSING',basis='QUOTE_PROXY')]
        self.assertEqual(lab.research_stage(self.p,'2026-10-01T22:00:00+08:00'),'RESULTS_RECORDED_SEE_BASIS')
