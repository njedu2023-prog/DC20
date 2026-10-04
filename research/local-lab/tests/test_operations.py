import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import lab,operations as ops,result_capture as capture
class OpsTests(unittest.TestCase):
 def setUp(self):
  self.nightly_patch=patch('learning.nightly.run',return_value={'intake':{'state':'NON_SESSION'}});self.nightly_patch.start();self.addCleanup(self.nightly_patch.stop)
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.c=lab.connect(self.root/'live.db');p=self.root/'raw.txt';p.write_text('evidence');lab.artifact(self.c,p);self.c.commit()
 def tearDown(self):self.c.close();self.tmp.cleanup()
 def test_backup_restore_readback_and_corruption(self):
  r=ops.backup(self.c,self.root/'backup');self.assertEqual(r['blobs'],1)
  next((self.root/'backup/blobs').iterdir()).write_text('corrupted')
  with self.assertRaises(ValueError):ops.verify_backup(self.root/'backup')
 def test_backup_rejects_pending_transaction(self):
  self.c.execute("BEGIN")
  with self.assertRaisesRegex(ValueError,"pending writes"):ops.backup(self.c,self.root/"pending")
  self.assertFalse((self.root/"pending").exists());self.c.rollback()
 def test_overlap_run_lock(self):
  with ops.lock(self.root/'lock'):
   with self.assertRaises(RuntimeError):
    with ops.lock(self.root/'lock'):pass
 def test_capture_failure_persists_run(self):
  with patch.object(capture,'run',side_effect=OSError('network unavailable')):r=ops.run(self.c,self.root/'run',True)
  self.assertEqual(r['state'],'FAILED');self.assertTrue((self.root/'run/run.json').exists());self.assertEqual(self.c.execute('select count(*) from outcomes').fetchone()[0],0)
 def test_no_future_fetch(self):
  with patch.object(capture.ths_evidence,'collect',side_effect=AssertionError('should not fetch')):r=ops.run(self.c,self.root/'run',True)
  self.assertEqual(r['steps']['capture']['attempts'],0)
 def test_repeated_output_rejected(self):
  ops.run(self.c,self.root/'run')
  with self.assertRaises(FileExistsError):ops.run(self.c,self.root/'run')
 def test_delivery_failure_and_unknown_separate(self):
  rows=[dict(recipient='a',signal_date='D',version='v',status='DELIVERED'),dict(recipient='b',signal_date='D',version='v',status='FAILED'),dict(recipient='c',signal_date='D',version='v',status='SENT_RECEIPT_PENDING')]
  actions=ops.delivery_actions(rows);self.assertEqual(len(actions),2);self.assertEqual(actions[0]['action'],'RETRY_NEXT_AUTHORIZED_NIGHT');self.assertEqual(actions[1]['action'],'CHECK_CONVERSATION_BEFORE_ANY_RESEND')
