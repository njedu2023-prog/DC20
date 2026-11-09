from datetime import datetime
import io,json,tarfile,unittest,urllib.error,zipfile
from pathlib import Path
from unittest.mock import patch,Mock
from scripts import abandoned_pages_repair as r

class Response:
 def __init__(self,code=204,raw=b''):self.status,self.raw=code,raw
 def __enter__(self):return self
 def __exit__(self,*a):return False
 def read(self,n):return self.raw[:n]

class CancelTests(unittest.TestCase):
 def test_one_exact_post(self):
  api,opener=r.Client('token'),Mock();opener.open.return_value=Response()
  with patch.object(r.urllib.request,'build_opener',return_value=opener):
   result=api.cancel_once()
   with self.assertRaisesRegex(ValueError,'CANCEL_ALREADY_ATTEMPTED'):api.cancel_once()
  request=opener.open.call_args.args[0]
  self.assertEqual(request.full_url,'https://api.github.com/repos/'+r.REPO+'/pages/deployments/'+r.DEPLOYMENT+'/cancel')
  self.assertEqual(request.get_method(),'POST');self.assertEqual(opener.open.call_count,1)
  self.assertFalse(result['publication_complete'])
  with self.assertRaisesRegex(ValueError,'NON_CANCEL_MUTATION_REJECTED'):api.request('/pages/deployments',{})
 def test_unknown_and_http_errors_never_retry(self):
  for error in [TimeoutError(),urllib.error.HTTPError('x',404,'missing',{},None),urllib.error.HTTPError('x',500,'server',{},None)]:
   with self.subTest(error=error):
    api,opener=r.Client('t'),Mock();opener.open.side_effect=error
    with patch.object(r.urllib.request,'build_opener',return_value=opener):
     with self.assertRaisesRegex(ValueError,'DO_NOT_RETRY'):api.cancel_once()
     with self.assertRaisesRegex(ValueError,'CANCEL_ALREADY_ATTEMPTED'):api.cancel_once()
    self.assertEqual(opener.open.call_count,1)
 def test_only_empty_204_is_acknowledged(self):
  for code,raw in [(200,b''),(202,b''),(204,b'x')]:
   opener=Mock();opener.open.return_value=Response(code,raw)
   with patch.object(r.urllib.request,'build_opener',return_value=opener):
    with self.assertRaisesRegex(ValueError,'DO_NOT_RETRY'):r.Client('t').cancel_once()
 def test_download_redirect_drops_auth(self):
  opener=Mock();opener.open.side_effect=[urllib.error.HTTPError('x',302,'redirect',{'Location':'https://safe.blob.core.windows.net/file?sig=private'},None),Response(200,b'logs')]
  with patch.object(r.urllib.request,'build_opener',return_value=opener):self.assertEqual(r.Client('secret').raw('/actions/jobs/1/logs'),b'logs')
  self.assertTrue(opener.open.call_args_list[0].args[0].get_header('Authorization'))
  self.assertIsNone(opener.open.call_args_list[1].args[0].get_header('Authorization'))
 def test_untrusted_redirect_rejected(self):
  opener=Mock();opener.open.side_effect=urllib.error.HTTPError('x',302,'redirect',{'Location':'https://attacker.invalid/file'},None)
  with patch.object(r.urllib.request,'build_opener',return_value=opener):
   with self.assertRaisesRegex(ValueError,'UNTRUSTED_DOWNLOAD_REDIRECT'):r.Client('secret').raw('/actions/jobs/1/logs')

class EvidenceTests(unittest.TestCase):
 def test_log_hash_and_literals_and_identity(self):
  raw=b'exact duplicate deployment proof\n';api=Mock();api.raw.return_value=raw
  job={'id':1,'name':'source','conclusion':'success'}
  spec={'job_id':1,'name':'source','conclusion':'success','sha256':r.digest(raw),'required_literals':['duplicate deployment']}
  r.checked_logs(api,{1:job},[spec])
  for bad in [dict(spec,sha256='0'*64),dict(spec,required_literals=['missing']),dict(spec,name='other')]:
   with self.assertRaises(ValueError):r.checked_logs(api,{1:job},[bad])
 def test_backend_succeed_is_only_observation_not_clear(self):
  api=Mock();api.request.return_value={'status':'succeed'};self.assertEqual(r.backend_state(api),'succeed')
  for status in ['deployment_cancelled','deployment_failed','unknown',None]:
   api.request.return_value={'status':status}
   with self.assertRaises(ValueError):r.backend_state(api)
 def test_archive_rejects_traversal_and_links(self):
  for name,kind in [('../evil',tarfile.REGTYPE),('link',tarfile.SYMTYPE)]:
   buf=io.BytesIO()
   with tarfile.open(fileobj=buf,mode='w') as tar:
    member=tarfile.TarInfo(name);member.type=kind;member.size=0;tar.addfile(member,io.BytesIO())
   archive=io.BytesIO()
   with zipfile.ZipFile(archive,'w') as z:z.writestr('artifact.tar',buf.getvalue())
   with self.assertRaisesRegex(ValueError,'UNSAFE_ARTIFACT_'):r.artifact_files(archive.getvalue())
 def test_public_bytes_not_only_head_must_match(self):
  primary={f'item{i}.json':str(i).encode() for i in range(5)}
  revision={'head_sha':r.SOURCE,'primary_d_receipt_url':'receipt.json'}
  for i in range(5):revision[f'item{i}_url']=f'item{i}.json';revision[f'item{i}_sha256']=r.digest(primary[f'item{i}.json'])
  primary['receipt.json']=b'receipt';primary['revision.json']=json.dumps(revision).encode()
  def read(url):return primary[url.removeprefix(r.PUBLIC)]
  self.assertEqual(len(r.preserved_public(primary,read)),6)
  def corrupt(url):return b'changed' if url.endswith('item3.json') else read(url)
  with self.assertRaisesRegex(ValueError,'PUBLIC_CORE_FINGERPRINT_CHANGED'):r.preserved_public(primary,corrupt)
  with self.assertRaisesRegex(ValueError,'PUBLIC_REVISION_CHANGED'):r.preserved_public(primary,lambda u:b'changed')
 def test_real_clock_exact_day_and_2330_cutoff(self):
  for stamp,ok in [('2026-11-09T14:59:00+08:00',False),('2026-11-09T15:00:00+08:00',True),('2026-11-09T23:29:59+08:00',True),('2026-11-09T23:30:00+08:00',False),('2026-11-10T21:00:00+08:00',False)]:
   with self.subTest(stamp=stamp),patch.object(r,'datetime') as clock:
    clock.now.return_value=datetime.fromisoformat(stamp)
    if ok:r.within_cancel_window()
    else:
     with self.assertRaisesRegex(ValueError,'WINDOW_CLOSED'):r.within_cancel_window()

class QueueTests(unittest.TestCase):
 def api(self,other=None,members=None):
  def request(path):
   if path=='/actions/concurrency_groups?per_page=100':return {'total_count':1,'concurrency_groups':[{'group_name':'dc20-pages'}]}
   if path=='/actions/concurrency_groups/dc20-pages':
    rows=members or [{'run_id':100,'status':'in_progress'}];return {'group_name':'dc20-pages','total_count':len(rows),'group_members':rows}
   if path.startswith('/actions/runs?status='):
    rows=[other] if other and 'status=in_progress&' in path else [];return {'total_count':len(rows),'workflow_runs':rows}
   if other and path=='/actions/runs/'+str(other['id']):return other
   if '/jobs?' in path:return {'total_count':1,'jobs':[{'id':999,'status':'in_progress'}]}
   if path=='/git/ref/heads/main':return {'ref':'refs/heads/main','object':{'sha':'a'*40}}
   raise AssertionError(path)
  api=Mock();api.request.side_effect=request;return api
 def test_all_statuses_without_branch_filter(self):
  api=self.api();self.assertEqual(r.no_other_work(api,100,'a'*40),[])
  urls=[c.args[0] for c in api.request.call_args_list]
  self.assertEqual(len([u for u in urls if u.startswith('/actions/runs?status=')]),5)
  self.assertTrue(all('branch=' not in u for u in urls))
 def test_live_guard_not_exempt(self):
  other={'id':42635323337,'head_branch':'main','repository':{'full_name':r.REPO},'head_repository':{'full_name':r.REPO},'run_attempt':1,'status':'in_progress','path':'.github/workflows/profit_research_pages.yml'}
  with self.assertRaisesRegex(ValueError,'OTHER_LIVE_RUN_OR_JOB_42635323337'):r.no_other_work(self.api(other),100,'a'*40)
 def test_other_pages_lock_member_blocks(self):
  with self.assertRaisesRegex(ValueError,'PAGES_QUEUE_NOT_EXCLUSIVE'):r.no_other_work(self.api(members=[{'run_id':101,'status':'in_progress'}]),100,'a'*40)
 def test_api_error_or_incomplete_group_never_clear(self):
  api=Mock();api.request.side_effect=ValueError('HTTP_403')
  with self.assertRaisesRegex(ValueError,'HTTP_403'):r.no_other_work(api,100,'a'*40)
  api=Mock();api.request.return_value={'total_count':2,'concurrency_groups':[{'group_name':'dc20-pages'}]}
  with self.assertRaisesRegex(ValueError,'CONCURRENCY_LIST_INCOMPLETE'):r.no_other_work(api,100,'a'*40)
 def test_incomplete_jobs_pagination_rejected(self):
  api=Mock();api.request.return_value={'total_count':2,'jobs':[{'id':1,'status':'completed'}]}
  with self.assertRaisesRegex(ValueError,'API_PAGINATION_INCOMPLETE'):r.terminal_jobs(api,r.PRODUCER)

class MarkerTests(unittest.TestCase):
 def setup_api(self,mode,prior):
  head='a'*40;env={'GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':r.REPO,'GITHUB_REF':'refs/heads/main','GITHUB_RUN_ATTEMPT':'1','GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_WORKFLOW_REF':r.REPO+'/'+r.WORKFLOW_PATH+'@refs/heads/main','GITHUB_SHA':head,'GITHUB_RUN_ID':'100'}
  run={'id':100,'path':r.WORKFLOW_PATH,'name':r.WORKFLOW_NAME,'head_branch':'main','run_attempt':1,'repository':{'full_name':r.REPO},'head_repository':{'full_name':r.REPO},'head_sha':head,'event':'workflow_dispatch','status':'in_progress','workflow_id':1,'display_title':'Repair abandoned Pages | '+mode+' | id='+r.DEPLOYMENT}
  def request(path):
   if path=='/actions/workflows/'+r.WORKFLOW:return {'id':1,'state':'active','path':r.WORKFLOW_PATH,'name':r.WORKFLOW_NAME}
   if path=='/actions/runs/100':return run
   if '/runs?' in path:
    rows=[run]+prior;return {'total_count':len(rows),'workflow_runs':rows}
   raise AssertionError(path)
  api=Mock();api.request.side_effect=request;return env,api
 def test_prior_audit_does_not_consume_cancel(self):
  env,api=self.setup_api('cancel',[{'id':99,'display_title':'Repair abandoned Pages | audit | id='+r.DEPLOYMENT}]);self.assertEqual(r.environment(api,env,'a'*40,'cancel'),100)
 def test_any_prior_cancel_permanently_blocks_auto_retry(self):
  for conclusion in ['failure','cancelled',None,'success']:
   env,api=self.setup_api('cancel',[{'id':99,'display_title':r.cancel_marker(),'conclusion':conclusion}])
   with self.assertRaisesRegex(ValueError,'PRIOR_CANCEL_RUN_EXISTS_DO_NOT_RETRY'):r.environment(api,env,'a'*40,'cancel')
 def test_rerun_rejected(self):
  env,api=self.setup_api('audit',[]);env['GITHUB_RUN_ATTEMPT']='2'
  with self.assertRaisesRegex(ValueError,'REPAIR_ENVIRONMENT_REJECTED'):r.environment(api,env,'a'*40,'audit')

class WorkflowTests(unittest.TestCase):
 def test_manual_only_same_lock_no_other_writes(self):
  text=(Path(__file__).resolve().parents[1]/'.github/workflows/repair_abandoned_pages.yml').read_text()
  for expected in ['workflow_dispatch:','group: dc20-pages','cancel-in-progress: false','persist-credentials: false','python -m unittest discover']:self.assertIn(expected,text)
  for forbidden in ['schedule:','workflow_run:','uses: ./.github/workflows/deploy_','id-token: write']:self.assertNotIn(forbidden,text)

if __name__=='__main__':unittest.main()
