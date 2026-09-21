"""Objective paired development audit; never writes rankings or activates models."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
KEY=['signal_date','ts_code']
SEED=20260921


def verify_blob(path,expected):
    raw=path.read_bytes()
    if hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=expected:
        raise ValueError('INPUT_CHANGED:'+str(path))


def validate_frames(frames,target):
    truth=['proxy_fill','slot_net_return'] if target=='profit' else ['promotion_hit']
    reference=None
    for name,frame in frames.items():
        if frame.duplicated(KEY).any() or not frame.signal_date.str.fullmatch(r'\d{8}').all():
            raise ValueError('INVALID_KEYS')
        if not np.isfinite(frame[['score','rank']+truth].to_numpy(dtype=float)).all():
            raise ValueError('MISSING_OR_NONFINITE_NOT_ZERO')
        binary='proxy_fill' if target=='profit' else 'promotion_hit'
        if not frame[binary].isin([0,1]).all():
            raise ValueError('INVALID_TRUTH')
        if target=='profit' and not frame.loc[frame.proxy_fill==0,'slot_net_return'].eq(0).all():
            raise ValueError('NO_FILL_CASH_RETURN_MUST_BE_ZERO')
        for _,group in frame.groupby('signal_date'):
            if sorted(group['rank'])!=list(range(1,len(group)+1)):
                raise ValueError('INVALID_RANKS')
            ordered=group.sort_values(['score','ts_code'],ascending=[False,True])
            if ordered['rank'].tolist()!=list(range(1,len(group)+1)):
                raise ValueError('RANK_SCORE_MISMATCH')
        actual=frame.set_index(KEY)[truth].sort_index()
        if reference is not None and not actual.equals(reference):
            raise ValueError('COHORT_OR_TRUTH_MISMATCH')
        reference=actual


def seat(frame,rank):
    return frame.loc[frame['rank']==rank].sort_values('signal_date').set_index('signal_date')


def mean_or_none(values):
    return float(np.mean(values)) if len(values) else None


def profit_metrics(frame):
    out={}
    for rank in (1,2):
        rows=seat(frame,rank)
        filled=rows[rows.proxy_fill==1]
        out[str(rank)]=dict(selected=len(rows),filled=len(filled),no_fill=int((rows.proxy_fill==0).sum()),
            mean_slot_net=mean_or_none(rows.slot_net_return),
            mean_filled_net=mean_or_none(filled.slot_net_return),
            filled_win_rate=mean_or_none(filled.slot_net_return>0),
            mean_filled_net_90bp=mean_or_none(filled.slot_net_return-.0045),
            mean_slot_net_90bp=mean_or_none(rows.slot_net_return-.0045*rows.proxy_fill),
            worst_filled_net=float(filled.slot_net_return.min()) if len(filled) else None)
    return out


def paired_blocks(a,b,field):
    if not a.index.equals(b.index):
        raise ValueError('PAIRED_DATES_MISMATCH')
    delta=(b[field]-a[field]).to_numpy()
    if not len(delta):
        return dict(n=0,mean_delta=None,interval98_75=None)
    rng=np.random.default_rng(SEED)
    draws=[]
    for _ in range(4000):
        starts=rng.integers(0,len(delta),size=(len(delta)+4)//5)
        indices=((starts[:,None]+np.arange(5))%len(delta)).ravel()[:len(delta)]
        draws.append(delta[indices].mean())
    # Four profit comparisons: two variants times two seats. Exploratory correction only.
    return dict(n=len(delta),mean_delta=float(delta.mean()),
                interval98_75=np.quantile(draws,[.00625,.99375]).tolist())


def evaluate(profit,promotion):
    validate_frames(profit,'profit')
    validate_frames(promotion,'promotion')
    dates=sorted(profit['A'].signal_date.unique())
    if any(len(seat(f,rank))!=len(dates) for f in profit.values() for rank in (1,2)):
        raise ValueError('TOP2_DATES_INCOMPLETE')
    metrics={k:profit_metrics(f) for k,f in profit.items()}
    comparisons={}
    for name in ('B','C'):
        slots={str(rank):paired_blocks(seat(profit['A'],rank),seat(profit[name],rank),'slot_net_return') for rank in (1,2)}
        periods=[]
        for block in np.array_split(dates,3):
            a=profit_metrics(profit['A'][profit['A'].signal_date.isin(block)])
            b=profit_metrics(profit[name][profit[name].signal_date.isin(block)])
            periods.append(dict(start=str(block[0]),end=str(block[-1]),
                mean_slot_deltas={str(rank):b[str(rank)]['mean_slot_net']-a[str(rank)]['mean_slot_net'] for rank in (1,2)}))
        gates=dict(both_slot_means_improve=all(x['mean_delta']>0 for x in slots.values()),
            both_corrected_intervals_above_zero=all(x['interval98_75'][0]>0 for x in slots.values()),
            both_filled_means_positive_at_90bp=all(metrics[name][str(rank)]['mean_filled_net_90bp'] is not None and metrics[name][str(rank)]['mean_filled_net_90bp']>0 for rank in (1,2)),
            each_slot_improves_in_at_least_two_periods=all(sum(p['mean_slot_deltas'][str(rank)]>0 for p in periods)>=2 for rank in (1,2)),
            untouched_forward_evidence=False)
        comparisons[name]=dict(paired_slots=slots,periods=periods,gates=gates,eligible_for_activation=False)
    promo={}
    for name,f in promotion.items():
        promo[name]={str(rank):dict(n=len(seat(f,rank)),hit_rate=mean_or_none(seat(f,rank).promotion_hit)) for rank in (1,2,3)}
    return dict(schema='objective_path_validation_v1',source_role='INSPECTED_DEVELOPMENT_ONLY',
        research_only=True,independent_improvement_proven=False,production_activation_allowed=False,
        start=dates[0],end=dates[-1],dates=len(dates),
        exit_policy='dc20_exit_1000_limit_hold_20260912_v1',cost_rate=.0045,
        profit=metrics,comparisons=comparisons,promotion=promo,
        caveats=['no-fill zero is cash-slot opportunity return, not zero-return fill',
                 'block intervals remain exploratory after earlier inspection',
                 'no portfolio NAV computed: overlapping holding periods require cash-flow simulation',
                 'no feature selection or refit on this evaluation window'])


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--repo-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.exists():raise ValueError('FRESH_OUTPUT_REQUIRED')
    pins=json.loads((HERE/'source_pins.json').read_text())
    for path,blob in pins['files'].items():verify_blob(args.repo_root/path,blob)
    root=args.repo_root/'work/path_feature_upgrade_20260921/results'
    original=json.loads((root/'report.json').read_text())
    if original['profit']['exit_policy_id']!='dc20_exit_1000_limit_hold_20260912_v1' or original['profit']['round_trip_cost_rate']!=.0045:
        raise ValueError('POLICY_OR_COST_MISMATCH')
    def read(kind):
        return {v:pd.read_csv(root/f'{kind}_{v}_predictions.csv',dtype={'signal_date':str}) for v in ('A','B','C')}
    report=evaluate(read('profit'),read('promotion'))
    report.update(source_commit=pins['source_commit'],script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    args.output.mkdir(parents=True)
    (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
