"""Prepare missing P0 history as a separate, source-bound data commit.

Never alter P0, models, existing predictions, or the live feature window. P0 is
started by the next delivery tick on the REAL new main, not on staged files.
Only absent/empty history may be restored; existing frozen bindings must match.
"""
from __future__ import annotations
import argparse
import base64
import csv
from datetime import datetime, time, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import urllib.request

REPO = 'njedu2023-prog/DC20'
MARKET = 'njedu2023-prog/a-share-top3-data'
WORKFLOW = '.github/workflows/prepare_primary_history.yml'
TABLES = ('daily', 'daily_basic', 'limit_list_d', 'stk_limit', 'stock_basic')
REQUIRED = frozenset(TABLES) - {'limit_list_d'}
TZ = timezone(timedelta(hours=8))
MAX_BYTES = 8 * 1024**2


def require(value, reason):
    if not value:
        raise ValueError('HISTORY_' + reason)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def blob_sha(raw):
    return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()


def window(day, now):
    require(now.tzinfo is not None, 'AWARE_CLOCK_REQUIRED')
    local = now.astimezone(TZ)
    require(re.fullmatch(r'20[0-9]{6}', day) and local.strftime('%Y%m%d') == day
            and time(15) < local.time() < time(23,30), 'SAME_D_PREPARATION_WINDOW_REQUIRED')


def dates(calendar, day):
    reader = csv.DictReader(io.StringIO(calendar.decode('utf-8-sig')))
    require(reader.fieldnames == ['exchange','cal_date','is_open','pretrade_date'], 'CALENDAR_COLUMNS')
    rows = list(reader)
    opened, previous, last = [], None, ''
    for row in rows:
        date = row['cal_date']
        require(row['exchange'] == 'SSE' and re.fullmatch(r'20[0-9]{6}',date)
                and date > last and row['is_open'] in ('0','1'), 'CALENDAR_INVALID')
        if previous is not None:
            require(row['pretrade_date'] == previous, 'CALENDAR_CHAIN_CHANGED')
        if row['is_open'] == '1':
            opened.append(date)
            previous = date
        last = date
    require(day in opened, 'NONTRADING_D_REJECTED')
    pos = opened.index(day)
    require(pos >= 20 and pos+2 < len(opened), 'CALENDAR_HISTORY_OR_FUTURE_MISSING')
    return opened[pos-20:pos]


def inventory(client, head, *, repository=REPO):
    require(re.fullmatch('[0-9a-f]{40}',head), 'EXACT_HEAD_REQUIRED')
    tree = client.request('/git/trees/'+head+'?recursive=1', repository=repository)
    require(tree.get('truncated') is False and isinstance(tree.get('tree'),list), 'COMPLETE_TREE_REQUIRED')
    result = {}
    for entry in tree['tree']:
        path = entry['path']
        require(path not in result, 'TREE_PATH_DUPLICATED')
        result[path] = entry
    return result


def read_blob(client, entry, *, repository=REPO):
    require(entry and entry.get('type') == 'blob' and entry.get('mode') == '100644'
            and type(entry.get('size')) is int and 0 <= entry['size'] <= MAX_BYTES, 'REGULAR_BOUNDED_BLOB_REQUIRED')
    response = client.request('/git/blobs/'+entry['sha'], repository=repository)
    require(response.get('encoding') == 'base64', 'BLOB_ENCODING')
    raw = base64.b64decode(response['content'])
    require(blob_sha(raw) == entry['sha'] == response.get('sha')
            and len(raw) == entry['size'] == response.get('size'), 'BLOB_IDENTITY_CHANGED')
    return raw


def rows(raw):
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    return reader.fieldnames, list(reader)


def blank(raw):
    return not raw.decode('utf-8-sig').strip()


def is_empty(raw):
    if blank(raw):
        return True
    _, values = rows(raw)
    return not values


def valid_table(raw, day, table):
    header, values = rows(raw)
    require(header and 'ts_code' in header and len(header) == len(set(header)), 'SOURCE_COLUMNS_INVALID')
    if table in REQUIRED:
        require(values, 'UPSTREAM_REQUIRED_TABLE_EMPTY')
    require(all(None not in r and None not in r.values() for r in values), 'SOURCE_CSV_MALFORMED')
    codes = [r['ts_code'] for r in values]
    require(all(re.fullmatch(r'[0-9]{6}\.(?:SH|SZ|BJ)',c) for c in codes)
            and len(codes) == len(set(codes)), 'SOURCE_STOCK_IDENTITIES_INVALID')
    if table != 'stock_basic':
        require('trade_date' in header and all(r['trade_date'] == day for r in values), 'SOURCE_DATE_CHANGED')
    return True


def inspect_history(client, head, day):
    """Cheap missing/empty preflight. Does not write or grant P0 admission.

    Large, nonempty files are still authoritatively checked by P0. Small files
    are parsed to distinguish valid empty optional tables from required gaps.
    """
    tree = inventory(client, head)
    calendar = read_blob(client, tree.get('data/market/trade_cal_sse.csv'))
    missing = []
    for date in dates(calendar, day):
        for table in TABLES:
            path = f'data/market/raw/{date[:4]}/{date}/{table}.csv'
            entry = tree.get(path)
            if entry is None:
                missing.append(path)
                continue
            require(entry.get('type') == 'blob' and entry.get('mode') == '100644', 'UNSAFE_LOCAL_HISTORY')
            raw = read_blob(client,entry) if entry.get('size',0) <= 4096 else None
            if raw is not None and (blank(raw) or (table in REQUIRED and is_empty(raw))):
                missing.append(path)
    return {'needs_repair':bool(missing), 'missing_paths':missing, 'head':head, 'signal_date':day}


def _no_current_generation(tree, day):
    forbidden = [f'outputs/decision/primary_d_receipt_{day}.json',
        f'outputs/decision/primary_d_runtime_features_{day}.csv',
        f'outputs/decision/three_rank_top10_{day}.json',f'outputs/decision/three_rank_top10_{day}.csv',
        f'outputs/decision/candidate_profit_v1/day_{day}.json']
    prefix='work/profit_1000_upgrade/candidate_natural_forward/'
    forbidden += [prefix+f'{kind}_{day}.json' for kind in ('day','workflow','p0_sources','local_freeze')]
    forbidden += [f'work/profit_1000_upgrade/candidate_natural_evidence/{day}/context.json']
    require(not any(path in tree for path in forbidden), 'CURRENT_D_ALREADY_STARTED_NO_HISTORY_REWRITE')


def build_plan(client, head, day, market_commit, now, *, source_cutoff=None):
    window(day,now)
    require(re.fullmatch('[0-9a-f]{40}',market_commit), 'EXACT_MARKET_COMMIT_REQUIRED')
    require(client.request('/git/ref/heads/main')['object']['sha'] == head, 'MAIN_MOVED')
    source_commit = client.request('/commits/'+market_commit, repository=MARKET)
    stamp=datetime.fromisoformat(source_commit['commit']['committer']['date'].replace('Z','+00:00'))
    cutoff = source_cutoff if source_cutoff is not None else now
    require(cutoff.tzinfo is not None and cutoff <= now, 'SOURCE_CUTOFF_INVALID')
    require(source_commit.get('sha') == market_commit and stamp.tzinfo is not None and stamp <= cutoff,
            'UPSTREAM_COMMIT_FROM_FUTURE')
    tree, source = inventory(client,head), inventory(client,market_commit,repository=MARKET)
    _no_current_generation(tree,day)
    calendar=read_blob(client,tree.get('data/market/trade_cal_sse.csv'))
    history=dates(calendar,day)
    changes={}
    for date in history:
        for table in TABLES:
            path=f'data/market/raw/{date[:4]}/{date}/{table}.csv'
            entry=tree.get(path)
            old=read_blob(client,entry) if entry else None
            # Never silently overwrite malformed nonempty data or stale dates.
            if old is not None and not blank(old) and not (table in REQUIRED and is_empty(old)):
                valid_table(old,date,table)
                continue
            upstream_path=f'data/raw/{date[:4]}/{date}/{table}.csv'
            require(upstream_path in source,'UPSTREAM_HISTORY_MISSING')
            raw=read_blob(client,source[upstream_path],repository=MARKET)
            valid_table(raw,date,table)
            changes[path]={'raw':raw,'previous_blob_sha':entry['sha'] if entry else None,
                           'source_path':upstream_path,'source_blob_sha':source[upstream_path]['sha']}
    # Preserve every known prior P0 source binding. Exact restoration is allowed;
    # changing even an empty previously-bound source is not.
    receipts=[p for p in tree if re.fullmatch(r'outputs/decision/primary_d_receipt_20[0-9]{6}\.json',p)]
    require(len(receipts)<=1000,'RECEIPT_INVENTORY_EXCEEDED')
    def check_bound(value):
        if isinstance(value,dict):
            path=value.get('path')
            if path in changes and 'sha256' in value:
                require(value['sha256']==digest(changes[path]['raw']),'FROZEN_HISTORY_WOULD_CHANGE')
            for child in value.values(): check_bound(child)
        elif isinstance(value,list):
            for child in value: check_bound(child)
    if changes:
        for path in receipts:
            check_bound(json.loads(read_blob(client,tree[path])))
    audit={'schema_version':'dc20_primary_history_preparation_v1','signal_date':day,
        'base_head':head,'market_repository':MARKET,'market_commit':market_commit,
        'prepared_at_utc':now.isoformat(),'source_cutoff_utc':cutoff.isoformat(),
        'calendar_sha256':digest(calendar),
        'history_dates':history,'not_a_natural_freeze':True,'models_or_rankings_changed':False,
        'files':[{'path':p,'sha256':digest(v['raw']),'bytes':len(v['raw']),
                  **{k:v[k] for k in ('previous_blob_sha','source_path','source_blob_sha')}}
                 for p,v in sorted(changes.items())]}
    return {'audit':audit,'changes':changes}


def commit_plan(client, plan, *, clock):
    audit,changes=plan['audit'],plan['changes']
    if not changes:
        return {'status':'HISTORY_ALREADY_COMPLETE','head':audit['base_head'],'files':0}
    day,head=audit['signal_date'],audit['base_head']
    window(day,clock())
    require(client.request('/git/ref/heads/main')['object']['sha']==head,'MAIN_MOVED')
    commit=client.request('/git/commits/'+head)
    entries=[{'path':p,'mode':'100644','type':'blob','content':v['raw'].decode('utf-8')}
             for p,v in sorted(changes.items())]
    manifest=f'data/market/history_repairs/{day}_{head}.json'
    require(manifest not in inventory(client,head),'REPAIR_RECEIPT_ALREADY_EXISTS')
    entries.append({'path':manifest,'mode':'100644','type':'blob',
                    'content':json.dumps(audit,sort_keys=True,ensure_ascii=False,indent=2)+'\n'})
    new_tree=client.request('/git/trees',{'base_tree':commit['tree']['sha'],'tree':entries})
    new_commit=client.request('/git/commits',{'message':f'data: prepare exact P0 history for {day}',
        'tree':new_tree['sha'],'parents':[head]})
    require(client.request('/git/ref/heads/main')['object']['sha']==head,'MAIN_MOVED')
    window(day,clock())
    client.request('/git/refs/heads/main',{'sha':new_commit['sha'],'force':False},method='PATCH')
    require(client.request('/git/ref/heads/main')['object']['sha']==new_commit['sha'],'COMMIT_RESULT_RECHECK_REQUIRED')
    return {'status':'HISTORY_PREPARED_REDISPATCH_P0_ON_NEW_HEAD','head':new_commit['sha'],
            'base_head':head,'files':len(changes),'audit_path':manifest}


class Client:
    def __init__(self,token):
        require(bool(token),'TOKEN_REQUIRED')
        self.token,self.calls=token,0
    def request(self,path,body=None,*,repository=REPO,method=None):
        self.calls+=1
        require(self.calls<=1300 and repository in (REPO,MARKET),'REQUEST_BUDGET')
        if body is not None:
            require(repository==REPO and (path, method or 'POST') in {('/git/trees','POST'),
                ('/git/commits','POST'),('/git/refs/heads/main','PATCH')},'WRITE_TARGET')
            if path == '/git/refs/heads/main':
                require(body.get('force') is False and re.fullmatch('[0-9a-f]{40}',body.get('sha','')), 'NON_FORCE_REF_ONLY')
        req=urllib.request.Request('https://api.github.com/repos/'+repository+path,
            data=None if body is None else json.dumps(body).encode(), method=method,
            headers={'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github+json',
                     'X-GitHub-Api-Version':'2022-11-28'})
        with urllib.request.urlopen(req,timeout=30) as response: raw=response.read(32*1024**2+1)
        require(len(raw)<=32*1024**2,'RESPONSE_TOO_LARGE')
        return json.loads(raw)


def verify_run(client,head,day,now):
    require(os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('GITHUB_REPOSITORY')==REPO
            and os.environ.get('GITHUB_REF')=='refs/heads/main'
            and os.environ.get('GITHUB_WORKFLOW_REF')==REPO+'/'+WORKFLOW+'@refs/heads/main'
            and os.environ.get('GITHUB_RUN_ATTEMPT')=='1'
            and os.environ.get('GITHUB_EVENT_NAME')=='workflow_dispatch'
            and os.environ.get('GITHUB_SHA')==head,'REGISTERED_WORKFLOW_ONLY')
    run_id=os.environ.get('GITHUB_RUN_ID','')
    require(re.fullmatch('[1-9][0-9]*',run_id), 'RUN_ID_REQUIRED')
    run=client.request('/actions/runs/'+run_id)
    require(run['path']==WORKFLOW and run['event']=='workflow_dispatch' and run['head_sha']==head
            and run['head_branch']=='main' and run['run_attempt']==1
            and run['repository']['full_name']==REPO
            and run['head_repository']['full_name']==REPO,'ORIGINAL_DISPATCH_REQUIRED')
    created=datetime.fromisoformat(run['created_at'].replace('Z','+00:00'))
    window(day,created)
    require(created<=now,'RUN_TIME_INVALID')
    return created


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--signal-date',required=True)
    parser.add_argument('--expected-head',required=True)
    parser.add_argument('--market-commit',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    client=Client(os.environ.get('GH_TOKEN'))
    now=datetime.now(timezone.utc)
    receipt={'result':{'status':'BLOCKED'},'signal_date':args.signal_date,'base_head':args.expected_head}
    try:
        created=verify_run(client,args.expected_head,args.signal_date,now)
        plan=build_plan(client,args.expected_head,args.signal_date,args.market_commit,now,source_cutoff=created)
        receipt['audit']=plan['audit']
        receipt['result']=commit_plan(client,plan,clock=lambda:datetime.now(timezone.utc)) if args.execute else {'status':'DRY_RUN_NO_COMMIT'}
        print(json.dumps(receipt['result']))
    except Exception as exc:
        reason=str(exc) if re.fullmatch('[A-Z0-9_]+',str(exc)) else type(exc).__name__
        receipt['result']={'status':'BLOCKED','reason':reason}
        raise
    finally:
        Path(args.output).write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__': main()
