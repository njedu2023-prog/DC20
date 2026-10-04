import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from learning.membership import join
class MembershipTests(unittest.TestCase):
 def test_wrong_stock_rejected(self):
  with self.assertRaises(ValueError):join('A',[{'con_code':'B','ts_code':'I'}],{})
 def test_duplicate_rejected(self):
  with self.assertRaises(ValueError):join('A',[{'con_code':'A','ts_code':'I'}]*2,{})
 def test_exact_sector_identity_not_name(self):
  x=join('A',[{'con_code':'A','ts_code':'I'},{'con_code':'A','ts_code':'OTHER'}],{'I':{'name':'Industry','windows':{}}})
  self.assertEqual([r['sector_code'] for r in x],['I'])
 def test_empty_unknown(self):
  with self.assertRaises(ValueError):join('A',[],{})
