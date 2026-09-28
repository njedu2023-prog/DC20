"""Nightly writer-bound close statistics: freshness without blocking rankings."""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest
import yaml

ROOT=Path(__file__).resolve().parents[1]

def namespace():
    workflow=yaml.safe_load((ROOT/'.github/workflows/deploy_dc20_pages.yml').read_text())
    steps=workflow['jobs']['deploy']['steps']
    i=next(i for i,s in enumerate(steps) if s.get('name')=='Refresh primary observation projection from exact deployment sources')
    assert next(i for i,s in enumerate(steps) if s.get('name')=='Validate frozen Decision trust root before projection') < i
    assert i < next(i for i,s in enumerate(steps) if s.get('name')=='Build independent compact statistics window')
    script=steps[i]['run'].split("python - <<'PY'\n",1)[1].rsplit('\nPY',1)[0]
    ns={'__name__':'test_refresh'}
    exec(compile(script,'<nightly-refresh>','exec'),ns)
    return ns

def fixture(root):
    out=root/'outputs/decision';out.mkdir(parents=True)
    (out/'primary_observation').mkdir()
    (out/'primary_observation/summary.json').write_text('{"as_of_date":"20260924"}')
    (out/'primary_observation/rows.csv').write_text('original rows')
    index={'latest_signal_date':'20260928','latest_receipt_url':'receipt', 'latest_runtime_features_url':'runtime',
           'latest_three_rank_json_url':'p0','latest_three_rank_csv_url':'csv'}
    (out/'primary_d_runtime_index.json').write_text(json.dumps(index))
    return index

def target_namespace(root):
    index=fixture(root);ns=namespace()
    ns.update(validate_primary_d_runtime_index=lambda i:None,build_primary_d_runtime_index=lambda *a,**k:index,
              _strict_open_dates=lambda r:['20260924','20260928'],_find_market_file=lambda *a:root/'daily',_market_rows=lambda *a:{'stock':{}})
    return ns

def test_advance_from_verified_nightly_d_not_old_summary(tmp_path):
    ns=target_namespace(tmp_path)
    assert ns['refresh_target'](tmp_path,datetime(2026,9,28,20,tzinfo=ZoneInfo('Asia/Shanghai')))=='20260928'

@pytest.mark.parametrize('failure',['before_close','missing_data','binding','calendar'])
def test_target_rejects_unverified_or_future_close(tmp_path,failure):
    ns=target_namespace(tmp_path);now=datetime(2026,9,28,20,tzinfo=ZoneInfo('Asia/Shanghai'))
    if failure=='before_close':now=now.replace(hour=14)
    if failure=='missing_data':ns['_market_rows']=lambda *a:{}
    if failure=='binding':ns['build_primary_d_runtime_index']=lambda *a,**k:{}
    if failure=='calendar':ns['_strict_open_dates']=lambda r:['20260924']
    with pytest.raises(ValueError):ns['refresh_target'](tmp_path,now)

def test_no_source_does_not_invent_today(tmp_path):
    ns=namespace()
    assert ns['refresh_target'](tmp_path,datetime.now(ZoneInfo('Asia/Shanghai'))) is None

def test_does_not_regress_previous_asof(tmp_path):
    ns=target_namespace(tmp_path)
    p=tmp_path/'outputs/decision/primary_d_runtime_index.json';i=json.loads(p.read_text());i['latest_signal_date']='20260923';p.write_text(json.dumps(i))
    ns['build_primary_d_runtime_index']=lambda *a,**k:i
    assert ns['refresh_target'](tmp_path,datetime(2026,9,28,20,tzinfo=ZoneInfo('Asia/Shanghai')))=='20260924'

@pytest.mark.parametrize('recover',[True,False])
def test_bounded_retry_restore_and_nonblocking_failure(tmp_path,monkeypatch,recover):
    fixture(tmp_path);ns=namespace();ns['refresh_target']=lambda *a:'20260928'
    monkeypatch.setattr(ns['time'],'sleep',lambda n:None)
    calls=[];p=tmp_path/'outputs/decision/primary_observation/summary.json';before=p.read_bytes()
    def rebuild(*args):
        calls.append(args)
        if len(calls)==2:assert p.read_bytes()==before
        if recover and len(calls)==2:return
        p.write_text('partial write');raise RuntimeError('source failure')
    ns['rebuild_and_validate']=rebuild
    result=ns['refresh_statistics'](tmp_path)
    assert len(calls)==2
    assert result['status']==('READY' if recover else 'STALE_OR_UNAVAILABLE')
    if not recover:assert p.read_bytes()==before

def test_validation_and_timeout_are_required(tmp_path,monkeypatch):
    ns=namespace();calls=[]
    monkeypatch.setattr(ns['subprocess'],'run',lambda cmd,**kw:calls.append((cmd,kw)))
    ns['rebuild_and_validate'](tmp_path,'20260928')
    assert len(calls)==2 and calls[1][0]==calls[0][0]+['--validate-existing']
    assert all(kw=={'check':True,'timeout':180} for _,kw in calls)

def test_ui_explicitly_marks_shared_stale_cutoff():
    html=(ROOT/'decision.html').read_text()
    assert html.count('统计更新滞后：尚未覆盖最新D日收盘')==2
