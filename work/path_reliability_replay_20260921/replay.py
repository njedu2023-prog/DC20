"""Read-only source replay and abstention experiment; never activates a model.

Sensitivity is NOT accuracy. All evaluated dates are development observations.
Only D-and-earlier inputs are exposed to the blind-review sample generator.
"""
import argparse
import concurrent.futures
import copy
import csv
import hashlib
import io
import json
import math
from collections import Counter
from pathlib import Path
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
WEIGHTS = (.25, .30, .20, .25)


def number(v):
    try:
        n = float(v)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def minute(v):
    # CSV/pandas numeric HHMMSS values may include a trailing .0.
    n = number(v)
    if n is None or n != int(n):
        return None
    h, rem = divmod(int(n), 10000)
    m, s = divmod(rem, 100)
    if not (0 <= h < 24 and 0 <= m < 60 and 0 <= s < 60):
        return None
    value = h * 60 + m + s / 60
    return value if 565 <= value <= 900 else None


def clip(v):
    return min(1., max(0., v))


def strength(s):
    parts = [(v, w) for v, w in zip(s['components'], WEIGHTS) if v is not None]
    return sum(v*w for v, w in parts)/sum(w for _, w in parts) if parts else None


def snapshot(daily, limit, detail):
    pre, op, up = [number(x) for x in (daily.get('pre_close'), daily.get('open'), limit.get('up_limit'))]
    gap = op/pre-1 if pre and pre > 0 and op and op > 0 else None
    ratio = up/pre-1 if pre and pre > 0 and up and up > 0 else None
    if ratio is None or not .03 <= ratio <= .35:
        ratio = .10  # Match incumbent fallback, disclosed separately.
    first, last = minute(detail.get('first_time')), minute(detail.get('last_time'))
    opens = number(detail.get('open_times'))
    if opens is not None and (opens < 0 or opens != int(opens)):
        opens = None
    amount = number(detail.get('amount'))
    if not amount or amount <= 0:
        amount = (number(daily.get('amount')) or 0)*1000
    seal = number(detail.get('seal_amount'))
    if seal is None:
        seal = number(detail.get('fd_amount'))
    seal_ratio = seal/amount if seal is not None and seal > 0 and amount > 0 else None
    parts = [clip((gap/max(ratio,.03)+.25)/1.25) if gap is not None else None,
             clip((900-first)/330) if first is not None else None,
             1/(1+opens) if opens is not None else None,
             clip(math.log1p(100*seal_ratio)/math.log(11)) if seal_ratio is not None else None]
    return dict(components=parts, gap=gap, first=first, last=last, opens=opens,
                seal_ratio=seal_ratio, coverage=sum(w for v,w in zip(parts,WEIGHTS) if v is not None))


def slope(seq, field):
    points = [(i,s[field]) for i,s in enumerate(seq) if s[field] is not None]
    return (points[-1][1]-points[0][1])/(points[-1][0]-points[0][0]) if len(points)>1 else 0.


def classify(seq):
    if len(seq)<2 or sum(s['coverage'] for s in seq)/len(seq)<.35:
        return 'INSUFFICIENT'
    prev, cur = strength(seq[-2]), strength(seq[-1])
    if prev is None or cur is None:
        return 'INSUFFICIENT'
    delta = cur-prev
    votes = sum((slope(seq,'gap')>=.005, slope(seq,'first')<=-10, slope(seq,'opens')<=-.5))
    if prev>=.60 and cur<.60 and delta<=-.12:
        return 'STRONG_TO_WEAK'
    if prev<.58 and cur>=.58 and delta>=.12:
        return 'WEAK_TO_STRONG'
    if prev>=.58 and cur>=.70 and delta>=.05 and votes>=2:
        return 'ACCELERATION_CONSENSUS'
    if (seq[-1]['opens'] or 0)>=1 and slope(seq,'opens')>0 and cur>=.45:
        return 'DIVERGENCE_RESEAL'
    if prev>=.65 and cur>=.65 and abs(delta)<.12:
        return 'STABLE_STRONG'
    return 'MIXED'


def assess(seq):
    baseline = classify(seq)
    if not seq or any(s['coverage']<1-1e-9 for s in seq):
        return dict(baseline=baseline,candidate='INSUFFICIENT',reason='INCOMPLETE',flip_fraction=None)
    variants = []
    # Predeclared, one-at-a-time component perturbation, not fitted to outcomes.
    for i in range(len(seq)):
        for j in range(4):
            for step in (-.02,.02):
                changed = copy.deepcopy(seq)
                changed[i]['components'][j] = clip(changed[i]['components'][j]+step)
                variants.append(classify(changed))
    flips = sum(label!=baseline for label in variants)
    # Last-first is only a timestamp span, NOT total open duration.
    inconsistent = any(s['first'] is None or s['last'] is None or s['last']<s['first'] for s in seq)
    reason = 'INVALID_TIME_ORDER' if inconsistent else 'BOUNDARY_SENSITIVE' if flips else 'STABLE_UNDER_DECLARED_STRESS'
    return dict(baseline=baseline,candidate='UNCERTAIN' if flips or inconsistent else baseline,
                reason=reason,flip_fraction=flips/len(variants))


def load_sources(pins):
    def fetch(item):
        path, expected = item
        url = 'https://raw.githubusercontent.com/njedu2023-prog/DC20/'+pins['source_commit']+'/'+path
        with urlopen(url,timeout=60) as response:
            raw=response.read()
        blob=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        if blob!=expected:
            raise ValueError('SOURCE_HASH_MISMATCH:'+path)
        rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
        if path==pins['calendar']['path']:
            return ('calendar','calendar'),rows
        date=path.split('/')[-2]
        if any(r.get('trade_date')!=date for r in rows):
            raise ValueError('DATE_MISMATCH:'+path)
        bycode={r['ts_code']:r for r in rows}
        if len(bycode)!=len(rows):
            raise ValueError('DUPLICATE_CODE:'+path)
        return (date,Path(path).stem),bycode
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        items=list(pins['files'].items())+[(pins['calendar']['path'],pins['calendar']['blob'])]
        return dict(pool.map(fetch,items))


def evaluate(data):
    dates=sorted({d for d,_ in data if d!='calendar'})
    expected=[r['cal_date'] for r in data[('calendar','calendar')]
              if r['is_open']=='1' and dates[0]<=r['cal_date']<=dates[-1]]
    if dates!=sorted(set(expected)):
        raise ValueError('MISSING_TRADING_DAY_IN_SOURCE_GRID')
    records=[]
    rejected=Counter()
    for i,date in enumerate(dates):
        for code, detail in data[(date,'limit_list_d')].items():
            stage=number(detail.get('limit_times'))
            if stage not in (2,3) or detail.get('limit_type')!='U':
                continue
            stage=int(stage)
            if i<stage:  # Need preceding non-streak day to establish exact 2/3 boards.
                rejected['LEFT_CENSORED']+=1
                continue
            window=dates[i-stage+1:i+1]
            seq=[]
            for d in window:
                daily=data[(d,'daily')].get(code,{})
                limit=data[(d,'stk_limit')].get(code,{})
                det=data[(d,'limit_list_d')].get(code,{})
                close,up=number(daily.get('close')),number(limit.get('up_limit'))
                if close is None or up is None or abs(close-up)>.005 or det.get('limit_type')!='U':
                    break
                seq.append(snapshot(daily,limit,det))
            before=dates[i-stage]
            prev=data[(before,'limit_list_d')].get(code,{})
            if len(seq)!=stage or prev.get('limit_type')=='U':
                rejected['STREAK_NOT_CONFIRMED']+=1
                continue
            records.append(dict(signal_date=date,ts_code=code,stage=stage,days=window,
                                sessions=seq,**assess(seq)))
    summaries={}
    for stage in (2,3):
        rows=[r for r in records if r['stage']==stage]
        complete=[r for r in rows if r['flip_fraction'] is not None]
        summaries[str(stage)]=dict(rows=len(rows),complete=len(complete),
            sensitivity_flagged=sum(r['flip_fraction']>0 for r in complete),
            candidate_retained=sum(r['candidate'] not in ('UNCERTAIN','INSUFFICIENT') for r in rows),
            reasons=dict(Counter(r['reason'] for r in rows)),
            baseline_labels=dict(Counter(r['baseline'] for r in rows)))
    # Deterministic sample, blind to rule output and future price/outcomes.
    ordered=sorted(records,key=lambda r:hashlib.sha256((r['signal_date']+r['ts_code']).encode()).hexdigest())
    blind=[{k:r[k] for k in ('signal_date','ts_code','stage','days','sessions')} for r in ordered[:60]]
    for row in blind:
        row.update(reviewer_a=None,reviewer_b=None,adjudicated_label=None,evidence_notes=None)
    return dict(schema='path_source_replay_v1',research_only=True,production_activation_allowed=False,
        accuracy=None,accuracy_reason='NO_INDEPENDENT_ADJUDICATED_REFERENCE_LABELS',
        source_date_count=len(dates),source_start=dates[0],source_end=dates[-1],
        summaries=summaries,excluded=dict(rejected),
        warning='STABILITY_IS_NOT_ACCURACY; SELECTIVE_ABSTENTION_REDUCES_COVERAGE; NOT_FUTURE_HOLDOUT'),records,blind


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():
        raise ValueError('FRESH_OUTPUT_REQUIRED')
    pins=json.loads((HERE/'source_pins.json').read_text())
    report,records,blind=evaluate(load_sources(pins))
    report.update(source_commit=pins['source_commit'],source_file_count=len(pins['files']),
                  calendar_verified=True,
                  script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    a.output.mkdir(parents=True)
    for name,value in [('report',report),('records',records),('blind_review',blind)]:
        (a.output/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
