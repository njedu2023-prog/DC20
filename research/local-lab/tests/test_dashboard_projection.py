import json, tempfile, unittest
from pathlib import Path
import lab
from dashboard_projection import project

class ProjectionTest(unittest.TestCase):
 def test_hash_verified_projection_never_rewrites_prediction(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);c=lab.connect(root/'test.sqlite3')
   f=root/'evidence.json';f.write_text(json.dumps({'metrics':{'ma':{str(n):1 for n in (5,10,20,30,60)}}}))
   aid=lab.artifact(c,f)
   row=c.execute('select * from artifacts where id=?',(aid,)).fetchone()
   payload={'evidence_refs':{'price_volume':{'artifact_id':aid,'sha256':row['sha256']}}}
   before=json.dumps(payload);result=project(c,payload)
   self.assertTrue(result['ma_complete']);self.assertIsNone(result['daily_count'])
   self.assertEqual(json.dumps(payload),before)
   f.write_text('{}') # Mutable source changes do not change frozen projection.
   self.assertTrue(project(c,payload)['ma_complete'])
   (root/'blobs'/row['sha256']).write_text('{}')
   result=project(c,payload)
   self.assertFalse(result['ma_complete']);self.assertEqual(result['errors'],['price_volume'])
   c.close()
 def test_missing_is_not_complete(self):
  result=project(None,{})
  self.assertFalse(result['ma_complete']);self.assertFalse(result['sector_windows'])
