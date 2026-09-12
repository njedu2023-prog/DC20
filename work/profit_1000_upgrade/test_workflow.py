"""Parse the actual research workflow before dispatching any data requests."""
from pathlib import Path
import re

import yaml


def test_research_workflow_is_valid_and_read_only():
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.load(
        (root / '.github/workflows/research_profit_1000_upgrade.yml').read_text(),
        Loader=yaml.BaseLoader,
    )
    assert workflow['on'] == {'push': {'branches': ['main'], 'paths': [
        'work/profit_1000_upgrade/COLLECTION.json']}}
    assert workflow['permissions'] == {'contents': 'read', 'actions': 'read'}
    job = workflow['jobs']['research']
    assert 'github.run_attempt == 1' in job['if']
    steps = job['steps']
    for step in steps:
        assert isinstance(step.get('run', ''), str)
        if 'uses' in step:
            assert re.fullmatch(r'.+@[0-9a-f]{40}', step['uses'])
    install = next(s for s in steps if s['name'].startswith('Install'))
    assert '--only-binary=:all: --require-hashes' in install['run']
    tests = next(i for i, s in enumerate(steps) if s['name'].startswith('Test'))
    freeze = next(i for i, s in enumerate(steps) if s['name'].startswith('Verify production'))
    credential_steps = [i for i, s in enumerate(steps)
                        if 'secrets.TUSHARE_TOKEN' in str(s)]
    assert len(credential_steps) == 1
    assert tests < freeze < credential_steps[0]
    assert steps[0]['with']['persist-credentials'] == 'false'
    assert steps[-1]['if'] == 'always()'


def test_suspension_probe_is_once_and_isolated():
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.load(
        (root / '.github/workflows/research_profit_1000_suspension.yml').read_text(),
        Loader=yaml.BaseLoader,
    )
    assert workflow['on'] == {'push': {'branches': ['main'], 'paths': [
        'work/profit_1000_upgrade/SUSPENSION_REQUEST.json']}}
    assert workflow['permissions'] == {'contents': 'read'}
    job = workflow['jobs']['probe']
    assert 'github.run_attempt == 1' in job['if']
    assert job['timeout-minutes'] == '8'
    steps = job['steps']
    tests = next(i for i, s in enumerate(steps) if 'unittest discover' in s.get('run', ''))
    credentials = [i for i, s in enumerate(steps) if 'secrets.TUSHARE_TOKEN' in str(s)]
    assert len(credentials) == 1 and tests < credentials[0]
    assert 'suspension_probe.py' in steps[credentials[0]]['run']
    assert '${RUNNER_TEMP}' in steps[credentials[0]]['run']
    for step in steps:
        if 'uses' in step:
            assert re.fullmatch(r'.+@[0-9a-f]{40}', step['uses'])
    assert steps[-1]['if'] == 'always()'


def test_capital_followup_is_bound_to_reviewed_run_and_cannot_trade():
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.load(
        (root / '.github/workflows/research_profit_1000_capital.yml').read_text(),
        Loader=yaml.BaseLoader,
    )
    assert set(workflow['on']) == {'workflow_run'}
    assert workflow['on']['workflow_run']['types'] == ['completed']
    assert workflow['on']['workflow_run']['branches'] == ['main']
    assert workflow['permissions'] == {'contents': 'read', 'actions': 'read'}
    job = workflow['jobs']['replay']
    assert "workflow_run.id == 34671477608" in job['if']
    assert 'workflow_run.run_attempt == 1' in job['if']
    assert "workflow_run.head_sha == '4675fab984050fa32be875fff07e0285e8903ebb'" in job['if']
    assert "workflow_run.event == 'push'" in job['if']
    assert "head_repository.full_name == 'njedu2023-prog/DC20'" in job['if']
    assert 'github.run_attempt == 1' in job['if']
    assert 'secrets.' not in str(workflow)
    steps = job['steps']
    for step in steps:
        if 'uses' in step:
            assert re.fullmatch(r'.+@[0-9a-f]{40}', step['uses'])
        assert isinstance(step.get('run', ''), str)
    download = next(s for s in steps if 'download-artifact@' in s.get('uses', ''))
    assert download['with']['run-id'] == '34671477608'
    assert download['with']['name'].endswith('4675fab984050fa32be875fff07e0285e8903ebb-34671477608')
    replay = next(s for s in steps if 'capital_report.py' in s.get('run', ''))
    assert '--expected-source-run-id 34671477608' in replay['run']
    assert '--expected-source-commit 4675fab984050fa32be875fff07e0285e8903ebb' in replay['run']
    assert steps[0]['with']['persist-credentials'] == 'false'


def test_price_and_minute_diagnostics_are_fixed_read_only_requests():
    root = Path(__file__).resolve().parents[2]
    for kind, trigger, script in (("price", "PRICE_REQUEST.json", "price_probe.py"),
                                  ("minute_gap", "MINUTE_GAP_REQUEST.json", "minute_gap_probe.py")):
        workflow = yaml.load(
            (root / f'.github/workflows/research_profit_1000_{kind}.yml').read_text(),
            Loader=yaml.BaseLoader,
        )
        assert workflow['on'] == {'push': {'branches': ['main'], 'paths': [
            'work/profit_1000_upgrade/' + trigger]}}
        assert workflow['permissions'] == {'contents': 'read'}
        job = workflow['jobs']['probe']
        assert 'github.run_attempt == 1' in job['if']
        assert 'njedu2023-prog/DC20' in job['if'] and "refs/heads/main" in job['if']
        assert int(job['timeout-minutes']) <= 8
        steps = job['steps']
        tests = next(i for i, s in enumerate(steps) if 'unittest discover' in s.get('run', ''))
        credentials = [i for i, s in enumerate(steps) if 'secrets.' in str(s)]
        assert len(credentials) == 1 and tests < credentials[0]
        assert script in steps[credentials[0]]['run']
        assert '${RUNNER_TEMP}' in steps[credentials[0]]['run']
        assert steps[0]['with']['persist-credentials'] == 'false'
        assert steps[0]['with']['ref'] == '${{ github.sha }}'
        for step in steps:
            if 'uses' in step:
                assert re.fullmatch(r'.+@[0-9a-f]{40}', step['uses'])
            assert isinstance(step.get('run', ''), str)
        assert steps[-1]['if'] == 'always()'
