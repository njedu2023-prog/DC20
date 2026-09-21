"""Expanded development stress audit. No fitting, outcomes, or activation.

Perturbations are hypothetical error scenarios, NOT a probability distribution.
"""
import argparse
import copy
import hashlib
import itertools
import json
import math
from collections import Counter
from pathlib import Path
if __package__:
    from .replay import HERE, assess, classify, clip, evaluate, load_sources, minute, number, snapshot
else:  # Preserve direct script execution as well as package-based CI imports.
    from replay import HERE, assess, classify, clip, evaluate, load_sources, minute, number, snapshot


def joint_labels(seq):
    # All 256 last-two-session component corners; earlier session slopes unchanged.
    for shifts in itertools.product((-.02,.02),repeat=8):
        changed=copy.deepcopy(seq)
        for k,shift in enumerate(shifts):
            day=len(seq)-2+k//4
            part=k%4
            changed[day]['components'][part]=clip(changed[day]['components'][part]+shift)
        yield classify(changed)


def clock(minutes):
    total=round(minutes*60)
    h,rem=divmod(total,3600)
    m,s=divmod(rem,60)
    return f'{h:02}{m:02}{s:02}'


def raw_variants(daily,limit,detail):
    for kind in ('open_tick','first_seal_minute','open_count','seal_amount_10pct'):
        for sign in (-1,1):
            d,l,x=copy.deepcopy((daily,limit,detail))
            if kind=='open_tick':
                value=number(d.get('open'))
                low,high=number(l.get('down_limit')),number(l.get('up_limit'))
                if value is None or low is None or high is None:
                    continue
                low=max(low,number(d.get('low')) if number(d.get('low')) is not None else low)
                high=min(high,number(d.get('high')) if number(d.get('high')) is not None else high)
                value=round(value+sign*.01,2)
                if not low<=value<=high:
                    continue
                d['open']=value
            elif kind=='first_seal_minute':
                first,last=minute(x.get('first_time')),minute(x.get('last_time'))
                if first is None or last is None:
                    continue
                value=first+sign
                if number(x.get('open_times'))==0 and first==last:
                    last=value
                    x['last_time']=clock(value)
                if not (565<=value<=min(last,900) and (value<=690 or value>=780)):
                    continue
                x['first_time']=clock(value)
            elif kind=='open_count':
                value=number(x.get('open_times'))
                if value is None or value+sign<0:
                    continue
                # Do not invent zero opens while distinct first/last times persist.
                if value+sign==0 and minute(x.get('first_time'))!=minute(x.get('last_time')):
                    continue
                x['open_times']=value+sign
            else:
                field='seal_amount' if number(x.get('seal_amount')) is not None else 'fd_amount'
                value=number(x.get(field))
                if value is None or value<=0:
                    continue
                x[field]=value*(1+sign*.1)
            yield kind,snapshot(d,l,x)


def stress_record(row,data):
    seq=row['sessions']
    baseline=classify(seq)
    if any(s['coverage']<1-1e-9 for s in seq):
        return dict(signal_date=row['signal_date'],ts_code=row['ts_code'],stage=row['stage'],status='INCOMPLETE')
    joint=Counter(joint_labels(seq))
    causes=Counter()
    counts=Counter()
    alternatives=Counter()
    for i,date in enumerate(row['days']):
        code=row['ts_code']
        raw=[data[(date,t)][code] for t in ('daily','stk_limit','limit_list_d')]
        if snapshot(*raw)!=seq[i]:
            raise ValueError('RAW_REPLAY_CHANGED')
        for kind,replacement in raw_variants(*raw):
            altered=copy.deepcopy(seq)
            altered[i]=replacement
            result=classify(altered)
            counts[kind]+=1
            if result!=baseline:
                causes[kind]+=1
                alternatives[result]+=1
    prior=assess(seq)
    joint_flip=sum(n for label,n in joint.items() if label!=baseline)
    flagged=prior['candidate'] in ('UNCERTAIN','INSUFFICIENT') or joint_flip>0 or bool(causes)
    without_count=prior['candidate'] in ('UNCERTAIN','INSUFFICIENT') or joint_flip>0 or any(k!='open_count' for k in causes)
    return dict(signal_date=row['signal_date'],ts_code=row['ts_code'],stage=row['stage'],
                baseline=baseline,prior_sensitive=prior['candidate']=='UNCERTAIN',
                joint_flips=joint_flip,joint_cases=sum(joint.values()),
                joint_alternatives=dict(joint),raw_cases=dict(counts),raw_flip_causes=dict(causes),
                raw_alternatives=dict(alternatives),candidate='UNCERTAIN' if flagged else baseline,
                non_count_stress_retained=not without_count)


def summarize(rows):
    out={}
    for stage in (2,3):
        part=[r for r in rows if r['stage']==stage]
        cause_rows=Counter(k for r in part for k in r.get('raw_flip_causes',{}))
        out[str(stage)]=dict(rows=len(part),prior_sensitive=sum(r.get('prior_sensitive',False) for r in part),
            joint_sensitive=sum(r.get('joint_flips',0)>0 for r in part),
            raw_sensitive=sum(bool(r.get('raw_flip_causes')) for r in part),
            raw_cause_rows=dict(cause_rows),
            retained_excluding_count_shock=sum(r.get('non_count_stress_retained',False) for r in part),
            retained=sum(r.get('candidate') not in (None,'UNCERTAIN','INSUFFICIENT') for r in part))
    return out


def rate_interval(hits,n):
    if not n:
        return dict(n=0,hits=0,rate=None,wilson95=None)
    p=hits/n
    z=1.959963984540054
    den=1+z*z/n
    centre=(p+z*z/(2*n))/den
    half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return dict(n=n,hits=hits,rate=p,wilson95=[centre-half,centre+half])


def outcome_diagnostic(rows,data):
    # Outcome is appended AFTER fixed D-only stress, never used to choose thresholds.
    dates=sorted(d for d,t in data if t=='daily')
    groups={}
    pending=Counter()
    for r in rows:
        pos=dates.index(r['signal_date'])
        if pos+1==len(dates):
            pending['FUTURE_DATE_UNAVAILABLE']+=1
            continue
        t=dates[pos+1]
        daily=data[(t,'daily')].get(r['ts_code'],{})
        limits=data[(t,'stk_limit')].get(r['ts_code'],{})
        close,up,vol=[number(v) for v in (daily.get('close'),limits.get('up_limit'),daily.get('vol'))]
        if close is None or up is None or vol is None or vol<=0:
            pending['QUOTE_MISSING_OR_SUSPENDED']+=1
            continue
        hit=abs(close-up)<=.005
        stage=str(r['stage'])
        names=['all','label:'+r['baseline'],
               'non_count_retained' if r['non_count_stress_retained'] else 'non_count_flagged']
        for name in names:
            groups.setdefault(stage,{}).setdefault(name,[]).append(hit)
    return dict(groups={stage:{name:rate_interval(sum(values),len(values))
                for name,values in byname.items()} for stage,byname in groups.items()},
                excluded=dict(pending),
                warning='POST_HOC_DEVELOPMENT_DESCRIPTION_NOT_CAUSAL_OR_LABEL_ACCURACY; WILSON_IGNORES_DAY_CLUSTERING')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():
        raise ValueError('FRESH_OUTPUT_REQUIRED')
    pins=json.loads((HERE/'source_pins.json').read_text())
    data=load_sources(pins)
    _,records,_=evaluate(data)
    rows=[stress_record(r,data) for r in records]
    report=dict(schema='path_expanded_stress_v1',research_only=True,production_activation_allowed=False,
                source_commit=pins['source_commit'],accuracy=None,profit_improvement=None,
                evidence='DEVELOPMENT_SENSITIVITY_NOT_INDEPENDENT_ACCURACY',
                script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                summaries=summarize(rows),promotion_diagnostic=outcome_diagnostic(rows,data))
    a.output.mkdir(parents=True)
    for name,value in [('report',report),('records',rows)]:
        (a.output/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
