"""Bounded all-candidate counterfactual collection, isolated from production.

Uses the unchanged native label kernel and official source projection helpers.
Provider bodies exist only in temporary storage, never in public JSON/Git.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time

from scripts.build_profit_research import build, encoded, sha, OUT, SNAPS
from scripts.profit_research_extensions import ENTRY_FIELDS

def collect(root, revision, max_calls=180, seconds=540):
    # Import guards in these existing modules enforce the frozen economics.
    from work.profit_1000_upgrade import candidate_natural_outcome_collect as c
    from work.profit_1000_upgrade import candidate_natural_outcomes as o
    labels=c.labels
    root=Path(root).resolve(); dataset,_=build(root,revision); asof=dataset['as_of_date']
    if datetime.now(timezone.utc)<labels._timestamp(labels._at(asof,'15:00:00')):
        raise ValueError('ASOF_NOT_CLOSED')
    token=os.environ.get('TUSHARE_TOKEN','')
    if not token: return {'status':'CREDENTIAL_ABSENT','calls':0,'completed':0}
    calpath=labels.settlement.CALENDAR_PATH; calraw=(root/calpath).read_bytes()
    if sha(calraw)!=labels.settlement.CALENDAR_SHA256: raise ValueError('CALENDAR_CHANGED')
    dates=labels.settlement._strict_open_dates(root)
    guard=c.code_guard(); began=time.monotonic(); calls=completed=0; failures=[]
    # Recent live dates have priority: unavailable old quotes must not starve new evidence.
    for day in reversed(dataset['days']):
        for row in day['records']:
            dest=root/OUT/'labels'/day['date']/(row['ts_code']+'.json')
            if row['training_pair_ready'] or not row['profit_eligible'] or row['provenance']!='FORMAL_FROZEN_REFERENCE': continue
            if dest.exists(): continue
            t,t1=row['exec_date'],row['scheduled_exit_date']
            if not t or t>asof: continue
            if calls+3>max_calls or time.monotonic()-began+75>seconds: break
            snapraw=(root/SNAPS/f"day_{day['date']}.json").read_bytes(); frozen=json.loads(snapraw)
            expected=next(b['sha256'] for b in day['source_bindings'] if b['path'].startswith(SNAPS+'/'))
            if sha(snapraw)!=expected: raise ValueError('SNAPSHOT_CHANGED')
            code=row['ts_code']; snaprelative=f"research_inputs/candidate_natural_forward/day_{day['date']}.json"
            with tempfile.TemporaryDirectory(prefix='dc20-research-quotes-') as td:
                staging=Path(td); bodies={str(calpath):calraw,snaprelative:snapraw}; request_log=[]; attempted=set()
                target=t; report=None
                for _ in range(12):
                    if target not in dates or not t<=target<=asof: break
                    apis=('daily','stk_limit','stk_auction' if target==t else 'stk_mins')
                    if calls+3>max_calls or time.monotonic()-began+75>seconds: break
                    ok=True
                    for api in apis:
                        ident=(api,target,code)
                        if ident in attempted: continue
                        attempted.add(ident); contract=c.request_contract(api,target,code)
                        calls+=1
                        try:
                            raw=c.official_call(contract); c.safe_response(raw,token)
                            stamp=datetime.now(timezone.utc).isoformat()
                            if api in c.FIELDS:
                                projected=c.csv_projection(raw,contract); pairs=[] if projected is None else [projected]
                            elif api=='stk_auction':
                                pairs=o.auction_http_v3.source_bytes(raw,target,request=contract,fetched_at_utc=stamp,network_request_performed=True,token=token)
                            else:
                                pairs=labels.minute_truth.source_bytes(raw,target,code,request_params=contract['params'],fetched_at_utc=stamp,token=token)
                            request_log.append({'api':api,'date':target,'ts_code':code,'response_sha256':sha(raw),'fetched_at':stamp})
                            for path,body in zip(c.source_paths(api,target,code),pairs):
                                if token.encode() in body: raise ValueError('SECRET_IN_RESPONSE')
                                bodies[path]=body
                        except Exception:
                            ok=False; failures.append({'date':day['date'],'code':code,'status':'SOURCE_PENDING_OR_REJECTED'})
                        time.sleep(.5)
                    for p,b in bodies.items():
                        f=staging/p; f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(b)
                    manifest=o._manifest(frozen,{'ts_code':code,'board_stage':row['board_stage'],'promotion_rank':row['promotion_rank']},{p:sha(b) for p,b in bodies.items()})
                    report=labels.build_labels(staging,manifest,as_of_date=asof)
                    native=report['rows'][0]; labels.policy_v3.validate_label_contract(native)
                    if native['ts_code']!=code or native['signal_date']!=day['date']: raise ValueError('LABEL_IDENTITY_CHANGED')
                    if native['label_status'] in o.TERMINAL:
                        safe={'schema_version':'dc20_research_counterfactual_label_v1','signal_date':day['date'],
                            'ts_code':code,'snapshot_sha256':expected,'model_sha256':row['model_sha256'],
                            'as_of_date':asof,'status':native['label_status'],'proxy_fill':native['proxy_fill'],
                            'net_return':native['slot_net_return'],'label_available_date':native['label_available_date'],
                            'actual_exit_date':native.get('actual_exit_date'),
                            'basis':'COUNTERFACTUAL_NATIVE_POLICY','actual_execution_claimed':False,
                            'entry_policy_id':dataset['model']['entry_policy_id'],'exit_policy_id':dataset['model']['label_policy_id'],
                            'round_trip_cost_rate':dataset['model']['round_trip_cost_rate'],
                            'source_bindings':[{'path':p,'sha256':sha(b)} for p,b in sorted(bodies.items())],
                            'request_receipts':request_log,'source_revision':revision,
                            'raw_provider_data_public':False,'collector_test_only':False,
                            'entry_observation':{k:native.get(k) for k in ENTRY_FIELDS
                                                 if native.get(k) is not None}}
                        if c.code_guard()!=guard: raise ValueError('CODE_CHANGED')
                        dest.parent.mkdir(parents=True,exist_ok=True)
                        with dest.open('xb') as f:f.write(encoded(safe))
                        completed+=1;break
                    if not ok: break
                    missing=native.get('missing_evidence_date')
                    if missing and missing!=target: target=missing
                    elif target==t and t1<=asof: target=t1
                    else:
                        future=[d for d in dates if target<d<=asof]
                        if not future: break
                        target=future[0]
    return {'status':'BOUNDED_COLLECTION_COMPLETE','calls':calls,'completed':completed,'source_failures':len(failures)}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',default='.');p.add_argument('--revision',required=True)
    a=p.parse_args();print(json.dumps(collect(a.root,a.revision),ensure_ascii=False))
