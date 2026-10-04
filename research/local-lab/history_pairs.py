"""Prepare explicitly exploratory price pairs; never populate performance tables."""
import csv,json,math,hashlib
from collections import defaultdict
from pathlib import Path

def build(inventory_path,calendar_path,out):
 if out.exists():raise ValueError('Use fresh pair file')
 inv=json.loads(inventory_path.read_text());cal={r['cal_date']:r for r in csv.DictReader(calendar_path.read_text(encoding='utf-8-sig').splitlines())};auctions=defaultdict(list);mins=defaultdict(list)
 for r in inv:
  if r['kind']=='auction':auctions[r['day']].append(r)
  else:mins[(r['code'],r['day'])].append(r)
 cache={};pairs=[];excluded=defaultdict(int)
 for (code,exit_day),versions in mins.items():
  if len(versions)!=1:excluded['conflicting_minute_versions']+=1;continue
  r=versions[0];day=cal.get(exit_day,{})
  if day.get('is_open')!='1':excluded['calendar_missing_or_closed']+=1;continue
  entry_day=day['pretrade_date'];av=auctions.get(entry_day);a=av[0] if av else None
  if av and len(av)!=1:excluded['conflicting_auction_versions']+=1;continue
  if not a:excluded['entry_auction_day_absent']+=1;continue
  if set(r['issues'])-{'BAR_TIME_SEMANTICS_UNCONFIRMED'} or set(a['issues'])-{'FILL_CAPACITY_UNVERIFIED'}:excluded['quality_failure']+=1;continue
  if entry_day not in cache:
   data=json.loads(Path(a['path']).read_text());cache[entry_day]={x[0]:dict(zip(data['fields'],x)) for x in data['items']}
  row=cache[entry_day].get(code)
  if not row or not all(isinstance(row.get(k),(int,float)) and math.isfinite(row[k]) and row[k]>0 for k in ('price','vol','amount')):excluded['auction_price_or_volume_absent']+=1;continue
  point=r['point1000'][0];pairs.append(dict(code=code,entry_day=entry_day,exit_day=exit_day,auction_price=row['price'],bar1000_close=point['close'],auction_artifact_sha=a['sha256'],minute_artifact_sha=r['sha256'],calendar_sha=hashlib.sha256(calendar_path.read_bytes()).hexdigest(),status='EXPLORATORY_PRICE_PAIR_ONLY',limitations=['minute timestamp semantics unconfirmed','buy/sell execution not proven','historically targeted sample, no frozen D universe','no point-in-time feature dataset'],eligible_for_calibration=False))
 result={'pairs':pairs,'summary':{'price_pairs':len(pairs),'unique_entry_days':len({p['entry_day'] for p in pairs}),'excluded':dict(excluded),'calibration_samples':0},'calendar_source':str(calendar_path.resolve())};out.write_text(json.dumps(result,ensure_ascii=False,indent=2));return result['summary']
if __name__=='__main__':
 import argparse;p=argparse.ArgumentParser();p.add_argument('inventory',type=Path);p.add_argument('calendar',type=Path);p.add_argument('output',type=Path);a=p.parse_args();print(json.dumps(build(a.inventory,a.calendar,a.output)))
