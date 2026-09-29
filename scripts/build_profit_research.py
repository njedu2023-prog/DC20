"""Read-only production adapter; writes only the isolated research dataset.

No fitting, source admission, production writes, price synthesis or reranking.
The public projection is an explicit allowlist, never a dump of provider files.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

START = '20260914'
MODEL_SHA = '999666791b147e4d120ba9b7e10d9d1fc846ba171efbba73b2488ca56ce6f589'
OUT = 'outputs/decision/profit_research'
TERMINAL = {'SETTLED_1000_LIMIT_HOLD_MINUTE_PROXY','NO_FILL_CANONICAL_AUCTION_ZERO_VOLUME',
            'NO_FILL_SUSPENDED','NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED','NO_FILL_CAPACITY'}
SNAPS = 'work/profit_1000_upgrade/candidate_natural_forward'
NUMERIC = ('promotion_probability','promotion_rank','path_change','d_pct_change','volume_ratio',
 'ret_2d','ret_5d','ret_10d','volatility_5d','volatility_20d','stage_pool_share',
 'five_year_board_stage_delta','five_year_streak_runup','five_year_pre_streak_1d_return',
 'five_year_recent_20d_rate','five_year_recent_60d_rate','five_year_stock_prior_rate',
 'focus_pool_size','stage2_pool_size','stage3_pool_size')

def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()

def sha(raw): return hashlib.sha256(raw).hexdigest()

def number(value):
    return value if type(value) in (int,float) and math.isfinite(value) else None

def terminal_valid(status, value, fill):
    if status not in TERMINAL or value is None: return False
    if status.startswith('NO_FILL_'):
        if fill != 0 or value != 0: raise ValueError('INVALID_NO_FILL_LABEL')
    elif fill != 1: raise ValueError('INVALID_SETTLED_LABEL')
    return True

def read(root, path, expected=None):
    p = root / path
    if not p.resolve().is_relative_to(root.resolve()) or p.is_symlink():
        raise ValueError('UNSAFE_SOURCE_PATH')
    raw = p.read_bytes()
    if expected and sha(raw) != expected:
        raise ValueError('SOURCE_SHA_MISMATCH:'+path)
    return json.loads(raw), {'path':path,'sha256':sha(raw)}

def keyed(rows):
    result = {}
    for r in rows:
        if r['ts_code'] in result: raise ValueError('DUPLICATE_STOCK')
        result[r['ts_code']] = r
    return result

def model_view(root):
    doc, source = read(root,'work/profit_1000_upgrade/candidate_natural_model/evaluation.json')
    m = doc['candidate_model']
    canonical = json.dumps(m, sort_keys=True, separators=(',',':'), allow_nan=False).encode()
    if sha(canonical) != MODEL_SHA: raise ValueError('FIXED_MODEL_CHANGED')
    keys = ('features','coefficients','intercept','imputation_train_medians','scaling_train_means',
            'scaling_train_scales','specification','fit_signal_date_min','fit_signal_date_max',
            'fit_label_available_date_max','round_trip_cost_rate','entry_policy_id','label_policy_id')
    return {**{k:m[k] for k in keys},'sha256':MODEL_SHA,'source':source,
            'formula':'score = intercept + sum(coefficient * (imputed_feature - train_mean) / train_scale)',
            'missing_contract':['promotion_probability','path_change','path_label'],
            'training_enabled':False,'automatic_promotion_enabled':False}

def build(root, revision):
    root = Path(root)
    summary, sb = read(root,'outputs/decision/candidate_profit_v1/summary.json')
    index, ib = read(root,'outputs/decision/candidate_profit_v1/index.json')
    asof = summary['as_of_date']
    if summary['model_canonical_sha256'] != MODEL_SHA: raise ValueError('SUMMARY_MODEL_CHANGED')
    # The official publication validator owns source admission. This adapter
    # checks its persisted references, never upgrades a reconstruction to formal.
    entries = {x['signal_date']:x for x in index['days']}
    outcomes = {}
    for group in summary['groups'].values():
        for row in group['daily_sequence']:
            if row.get('ts_code'):
                outcomes[(row['signal_date'],row['ts_code'])] = row
    with (root/'data/market/trade_cal_sse.csv').open() as f:
        cal = list(csv.DictReader(f))
    days = sorted({r.get('cal_date',r.get('trade_date','')) for r in cal
                   if str(r.get('is_open')) == '1' and START <= r.get('cal_date',r.get('trade_date','')) <= asof})
    result, snapshots, issues = [], {}, []
    for day in days:
        try:
            p0path = f'outputs/decision/three_rank_top10_{day}.json'
            p0, pb = read(root,p0path)
            members = p0['rows']
            if len(members)>10: raise ValueError('OUTSIDE_TOP10')
            keyed(members)
            provenance='RETROSPECTIVE_UNVERIFIED'; source_rows={}; scores={}; eligibility={}; bindings=[pb]
            model_sha=None; frozen_at=None; exec_date=p0.get('exec_date'); exit_date=p0.get('exit_date')
            if day in entries:
                e=entries[day]
                public, db=read(root,e.get('path',e.get('url',f'outputs/decision/candidate_profit_v1/day_{day}.json')),e.get('sha256'))
                if public['signal_date']!=day or public['model_canonical_sha256']!=MODEL_SHA:
                    raise ValueError('FORMAL_IDENTITY_MISMATCH')
                snap, fb=read(root,public['snapshot_source']['path'],public['snapshot_source']['sha256'])
                if pb['sha256']!=public['p0_source']['sha256']: raise ValueError('P0_BINDING_MISMATCH')
                if snap['signal_date']!=day: raise ValueError('SNAPSHOT_DATE_MISMATCH')
                source_rows=keyed(snap['D_source_evidence']['projection']['rows'])
                if set(source_rows)!=set(keyed(members)): raise ValueError('FEATURE_MEMBERSHIP_MISMATCH')
                scores=keyed(public['rows']); eligibility=keyed(public.get('eligibility',[]))
                provenance='FORMAL_FROZEN_REFERENCE'; model_sha=MODEL_SHA
                frozen_at=public['pre_cas_freeze_at_utc']; exec_date=public['exec_date']; exit_date=public['exit_date']
                bindings += [db,fb]
            records=[]
            for member in members:
                code=member['ts_code']; feature=source_rows.get(code); score=scores.get(code,{})
                eligible=eligibility.get(code,{}).get('eligible',bool(score))
                rec={'signal_date':day,'ts_code':code,'name':member.get('name',''),
                     'industry':member.get('industry',''),'promotion_rank':member['promotion_rank'],
                     'profit_rank':score.get('candidate_rank'),'score':number(score.get('candidate_score')),
                     'board_stage':feature.get('board_stage') if feature else member.get('stage'),
                     'features':{k:number(feature.get(k)) if feature else None for k in NUMERIC},
                     'feature_snapshot_present':bool(feature),'profit_eligible':eligible,
                     'eligibility_reason':eligibility.get(code,{}).get('reason') or (None if eligible else 'NO_FROZEN_PROFIT_SCORE'),
                     'provenance':provenance,'model_sha256':model_sha,'frozen_at':frozen_at,
                     'exec_date':exec_date,'scheduled_exit_date':exit_date}
                frozen=dict(rec)
                observation=outcomes.get((day,code),{})
                status=observation.get('status','PENDING_T' if exec_date and exec_date>asof else 'MISSING_COUNTERFACTUAL_LABEL')
                value=number(observation.get('slot_net_return'))
                terminal=terminal_valid(status,value,observation.get('proxy_fill'))
                if observation and observation.get('snapshot_file_sha256') != (bindings[-1]['sha256'] if source_rows else None):
                    raise ValueError('OUTCOME_SNAPSHOT_MISMATCH')
                rec['outcome']={'status':status,'net_return':value if terminal else None,
                    'proxy_fill':observation.get('proxy_fill'),'label_available_date':observation.get('label_available_date'),
                    'actual_exit_date':observation.get('actual_exit_date'),
                    'basis':'FORMAL_SELECTED_PROXY' if observation else 'COUNTERFACTUAL_NOT_YET_AVAILABLE',
                    'actual_execution_claimed':False}
                labelpath=f'{OUT}/labels/{day}/{code}.json'
                if not terminal and (root/labelpath).exists():
                    lab,lb=read(root,labelpath)
                    if (lab.get('schema_version')!='dc20_research_counterfactual_label_v1' or lab.get('signal_date')!=day
                        or lab.get('ts_code')!=code or lab.get('snapshot_sha256')!=(bindings[-1]['sha256'] if source_rows else None)
                        or lab.get('model_sha256')!=MODEL_SHA or lab.get('collector_test_only') is not False
                        or lab.get('entry_policy_id')!=summary['entry_policy_id'] or lab.get('exit_policy_id')!=summary['exit_policy_id']
                        or lab.get('round_trip_cost_rate')!=summary['round_trip_cost_rate']):
                        raise ValueError('COUNTERFACTUAL_LABEL_BINDING_MISMATCH')
                    value=number(lab.get('net_return')); status=lab.get('status','')
                    terminal=terminal_valid(status,value,lab.get('proxy_fill'))
                    observation=lab
                    rec['outcome']={k:lab.get(k) for k in ('status','net_return','proxy_fill','label_available_date','actual_exit_date','basis','actual_execution_claimed')}
                    rec['outcome']['source_binding']=lb
                rec['training_pair_ready']=bool(provenance=='FORMAL_FROZEN_REFERENCE' and feature and eligible and terminal
                    and observation.get('label_available_date') and observation['label_available_date']<=asof)
                records.append(rec)
                key=f'{day}/{code}'
                snapshots[key]={'record':frozen,'source_bindings':bindings}
            ready=bool(records) and all(r['training_pair_ready'] for r in records if r['profit_eligible']) and any(r['profit_eligible'] for r in records)
            result.append({'date':day,'provenance':provenance,'count':len(records),
                'feature_count':sum(r['feature_snapshot_present'] for r in records),
                'ready_pair_count':sum(r['training_pair_ready'] for r in records),
                'mature_training_day':ready,'records':records,'source_bindings':bindings})
        except (OSError,ValueError,KeyError,TypeError) as exc:
            issues.append({'date':day,'code':str(exc)[:140]})
            result.append({'date':day,'provenance':'SOURCE_ERROR','count':0,'feature_count':0,
                           'ready_pair_count':0,'mature_training_day':False,'records':[],'source_bindings':[]})
    model=model_view(root)
    allrows=[r for d in result for r in d['records']]
    mature=sum(d['mature_training_day'] for d in result)
    return {'schema_version':'dc20_profit_research_v1','source_revision':revision,'as_of_date':asof,
        'start_date':START,'source_bindings':[sb,ib], 'model':model,'days':result,'issues':issues,
        'counts':{'trading_days':len(days),'candidate_records':len(allrows),
          'unique_stocks':len({r['ts_code'] for r in allrows}),
          'frozen_feature_records':sum(r['feature_snapshot_present'] for r in allrows),
          'ready_pairs':sum(r['training_pair_ready'] for r in allrows),'mature_training_days':mature,
          'missing_counterfactual_labels':sum(r['outcome']['status']=='MISSING_COUNTERFACTUAL_LABEL' for r in allrows)},
        'training_gate':{'first_review_days':60,'upgrade_review_days':120,'mature_days':mature,
          'status':'REVIEW_ELIGIBLE_MANUAL_ONLY' if mature>=60 else 'ACCUMULATING',
          'training_enabled':False,'automatic_promotion_enabled':False},
        'training_runs':[],
        'limitations':['全候选收益标签未齐的日期不计入成熟训练日。','未选中候选收益不以日开盘收益替代竞价策略收益。',
          '60/120天是评估提示，不是自动训练或上线许可。','事后重建不等于盘前冻结，未买入不计成交胜率。']}, snapshots

def write(root, revision):
    root=Path(root); doc,snapshots=build(root,revision); out=root/OUT
    out.mkdir(parents=True,exist_ok=True)
    for key, value in snapshots.items():
        raw=encoded(value); p=out/'snapshots'/key.split('/')[0]/(key.split('/')[1]+'.json')
        if p.exists() and p.read_bytes()!=raw: raise ValueError('IMMUTABLE_RESEARCH_SNAPSHOT_CHANGED:'+key)
        p.parent.mkdir(parents=True,exist_ok=True)
        if not p.exists(): p.write_bytes(raw)
    version=encoded(doc); digest=sha(version)
    p=out/'versions'/f'{digest}.json'; p.parent.mkdir(exist_ok=True)
    if not p.exists(): p.write_bytes(version)
    (out/'latest.json').write_bytes(encoded({'schema_version':'dc20_profit_research_pointer_v1',
        'path':f'versions/{digest}.json','sha256':digest,'as_of_date':doc['as_of_date'],
        'source_revision':revision}))
    return doc

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--root',default='.'); parser.add_argument('--revision',required=True)
    args=parser.parse_args(); print(json.dumps(write(args.root,args.revision)['counts'],ensure_ascii=False))
