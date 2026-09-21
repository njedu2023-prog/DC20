"""Build outcome-blind raw-evidence packets. Minute proxies are not tick truth."""
import concurrent.futures
import csv
import hashlib
import io
import json
import argparse
from pathlib import Path
if __package__:
    from .replay import HERE,number
else:  # Preserve direct script execution as well as package-based CI imports.
    from replay import HERE,number

EXPECTED={f'{m//60:02}:{m%60:02}:00' for m in list(range(571,691))+list(range(781,901))}


def fetch(repo,commit,path,blob):
    from urllib.request import urlopen
    url=f'https://raw.githubusercontent.com/{repo}/{commit}/{path}'
    with urlopen(url,timeout=60) as r:
        raw=r.read()
    if hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=blob:
        raise ValueError('HASH_MISMATCH:'+path)
    return list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))


def inspect(rows,date,code,up):
    date_iso=f'{date[:4]}-{date[4:6]}-{date[6:]}'
    times=[]
    bytime={}
    invalid=0
    for row in rows:
        stamp=row.get('trade_time','')
        if row.get('ts_code')!=code or stamp[:10]!=date_iso:
            raise ValueError('WRONG_DATE_OR_CODE')
        time=stamp[11:]
        if time in bytime:
            raise ValueError('DUPLICATE_MINUTE')
        bytime[time]=row
        if time not in EXPECTED:
            continue
        times.append(time)
        o,h,l,c,v=[number(row.get(k)) for k in ('open','high','low','close','vol')]
        if None in (o,h,l,c,v) or not (0<l<=min(o,c)<=max(o,c)<=h and v>=0):
            invalid+=1
    valid=up is not None and up>0
    active=[bytime[t] for t in sorted(times) if (number(bytime[t].get('vol')) or 0)>0]
    touches=[r['trade_time'][11:] for r in active if valid and number(r.get('high')) is not None and abs(float(r['high'])-up)<=.005]
    departures=0
    comparable_pairs=0
    for m in (list(range(572,691))+list(range(782,901))):
        a,b=(bytime.get(f'{k//60:02}:{k%60:02}:00') for k in (m-1,m))
        if not valid or not a or not b or any((number(r.get('vol')) or 0)<=0 for r in (a,b)):
            continue
        ca,cb=number(a.get('close')),number(b.get('close'))
        if ca is not None and cb is not None:
            comparable_pairs+=1
        if ca is not None and cb is not None and abs(ca-up)<=.005 and cb<up-.005:
            departures+=1
    unexpected=sorted(set(bytime)-EXPECTED-{'09:30:00'})
    return dict(status='COMPLETE_MINUTE_PROXY' if len(times)==240 and invalid==0 and valid and not unexpected else 'INCOMPLETE_OR_INVALID',
        unexpected_times=unexpected,
        continuous_rows=len(times),missing_minutes=sorted(EXPECTED-set(times)),invalid_ohlcv_rows=invalid,
        auction_0930_present='09:30:00' in bytime,zero_volume_minutes=sum((number(bytime[t].get('vol')) or 0)==0 for t in times),
        first_active_trade_touch_bar=touches[0] if touches else None,
        adjacent_active_close_departures=departures if valid and invalid==0 and comparable_pairs else None,
        comparable_active_pairs=comparable_pairs,
        true_open_count=None,true_seal_duration=None,
        limitation='OHLC does not reveal within-minute order or order-book sealing; zero-volume bars are not proof of trades')


def packet(samples,pins,minute_data,limits):
    output=[]
    for row in samples:
        sessions=[]
        for day,old in zip(row['days'],row['sessions']):
            path=f'data/raw/{day[:4]}/{day}/minute/1min/{row["ts_code"]}.csv'
            limitpath=f'data/market/raw/{day[:4]}/{day}/stk_limit.csv'
            up=number(limits[day].get(row['ts_code'],{}).get('up_limit'))
            evidence=dict(day=day,opening_gap=old['gap'],first_seal_minute_of_day=old['first'],
                last_seal_minute_of_day=old['last'],reported_open_times=old['opens'],
                seal_amount_to_turnover=old['seal_ratio'],up_limit=up,
                minute_source_url=f'https://github.com/{pins["source_repo"]}/blob/{pins["source_commit"]}/{path}' if path in pins['files'] else None,
                minute_blob=pins['files'].get(path),limit_blob=pins['limit_files'][limitpath])
            evidence['minute_check']=inspect(minute_data[path],day,row['ts_code'],up) if path in minute_data else {'status':'MISSING_SOURCE'}
            sessions.append(evidence)
        output.append(dict(sample_id=hashlib.sha256((row['signal_date']+row['ts_code']).encode()).hexdigest()[:16],
            signal_date=row['signal_date'],ts_code=row['ts_code'],stage=row['stage'],sessions=sessions,
            reviewer_a=None,reviewer_b=None,adjudicated_label=None,evidence_notes=None,independence_attested=False))
    return output


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():
        raise ValueError('FRESH_OUTPUT_REQUIRED')
    pins=json.loads((HERE/'minute_review_pins.json').read_text())
    raw=(HERE/'verified_results/blind_review.json').read_bytes()
    if hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=pins['blind_review_git_blob']:
        raise ValueError('BLIND_COHORT_CHANGED')
    samples=json.loads(raw)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        minute_data=dict(zip(pins['files'],pool.map(lambda kv:fetch(pins['source_repo'],pins['source_commit'],*kv),pins['files'].items())))
        raw_limits=pool.map(lambda kv:(kv[0].split('/')[-2],fetch('njedu2023-prog/DC20',pins['limit_source_commit'],*kv)),pins['limit_files'].items())
        limits={}
        for day,rows in raw_limits:
            if any(r.get('trade_date')!=day for r in rows) or len({r['ts_code'] for r in rows})!=len(rows):
                raise ValueError('INVALID_LIMIT_SOURCE')
            limits[day]={r['ts_code']:r for r in rows}
    packets=packet(samples,pins,minute_data,limits)
    unique={(s['day'],r['ts_code']):s for r in packets for s in r['sessions']}
    from collections import Counter
    report=dict(samples=len(packets),unique_sessions=len(unique),status_counts=dict(Counter(s['minute_check']['status'] for s in unique.values())),
        fully_minute_supported_samples=sum(all(s['minute_check']['status']=='COMPLETE_MINUTE_PROXY' for s in r['sessions']) for r in packets),
        zero_volume_session_count=sum(s['minute_check'].get('zero_volume_minutes',0)>0 for s in unique.values()),
        accuracy=None,production_activation_allowed=False,labels_filled=0,
        source_commit=pins['source_commit'],script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    a.output.mkdir(parents=True)
    for name,value in [('blind_packets',packets),('report',report)]:
        (a.output/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
