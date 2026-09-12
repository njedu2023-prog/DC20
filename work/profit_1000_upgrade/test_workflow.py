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
