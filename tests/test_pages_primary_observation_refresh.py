"""Targeted deployment regression: do not mix new market files with old views."""
import json
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

def refresh_step():
    workflow = yaml.safe_load((ROOT / '.github/workflows/deploy_dc20_pages.yml').read_text())
    steps = workflow['jobs']['deploy']['steps']
    found = next(i for i,s in enumerate(steps) if s.get('name') == 'Refresh primary observation projection from exact deployment sources')
    assert next(i for i,s in enumerate(steps) if s.get('name') == 'Validate frozen Decision trust root before projection') < found
    assert found < next(i for i,s in enumerate(steps) if s.get('name') == 'Validate optional legacy-profit relative research chain')
    script = steps[found]['run']
    return script.split("python - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]

def test_refresh_keeps_asof_and_validates_before_pages(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / 'outputs/decision/primary_observation/summary.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'as_of_date': '20260924'}))
    calls=[]
    monkeypatch.setattr(subprocess, 'run', lambda command, **kwargs: calls.append((command,kwargs)))
    exec(compile(refresh_step(), '<pages-refresh>', 'exec'), {})
    assert len(calls)==2
    assert calls[0][0][1:] == ['scripts/settle_primary_observations.py','--root','.','--as-of-date','20260924']
    assert calls[1][0] == calls[0][0]+['--validate-existing']
    assert all(kwargs == {'check':True} for _,kwargs in calls)
    assert json.loads(path.read_text())['as_of_date']=='20260924'

def test_refresh_failure_stops_build(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path=tmp_path/'outputs/decision/primary_observation/summary.json'
    path.parent.mkdir(parents=True); path.write_text('{"as_of_date":"20260924"}')
    def fail(*args,**kwargs): raise subprocess.CalledProcessError(1,args[0])
    monkeypatch.setattr(subprocess,'run',fail)
    with pytest.raises(subprocess.CalledProcessError):
        exec(compile(refresh_step(),'<pages-refresh>','exec'),{})

def test_no_observation_does_not_invent_one(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    def forbidden(*a,**k): raise AssertionError('must not fabricate an as-of')
    monkeypatch.setattr(subprocess,'run',forbidden)
    exec(compile(refresh_step(),'<pages-refresh>','exec'),{})
    assert not (tmp_path/'outputs').exists()

def test_review_changes_only_deployment_pin_and_inventory():
    review=json.loads((ROOT/'models/decision_source_surface_review_20260925_statistics.json').read_text())
    assert {x['path'] for x in review['source_changes']} == {'.github/workflows/deploy_dc20_pages.yml','models/decision_model_freeze.json','forward/model_inventory.json'}
    entry=next(x for x in review['source_changes'] if x['path']=='models/decision_model_freeze.json')
    assert len(entry['inverse_changes'])==1
    assert all('deploy_dc20_pages.yml' in line for key in ['baseline_lines','current_lines'] for line in entry['inverse_changes'][0][key])
