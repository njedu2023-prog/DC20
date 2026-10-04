"""Index existing archival minute/auction evidence; no training or settlement claims."""
import argparse,csv,hashlib,json,math
from pathlib import Path

def inspect(path):
    raw=path.read_bytes();sha=hashlib.sha256(raw).hexdigest();d=json.loads(raw);mp=path.with_name(path.name.replace('.data.json','.meta.json'));meta=json.loads(mp.read_text()) if mp.exists() else {};fields=d.get('fields',[]);items=d.get('items',[]);issues=[]
    if meta.get('data_sha256')!=sha:issues.append('HASH_UNVERIFIED')
    if d.get('has_more'):issues.append('PAGINATED')
    if any(len(r)!=len(fields) for r in items):issues.append('ROW_WIDTH')
    return sha,meta,[dict(zip(fields,r)) for r in items],issues

def inventory(minute_root,auction_root,out):
    if out.exists():raise ValueError('Use new output folder')
    out.mkdir(parents=True);seen=set();records=[];duplicates=0
    paths=list(minute_root.glob('**/minute_truth_0931/**/*.data.json'))+list(auction_root.glob('**/stk_auction.data.json'))
    for path in paths:
        sha,meta,rows,issues=inspect(path)
        if sha in seen:duplicates+=1;continue
        seen.add(sha);kind='minute' if 'trade_time' in (rows[0] if rows else {}) else 'auction';day=meta.get('trade_date');code=meta.get('ts_code');point=[]
        if kind=='minute':
            keys=[(r.get('ts_code'),r.get('trade_time')) for r in rows]
            if len(keys)!=len(set(keys)):issues.append('DUPLICATES')
            if any(r.get('ts_code')!=code or str(r.get('trade_time',''))[:10].replace('-','')!=day for r in rows):issues.append('IDENTITY_DATE_MISMATCH')
            point=[r for r in rows if str(r.get('trade_time','')).endswith('10:00:00')]
            if len(point)!=1:issues.append('NO_UNIQUE_1000')
            elif not isinstance(point[0].get('close'),(float,int)) or not math.isfinite(point[0]['close']) or point[0]['close']<=0:issues.append('INVALID_PRICE')
            if not meta.get('provider_timestamp_semantics_confirmed'):issues.append('BAR_TIME_SEMANTICS_UNCONFIRMED')
        else:
            if any(r.get('trade_date')!=day for r in rows):issues.append('DATE_MISMATCH')
            if not meta.get('actual_capacity_verified'):issues.append('FILL_CAPACITY_UNVERIFIED')
        records.append(dict(path=str(path.resolve()),sha256=sha,kind=kind,day=day,code=code,rows=len(rows),provider=meta.get('source'),captured_at=meta.get('fetched_at_utc'),point1000=point,issues=issues,eligible_for_strategy_validation=False))
    summary=dict(files_scanned=len(paths),unique_payloads=len(records),duplicate_payloads=duplicates,minute_with1000=sum(bool(r['point1000']) for r in records),auction_days=len({r['day'] for r in records if r['kind']=='auction'}),eligible_strategy_results=0,note='Historical targeted/replay selection, timestamp semantics and fill evidence are not validated. These are archival research inputs, not prospective performance samples.')
    (out/'inventory.json').write_text(json.dumps(records,ensure_ascii=False,indent=2));(out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));return summary
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--minute-root',type=Path,required=True);p.add_argument('--auction-root',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();print(json.dumps(inventory(a.minute_root,a.auction_root,a.out)))
