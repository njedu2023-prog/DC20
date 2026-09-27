"""Scoped regressions for holiday no-op and background writer isolation."""
import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

def workflow(name):
    return yaml.safe_load((ROOT / '.github/workflows' / name).read_text())

@pytest.mark.parametrize('event,flag,expected', [
    ('schedule','0',0), ('schedule','1',None),
    ('workflow_dispatch','0',1), ('schedule',None,1),
    ('schedule','bad',1), ('workflow_run','0',1),
])
def test_closed_day_only_skips_known_scheduled_holiday(tmp_path, monkeypatch,event,flag,expected):
    step=next(s for s in workflow('run_primary_d_daily.yml')['jobs']['compute']['steps'] if s.get('id')=='target')
    source=step['run']
    source=source[source.index('if signal_date not in opened:'):source.index("if event in {'schedule', 'workflow_run'}:")]
    target=tmp_path/'out'
    monkeypatch.setenv('GITHUB_OUTPUT',str(target))
    cal=pd.DataFrame([] if flag is None else [{'cal_date':'20260925','is_open':flag}],columns=['cal_date','is_open'])
    env=dict(signal_date='20260925',opened={'20260925'} if flag=='1' else set(),cal=cal,event=event,Path=Path,os=os)
    if expected is None:
        exec(source,env)
        assert not target.exists()
    else:
        with pytest.raises(SystemExit) as exc: exec(source,env)
        assert (exc.value.code==0)==(expected==0)
        if expected==0:
            assert target.read_text()=='non_trading_day=true\nsignal_date=20260925\n'
        else: assert not target.exists()

def test_holiday_cannot_publish_or_resolve_sources():
    compute=workflow('run_primary_d_daily.yml')['jobs']['compute']
    assert "non_trading_day == 'true' && 'false'" in compute['outputs']['publish']
    steps=compute['steps']; i=next(i for i,s in enumerate(steps) if s.get('id')=='target')
    for s in steps[i+1:]:
        assert 'non_trading_day' in s.get('if','') or "steps.candidate.outputs.has_changes == 'true'" in s.get('if','')
    assert 'already_complete=true' not in steps[i]['run']

def test_backfill_isolated_compute_retains_cas_and_short_shared_writer():
    w=workflow('backfill_decision_v11_history.yml')
    assert w['concurrency']['group']=='dc20-backfill-compute'
    assert w['jobs']['publish']['concurrency']=={'group':'decision-auction-main-writer','cancel-in-progress':False}
    assert w['jobs']['publish']['timeout-minutes']==15
    text='\n'.join(s.get('run','') for s in w['jobs']['publish']['steps'])
    assert 'test "$(git rev-parse HEAD)" = "${expected}"' in text

def test_backfill_replay_binds_all_dates_and_semantic_snapshot():
    w=workflow('backfill_decision_v11_history.yml')
    step=next(s for s in w['jobs']['compute']['steps'] if s.get('name')=='Validate exact Backfill candidate in an isolated frozen runtime')
    text=step['run']
    assert '_persisted_action_snapshot_binding' in text
    for opt in ('--signal-date','--report-date','--expected-action-semantic-sha256'):
        assert opt in text
    assert 'exit "${replay_status}"' in text

def test_source_pins_match_workflows():
    m=json.loads((ROOT/'models/decision_model_freeze.json').read_bytes())
    for p in ('.github/workflows/run_primary_d_daily.yml','.github/workflows/backfill_decision_v11_history.yml'):
        assert m['pinned_files'][p]==hashlib.sha256((ROOT/p).read_bytes()).hexdigest()
