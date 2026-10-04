import os,tempfile,unittest,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tushare_access as t
class Credentials(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.patch=patch.object(t,'CREDENTIAL',Path(self.tmp.name)/'private'/'token');self.patch.start()
 def tearDown(self):self.patch.stop();self.tmp.cleanup()
 def test_private_roundtrip(self):
  self.assertEqual(t.local_token('x'*40),'x'*40);self.assertEqual(t.CREDENTIAL.stat().st_mode&0o777,0o600)
 def test_missing_never_keychain(self):
  with patch.object(t,'keychain',side_effect=AssertionError('must not call')):
   with self.assertRaisesRegex(t.AccessError,'LOCAL_TOKEN_REQUIRED'):t.query('daily',{})
 def test_invalid_clipboard(self):
  with self.assertRaises(t.AccessError):t.local_token('not a token')
  self.assertFalse(t.CREDENTIAL.exists())
 def test_permissions_fail_closed(self):
  t.local_token('x'*40);os.chmod(t.CREDENTIAL,0o644)
  with self.assertRaises(t.AccessError):t.local_token()
 def test_symlink_rejected(self):
  t.CREDENTIAL.parent.mkdir();target=Path(self.tmp.name)/'target';target.write_text('unchanged');t.CREDENTIAL.symlink_to(target)
  with self.assertRaises(t.AccessError):t.local_token('x'*40)
  self.assertEqual(target.read_text(),'unchanged')
