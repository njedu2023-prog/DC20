"""Deterministic auction scenario audit; never infers executable fills or settlement."""
import math
import lab
from learning import store,features,settlement_queue
VERSION='AUCTION_EXECUTION_REVIEW_V2'

def auction(c,raw_id,code,at):
 d=store.raw(c,raw_id)
 if d.get('api')!='stk_auction':raise ValueError('NOT_AUCTION')
 rs=features.records(d['data']);day=at[:10].replace('-','')
 if len(rs)!=1 or rs[0].get('ts_code')!=code or rs[0].get('trade_date')!=day:raise ValueError('AUCTION_IDENTITY')
 r=rs[0]
 if any(not isinstance(r.get(k),(int,float)) or isinstance(r[k],bool) or not math.isfinite(r[k]) or r[k]<=0 for k in ('price','pre_close','vol')):raise ValueError('AUCTION_VALUE')
 return r,r['price']/r['pre_close']-1

def run(c):
 q=settlement_queue.build(c);results=[]
 for item in q['items']:
  if item.get('previous_status') not in (None,'MISSING','OUT_OF_SCENARIO'):continue
  if item['basis']!='QUOTE_PROXY' or item['provenance']!='PROSPECTIVE_LOCAL' or item['state'] in ('WAIT_ENTRY','AMBIGUOUS_LABEL'):continue
  draft=item['draft'];review=draft['review'];found=None
  for rid in review['evidence_ids']:
   try:found=(rid,*auction(c,rid,item['code'],review['entry_at']));break
   except (ValueError,KeyError,OSError):continue
  if not found:continue
  rid,r,gap=found;state='NEEDS_EXECUTION_REVIEW'
  result={'day':item['day'],'code':item['code'],'snapshot_id':item['snapshot_id'],'raw_id':rid,'entry_price':r['price'],'previous_close':r['pre_close'],'gap':gap,'state':state,'entry_policy':'ANY_EXECUTABLE_AUCTION'}
  if item.get('previous_status')=='OUT_OF_SCENARIO':
   evidence=dict(reviewer=VERSION,reason='2026-10-02用户取消跳空限制；重开执行性审核。旧条件预测保留，不凭报价宣称成交。',evidence_ids=[rid],entry_at=review['entry_at'],exit_at=review['exit_at'],entry_price=r['price'],previous_close=r['pre_close'],gap=gap,entry_policy='ANY_EXECUTABLE_AUCTION',supersedes=review['supersedes'])
   result['label_id']=store.label(c,item['snapshot_id'],item['code'],'QUOTE_PROXY','MISSING',evidence)
  results.append(result)
 report={'version':VERSION,'at':lab.now(),'rows':results,'automatic_settlements':0}
 store.put(c,'scenario_audit',store.digest(report),report);return report
