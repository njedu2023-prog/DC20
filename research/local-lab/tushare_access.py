"""Tushare HTTPS access with private local credential file; never log secrets."""
import os,stat
from pathlib import Path
import ctypes as C
import json,re,subprocess,urllib.request,ssl
SERVICE=b'DC20.local-lab.Tushare'
ACCOUNT=b'TUSHARE_TOKEN'
ENDPOINT='https://api.tushare.pro'
class AccessError(RuntimeError):pass

def security():
 s=C.CDLL('/System/Library/Frameworks/Security.framework/Security')
 s.SecKeychainFindGenericPassword.argtypes=[C.c_void_p,C.c_uint32,C.c_char_p,C.c_uint32,C.c_char_p,C.POINTER(C.c_uint32),C.POINTER(C.c_void_p),C.POINTER(C.c_void_p)]
 s.SecKeychainAddGenericPassword.argtypes=[C.c_void_p,C.c_uint32,C.c_char_p,C.c_uint32,C.c_char_p,C.c_uint32,C.c_void_p,C.POINTER(C.c_void_p)]
 s.SecKeychainItemModifyAttributesAndData.argtypes=[C.c_void_p,C.c_void_p,C.c_uint32,C.c_void_p]
 s.SecKeychainItemFreeContent.argtypes=[C.c_void_p,C.c_void_p]
 return s

def keychain(value=None):
 s=security();length=C.c_uint32();data=C.c_void_p();item=C.c_void_p()
 status=s.SecKeychainFindGenericPassword(None,len(SERVICE),SERVICE,len(ACCOUNT),ACCOUNT,C.byref(length),C.byref(data),C.byref(item))
 try:
  if value is None:
   if status:raise AccessError('KEYCHAIN_READ_STATUS_'+str(status))
   return C.string_at(data,length.value).decode()
  if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}',value):raise AccessError('CLIPBOARD_NOT_TOKEN_SHAPED')
  raw=value.encode();buf=C.create_string_buffer(raw)
  if status==0:result=s.SecKeychainItemModifyAttributesAndData(item,None,len(raw),buf)
  elif status==-25300:result=s.SecKeychainAddGenericPassword(None,len(SERVICE),SERVICE,len(ACCOUNT),ACCOUNT,len(raw),buf,None)
  else:raise AccessError('KEYCHAIN_FIND_STATUS_'+str(status))
  if result:raise AccessError('KEYCHAIN_WRITE_STATUS_'+str(result))
 finally:
  if data.value:s.SecKeychainItemFreeContent(None,data)
  if item.value:
   cf=C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation');cf.CFRelease.argtypes=[C.c_void_p];cf.CFRelease(item)

CREDENTIAL=Path(__file__).resolve().parent/'data'/'credentials'/'tushare.token'

def local_token(value=None):
 if value is not None:
  if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}',value):raise AccessError('CLIPBOARD_NOT_TOKEN_SHAPED')
  CREDENTIAL.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
  if CREDENTIAL.parent.is_symlink() or CREDENTIAL.is_symlink():raise AccessError('UNSAFE_CREDENTIAL_PATH')
  os.chmod(CREDENTIAL.parent,0o700)
  fd=os.open(CREDENTIAL,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
  with os.fdopen(fd,'w') as f:os.fchmod(f.fileno(),0o600);f.write(value);f.flush();os.fsync(f.fileno())
 try:
  fd=os.open(CREDENTIAL,os.O_RDONLY|os.O_NOFOLLOW)
 except FileNotFoundError:raise AccessError('LOCAL_TOKEN_REQUIRED') from None
 with os.fdopen(fd) as f:
  st=os.fstat(f.fileno())
  if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_mode&0o077:raise AccessError('UNSAFE_CREDENTIAL_PERMISSIONS')
  token=f.read(257).strip()
 if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}',token):raise AccessError('INVALID_LOCAL_TOKEN')
 return token

class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise AccessError('REDIRECT_REFUSED')

def query(api,params,fields=''):
 token=local_token()
 body=json.dumps({'api_name':api,'token':token,'params':params,'fields':fields}).encode()
 req=urllib.request.Request(ENDPOINT,data=body,headers={'Content-Type':'application/json'},method='POST')
 try:
  opener=urllib.request.build_opener(NoRedirect(),urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile='/etc/ssl/cert.pem')))
  with opener.open(req,timeout=25) as r:response=r.read()
  # Reject unexpected token reflection before exposing or archiving anything.
  if token.encode() in response:raise AccessError('RESPONSE_SECRET_REFLECTION')
  obj=json.loads(response)
 except AccessError:raise
 except Exception as e:raise AccessError('TRANSPORT_OR_PARSE_'+type(e).__name__) from None
 if obj.get('code')!=0:raise AccessError('TUSHARE_API_CODE_'+str(obj.get('code')))
 data=obj.get('data') or {};return {'fields':data.get('fields',[]),'items':data.get('items',[])}

if __name__=='__main__':
 import argparse,datetime,pathlib
 p=argparse.ArgumentParser();p.add_argument('--save-clipboard',action='store_true');p.add_argument('--probe',action='store_true');args=p.parse_args()
 try:
  if args.save_clipboard:
   value=subprocess.run(['/usr/bin/pbpaste'],capture_output=True,check=True).stdout.decode().strip()
   local_token(value)
   if local_token()!=value:raise AccessError('LOCAL_READBACK_MISMATCH')
   del value
   print('LOCAL_SAVED_AND_READBACK_VERIFIED')
  if args.probe:
   probes=[('trade_cal',{'exchange':'SSE','start_date':'20260924','end_date':'20260929'},'exchange,cal_date,is_open'),('daily',{'ts_code':'002909.SZ','trade_date':'20260924'},'ts_code,trade_date,open,close,vol,amount'),('stk_auction_o',{'ts_code':'002909.SZ','trade_date':'20260924'},''),('stk_mins',{'ts_code':'002909.SZ','freq':'1min','start_date':'2026-09-24 09:59:00','end_date':'2026-09-24 10:01:00'},'')]
   report={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'endpoint':ENDPOINT,'credential_store':'private local file (0600)','checks':[]}
   for api,params,fields in probes:
    try:
     result=query(api,params,fields);entry={'api':api,'params':params,'state':'RETURNED_DATA' if result['items'] else 'EMPTY','rows':len(result['items']),'result':result}
    except AccessError as e:entry={'api':api,'params':params,'state':'FAILED','error':str(e)}
    report['checks'].append(entry);print(json.dumps({k:v for k,v in entry.items() if k not in ('params','result')}))
   folder=pathlib.Path(__file__).parent/'data'/'tushare';folder.mkdir(parents=True,exist_ok=True)
   path=folder/('connection-'+datetime.datetime.now().strftime('%Y%m%dT%H%M%S')+'.json');path.write_text(json.dumps(report,ensure_ascii=False,indent=2));print('REPORT_SAVED '+str(path))
 except Exception as e:
  print(str(e) if isinstance(e,AccessError) else 'FAILED_'+type(e).__name__);raise SystemExit(1)
