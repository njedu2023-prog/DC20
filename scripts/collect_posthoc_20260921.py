"""Finite, manually authorized evidence collection; never a forward ledger writer."""
from __future__ import annotations
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import time

from work.profit_1000_upgrade import candidate_natural_outcome_collect as collector
from work.profit_1000_upgrade import auction_truth_v3 as auction, minute_truth
from top10decision.decision import executable_profit_shadow_settlement as settlement
from top10decision.decision.shadow_exit_1000 import resolve_exit_1000

ROOT = Path(__file__).resolve().parents[1]
CODES = ((1, '000910.SZ', '大亚圣象'), (2, '002589.SZ', '瑞康医药'))
DAYS = ('20260922', '20260923', '20260924')
COST = .0045


def sha(raw): return hashlib.sha256(raw).hexdigest()
def encoded(value): return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()


def qualify_entry(daily, limits, evidence):
    """Keep the existing auction/zero-volume/limit/capacity entry gates."""
    prices = {k: float(daily[k]) for k in ('open','high','low','close','pre_close')}
    import math
    vol, up, down = float(daily['vol']), float(limits['up_limit']), float(limits['down_limit'])
    if not all(math.isfinite(v) and v > 0 for v in prices.values()) or not math.isfinite(vol) or vol < 0 or not 0 < down < up:
        raise ValueError('INVALID_ENTRY_DAILY')
    if not down <= prices['low'] <= min(prices['open'],prices['close']) <= max(prices['open'],prices['close']) <= prices['high'] <= up:
        raise ValueError('INVALID_ENTRY_BOUNDS')
    same = settlement._same_rounded_price
    if vol == 0 and (not all(same(prices['close'],v) for k,v in prices.items() if k != 'pre_close') or evidence['auction_trade_observed'] is True):
        raise ValueError('ENTRY_SOURCE_CONFLICT')
    if evidence['status'] == 'OBSERVED_NO_AUCTION_TRADE': return 'NO_FILL_CANONICAL_AUCTION_ZERO_VOLUME'
    if vol == 0: return 'NO_FILL_SUSPENDED'
    if evidence['status'] != 'CANONICAL_PRICE_OBSERVED' or evidence['price_qualified'] is not True:
        return 'PENDING_CANONICAL_ENTRY_QUALIFICATION'
    if not same(evidence['price'],prices['open']): raise ValueError('ENTRY_SOURCE_CONFLICT')
    if same(evidence['price'],up): return 'NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED'
    if evidence['capacity_proxy_verified'] is True and evidence['amount'] is not None and evidence['amount'] * settlement.MAX_AUCTION_PARTICIPATION + 1e-9 < settlement.SHADOW_NOTIONAL_CNY:
        return 'NO_FILL_CAPACITY'
    return 'QUALIFIED_RESEARCH_PROXY'


def run(output):
    if output.exists(): raise ValueError('EXCLUSIVE_OUTPUT_REQUIRED')
    if ROOT == output or ROOT in output.parents: raise ValueError('OUTPUT_MUST_BE_OUTSIDE_CHECKOUT')
    if datetime.now(timezone.utc) < datetime(2026,9,24,7,tzinfo=timezone.utc): raise ValueError('SOURCE_NOT_CLOSED')
    token = os.environ.get('TUSHARE_TOKEN','')
    if not token: raise ValueError('CREDENTIAL_ABSENT')
    output.mkdir(parents=True)
    bindings, failures = [], []
    def write(relative, raw):
        if token.encode() in raw: raise ValueError('SENSITIVE_OUTPUT')
        path = output/relative
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as f: f.write(raw)
        bindings.append({'path':relative,'sha256':sha(raw),'bytes':len(raw)})
    calendar = (ROOT/'data/market/trade_cal_sse.csv').read_bytes()
    dates = [r['cal_date'] for r in csv.DictReader(io.StringIO(calendar.decode('utf-8-sig'))) if r['exchange']=='SSE' and r['is_open']=='1']
    start = dates.index('20260921')
    if dates[start+1:start+4] != list(DAYS): raise ValueError('CALENDAR_CONFLICT')
    write('trade_cal_sse.csv',calendar)
    for _,code,_ in CODES:
        contracts = [collector.request_contract(api,day,code) for day in DAYS for api in ('daily','stk_limit')]
        contracts += [collector.request_contract('stk_auction','20260922',code)]
        contracts += [collector.request_contract('stk_mins',day,code) for day in DAYS[1:]]
        for contract in contracts:
            api,day,_ = collector.request_identity(contract)
            try:
                raw = collector.official_call(contract)
                collector.safe_response(raw,token)
                fetched = datetime.now(timezone.utc).isoformat()
                if api in ('daily','stk_limit'):
                    body = collector.csv_projection(raw,contract)
                    if body is None: raise ValueError('EMPTY_EXACT_DAY_TABLE')
                    write(f'{code}/data/{day}/{api}.csv',body)
                    write(f'{code}/data/{day}/{api}.receipt.json',encoded({'request':contract,'fetched_at_utc':fetched,'http_response_sha256':sha(raw),'projection_sha256':sha(body),'network_request_performed':True}))
                elif api == 'stk_auction':
                    pair = auction.source_bytes(raw,day,request=contract,fetched_at_utc=fetched,network_request_performed=True,token=token)
                    for p,b in zip(auction.source_paths(output/code,day),pair): write(p.relative_to(output).as_posix(),b)
                else:
                    pair = minute_truth.source_bytes(raw,day,code,request_params=contract['params'],fetched_at_utc=fetched,token=token)
                    for p,b in zip(minute_truth.paths(output/code,day,code),pair): write(p.relative_to(output).as_posix(),b)
            except Exception as exc:
                # No provider text or secret-bearing exception is persisted.
                failures.append({'api':api,'date':day,'code':code,'error_type':type(exc).__name__})
            time.sleep(.6)
    rows=[]
    for rank,code,name in CODES:
        root=output/code
        row={'signal_date':'20260921','exec_date':'20260922','scheduled_exit_date':'20260923','slot_rank':rank,'ts_code':code,'name':name,
             'status':'PENDING_SOURCE_EVIDENCE','entry_price':None,'exit_price':None,'net_return':None,'actual_exit_date':None,
             'formal_statistics_eligible':False,'actual_execution_claimed':False}
        rows.append(row)
        def table(day,kind):
            path=root/f'data/{day}/{kind}.csv'
            raw=path.read_bytes(); data=list(csv.DictReader(io.StringIO(raw.decode())))
            if len(data)!=1 or data[0]['ts_code']!=code or data[0]['trade_date']!=day: raise ValueError('EXACT_SOURCE_REQUIRED')
            return data[0],{'path':path.relative_to(root).as_posix(),'sha256':sha(raw)}
        try:
            daily,binding=table('20260922','daily'); limits,_=table('20260922','stk_limit')
            evidence=auction.entry_price(auction.load(root,'20260922'),'20260922',code,float(daily['open']),daily_source_binding=binding)
            row.update(entry_price=evidence['price'],entry_evidence=evidence)
            row['status']=qualify_entry(daily,limits,evidence)
            if row['status']!='QUALIFIED_RESEARCH_PROXY': continue
            result,status=resolve_exit_1000(dates,'20260923','20260924',code,evidence['price'],float(daily['close']),
                lambda day:table(day,'daily')[0],lambda day:table(day,'stk_limit')[0],lambda day:minute_truth.load(root,day,code))
            row['status']=status
            if result is not None:
                row.update(status='POSTHOC_SETTLED_MINUTE_PROXY',exit_evidence=result,exit_price=result['exit_price'],net_return=result['gross_return']-COST,actual_exit_date=result['actual_exit_date'])
        except Exception as exc: row.update(status='PENDING_SOURCE_EVIDENCE',error_type=type(exc).__name__)
    result={'schema_version':'dc20_posthoc_settlement_20260921_v1','classification':'POSTHOC_RESEARCH_NOT_FORMAL',
            'formal_statistics_eligible':False,'actual_execution_claimed':False,'production_activation_allowed':False,
            'minute_time_semantics':minute_truth.TIME_SEMANTICS,'round_trip_cost_rate':COST,'as_of_date':'20260924',
            'generated_at_utc':datetime.now(timezone.utc).isoformat(),'workflow_run_id':os.environ.get('GITHUB_RUN_ID'),
            'source_commit':os.environ.get('GITHUB_SHA'),'rows':rows,'failures':failures,'source_files':bindings.copy()}
    write('settlement.json',encoded(result))
    print(json.dumps({'statuses':[r['status'] for r in rows],'source_failures':len(failures)}))

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--output',type=Path,required=True)
    run(p.parse_args().output.resolve())
