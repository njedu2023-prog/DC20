import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]));import result_capture as r
class CaptureTests(unittest.TestCase):
 def p(self):return dict(id=1,code='002909.SZ',provenance='PROSPECTIVE_LOCAL',entry_at='2026-09-28T09:25:00+08:00',exit_at='2026-09-29T10:00:00+08:00')
 def test_before(self):self.assertEqual([j['state'] for j in r.jobs([self.p()],'2026-09-26T22:00:00+08:00')],['WAITING','WAITING'])
 def test_entry_night(self):self.assertEqual([j['state'] for j in r.jobs([self.p()],'2026-09-28T22:00:00+08:00')],['DUE','WAITING'])
 def test_exit_night(self):self.assertEqual([j['state'] for j in r.jobs([self.p()],'2026-09-29T22:00:00+08:00')],['DUE','DUE'])
 def test_versions_share_capture(self):
  a=self.p();b={**a,'id':2};j=r.jobs([a,b],'2026-09-28T22:00:00+08:00');self.assertEqual(len(j),2);self.assertEqual(j[0]['prediction_ids'],[1,2])
 def test_legacy_excluded(self):self.assertEqual(r.jobs([{**self.p(),'provenance':'LEGACY_UNVERIFIED_FREEZE'}],'2026-09-30T22:00:00+08:00'),[])
if __name__=='__main__':unittest.main()
