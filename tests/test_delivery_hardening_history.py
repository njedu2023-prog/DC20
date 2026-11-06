"""Fault injection for data-only preparation; no API or natural freeze writes."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import pytest
from scripts import prepare_primary_history as m

HEAD='1'*40
MARKET='2'*40
DAY='20261106'
NOW=datetime(2026,11,6,12,tzinfo=timezone.utc)


def calendar():
    start=datetime(2026,9,21)
    previous='20260918'
    result=['exchange,cal_date,is_open,pretrade_date']
    for n in range(52):
        dt=start+timedelta(days=n); d=dt.strftime('%Y%m%d'); opened=int(dt.weekday()<5)
        result.append(f'SSE,{d},{opened},{previous}')
        if opened: previous=d
    return ('\n'.join(result)+'\n').encode()


def table(day,name):
    if name=='stock_basic': return b'ts_code,name\n000001.SZ,fixture\n'
    return f'ts_code,trade_date,close\n000001.SZ,{day},10.0\n'.encode()


class Fake:
    def __init__(self):
        self.raw={}; self.trees={m.REPO:{},m.MARKET:{}}; self.head=HEAD; self.writes=[]
        self.put('data/market/trade_cal_sse.csv',calendar())
        self.history=m.dates(calendar(),DAY)
        for day in self.history:
            for name in m.TABLES:
                self.put(f'data/market/raw/{day[:4]}/{day}/{name}.csv',table(day,name))
                self.put(f'data/raw/{day[:4]}/{day}/{name}.csv',table(day,name),m.MARKET)
    def put(self,path,raw,repo=m.REPO):
        sha=m.blob_sha(raw); self.raw[sha]=raw
        self.trees[repo][path]={'path':path,'type':'blob','mode':'100644','sha':sha,'size':len(raw)}
    def request(self,path,body=None,*,repository=m.REPO,method=None):
        if body is not None:
            self.writes.append((path,deepcopy(body)))
            if path=='/git/trees':
                for entry in body['tree']: self.put(entry['path'],entry['content'].encode())
                return {'sha':'3'*40}
            if path=='/git/commits': return {'sha':'4'*40}
            if path=='/git/refs/heads/main':
                assert method=='PATCH' and body['force'] is False
                self.head=body['sha'];return {'object':{'sha':self.head}}
            raise AssertionError(path)
        if path=='/git/ref/heads/main': return {'object':{'sha':self.head}}
        if path.startswith('/git/trees/'):return {'truncated':False,'tree':list(self.trees[repository].values())}
        if path.startswith('/git/blobs/'):
            sha=path.split('/')[-1];raw=self.raw[sha]
            return {'sha':sha,'size':len(raw),'encoding':'base64','content':base64.b64encode(raw).decode()}
        if path=='/commits/'+MARKET:return {'sha':MARKET,'commit':{'committer':{'date':'2026-11-06T11:00:00Z'}}}
        if path=='/git/commits/'+HEAD:return {'tree':{'sha':'5'*40}}
        raise AssertionError(path)


def plan(fake): return m.build_plan(fake,HEAD,DAY,MARKET,NOW)


def test_complete_history_never_rewrites_or_dispatches():
    f=Fake(); original=deepcopy(f.trees)
    assert not m.inspect_history(f,HEAD,DAY)['needs_repair']
    p=plan(f);assert p['changes']=={}
    assert m.commit_plan(f,p,clock=lambda:NOW)['status']=='HISTORY_ALREADY_COMPLETE'
    assert f.trees==original and f.writes==[]


@pytest.mark.parametrize('damage',['missing','zero','header_only'])
def test_missing_or_empty_required_file_restored_before_fresh_head(damage):
    f=Fake();path=f'data/market/raw/2026/{f.history[3]}/daily.csv'
    original=f.trees[m.REPO][path]
    if damage=='missing': del f.trees[m.REPO][path]
    else: f.put(path,b'' if damage=='zero' else b'ts_code,trade_date,close\n')
    assert m.inspect_history(f,HEAD,DAY)['missing_paths']==[path]
    before=deepcopy(f.trees[m.REPO]);p=plan(f)
    assert list(p['changes'])==[path]
    result=m.commit_plan(f,p,clock=lambda:NOW)
    assert result['status']=='HISTORY_PREPARED_REDISPATCH_P0_ON_NEW_HEAD'
    assert f.trees[m.REPO][path]==original
    assert all(f.trees[m.REPO][k]==v for k,v in before.items() if k!=path)
    assert len(set(f.trees[m.REPO])-set(before))==(2 if damage=='missing' else 1)
    assert all('/dispatches' not in path for path,_ in f.writes)
    assert not m.inspect_history(f,result['head'],DAY)['needs_repair']


def test_missing_optional_table_copies_real_empty_header_not_invented():
    f=Fake();d=f.history[0];path=f'data/market/raw/2026/{d}/limit_list_d.csv'
    del f.trees[m.REPO][path]
    f.put(f'data/raw/2026/{d}/limit_list_d.csv',b'ts_code,trade_date\n',m.MARKET)
    p=plan(f);assert p['changes'][path]['raw']==b'ts_code,trade_date\n'


@pytest.mark.parametrize('name',['daily','limit_list_d'])
@pytest.mark.parametrize('raw',[b'\n \t\r\n', b'\xef\xbb\xbf', b'\xef\xbb\xbf\n '])
def test_whitespace_and_bom_only_tables_are_restored(name,raw):
    f=Fake();d=f.history[2];path=f'data/market/raw/2026/{d}/{name}.csv'
    f.put(path,raw)
    assert m.inspect_history(f,HEAD,DAY)['missing_paths']==[path]
    assert plan(f)['changes'][path]['raw']==table(d,name)


def test_optional_header_only_is_valid_not_a_missing_day():
    f=Fake();d=f.history[2];path=f'data/market/raw/2026/{d}/limit_list_d.csv'
    f.put(path,b'ts_code,trade_date\n')
    assert not m.inspect_history(f,HEAD,DAY)['needs_repair']
    assert not plan(f)['changes']


def test_upstream_missing_never_commits_partial_repairs():
    f=Fake();d=f.history[2]
    del f.trees[m.REPO][f'data/market/raw/2026/{d}/daily.csv']
    del f.trees[m.MARKET][f'data/raw/2026/{d}/daily.csv']
    with pytest.raises(ValueError,match='UPSTREAM_HISTORY_MISSING'):plan(f)
    assert not f.writes


@pytest.mark.parametrize('kind',['wrong_day','empty','duplicate_code'])
def test_upstream_invalid_never_admitted(kind):
    f=Fake();d=f.history[2];path=f'data/market/raw/2026/{d}/daily.csv'
    del f.trees[m.REPO][path]
    raw=table('20261109','daily') if kind=='wrong_day' else (b'ts_code,trade_date\n' if kind=='empty' else table(d,'daily')+f'000001.SZ,{d},10\n'.encode())
    f.put(f'data/raw/2026/{d}/daily.csv',raw,m.MARKET)
    with pytest.raises(ValueError):plan(f)
    assert not f.writes


def test_nonempty_bad_history_is_not_overwritten():
    f=Fake();d=f.history[2]
    f.put(f'data/market/raw/2026/{d}/daily.csv',table('20261109','daily'))
    with pytest.raises(ValueError,match='SOURCE_DATE_CHANGED'):plan(f)
    assert not f.writes


@pytest.mark.parametrize('matching',[True,False])
def test_prior_frozen_source_only_exact_restoration_allowed(matching):
    f=Fake();d=f.history[2];path=f'data/market/raw/2026/{d}/daily.csv'
    raw=table(d,'daily');del f.trees[m.REPO][path]
    binding={'path':path,'sha256':m.digest(raw) if matching else '0'*64}
    f.put('outputs/decision/primary_d_receipt_20261105.json',json.dumps({'inputs':{'files':[binding]}}).encode())
    if matching:assert plan(f)['changes'][path]['raw']==raw
    else:
        with pytest.raises(ValueError,match='FROZEN_HISTORY_WOULD_CHANGE'):plan(f)
    assert not f.writes


@pytest.mark.parametrize('hour,minute,day',[(15,0,DAY),(23,30,DAY),(1,0,'20261107')])
def test_actual_time_window_rejects_late_or_cross_day(hour,minute,day):
    now=datetime.strptime(day,'%Y%m%d').replace(hour=hour,minute=minute,tzinfo=m.TZ)
    with pytest.raises(ValueError,match='WINDOW'):m.build_plan(Fake(),HEAD,DAY,MARKET,now)


def test_cas_rechecks_clock_after_objects_created():
    f=Fake();d=f.history[2];del f.trees[m.REPO][f'data/market/raw/2026/{d}/daily.csv']
    p=plan(f);ticks=iter([NOW,datetime(2026,11,7,0,tzinfo=m.TZ)])
    with pytest.raises(ValueError,match='WINDOW'):m.commit_plan(f,p,clock=lambda:next(ticks))
    assert not any(path=='/git/refs/heads/main' for path,_ in f.writes)


def test_main_race_rejects_before_write():
    f=Fake();d=f.history[2];del f.trees[m.REPO][f'data/market/raw/2026/{d}/daily.csv']
    p=plan(f);f.head='9'*40
    with pytest.raises(ValueError,match='MAIN_MOVED'):m.commit_plan(f,p,clock=lambda:NOW)
    assert not f.writes


@pytest.mark.parametrize('partial',['outputs/decision/primary_d_receipt_20261106.json',
    'work/profit_1000_upgrade/candidate_natural_forward/workflow_20261106.json'])
def test_any_existing_current_generation_blocks_history_mutation(partial):
    f=Fake();f.put(partial,b'{}')
    with pytest.raises(ValueError,match='CURRENT_D_ALREADY_STARTED'):plan(f)
    assert not f.writes


def test_holey_calendar_and_future_market_commit_rejected():
    f=Fake();f.put('data/market/trade_cal_sse.csv',calendar().replace(b'20261020,1,20261019',b'20261020,1,20261016'))
    with pytest.raises(ValueError,match='CALENDAR_CHAIN'):plan(f)


def test_workflow_has_only_manual_entry_shared_lock_no_push():
    import yaml
    from scripts.dc20_writer_queue import WRITERS, TARGETS
    p=Path(__file__).resolve().parents[1]/'.github/workflows/prepare_primary_history.yml'
    w=yaml.load(p.read_text(),Loader=yaml.BaseLoader)
    assert set(w['on'])=={'workflow_dispatch'}
    assert w['concurrency']=={'group':'decision-auction-main-writer','cancel-in-progress':'false'}
    assert 'git push' not in p.read_text() and 'schedule:' not in p.read_text()
    assert p.name in WRITERS and p.name in TARGETS


def test_upstream_commit_after_original_dispatch_rejected():
    f=Fake()
    with pytest.raises(ValueError,match='UPSTREAM_COMMIT_FROM_FUTURE'):
        m.build_plan(f,HEAD,DAY,MARKET,NOW,source_cutoff=datetime(2026,11,6,10,tzinfo=timezone.utc))
    assert not f.writes


def test_unsafe_blob_and_truncated_inventory_rejected():
    f=Fake();d=f.history[2];path=f'data/market/raw/2026/{d}/daily.csv'
    f.trees[m.REPO][path]['mode']='120000'
    with pytest.raises(ValueError,match='UNSAFE_LOCAL_HISTORY'):m.inspect_history(f,HEAD,DAY)
    with pytest.raises(ValueError,match='REGULAR_BOUNDED_BLOB'):plan(f)
    class Broken(Fake):
        def request(self,path,*args,**kwargs):
            out=super().request(path,*args,**kwargs)
            if path.startswith('/git/trees/'):out['truncated']=True
            return out
    with pytest.raises(ValueError,match='COMPLETE_TREE_REQUIRED'):plan(Broken())


def test_main_race_after_preparation_objects_never_updates_ref():
    class Raced(Fake):
        def request(self,path,body=None,**kwargs):
            result=super().request(path,body,**kwargs)
            if path=='/git/commits' and body is not None:self.head='9'*40
            return result
    f=Raced();d=f.history[2];del f.trees[m.REPO][f'data/market/raw/2026/{d}/daily.csv']
    with pytest.raises(ValueError,match='MAIN_MOVED'):m.commit_plan(f,plan(f),clock=lambda:NOW)
    assert not any(path=='/git/refs/heads/main' for path,_ in f.writes)
