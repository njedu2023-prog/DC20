"""Static workflow boundary and real local pin checks, without API or secrets."""
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from work.profit_1000_upgrade import candidate_natural_settlement_workflow as m


def workflow():
    return yaml.load((m.ROOT / m.WORKFLOW_PATH).read_text(), Loader=yaml.BaseLoader)


def test_exact_trigger_scope_and_safe_manual_default():
    doc = workflow()
    assert doc['name'] == m.WORKFLOW_NAME
    assert set(doc['on']) == {'schedule', 'workflow_run', 'workflow_dispatch', 'push'}
    assert doc['on']['schedule'] == [{'cron':'50 11 * * 1-5'}, {'cron':'50 12 * * 1-5'}]
    assert {slot['cron'] for slot in doc['on']['schedule']} == set(m.SCHEDULES)
    assert m.MAX_SCHEDULE_DELAY.total_seconds() == 12 * 60 * 60
    assert doc['on']['workflow_run'] == {
        'workflows':['DC20 · Preserve natural candidate publication (research)'],
        'branches':['main'], 'types':['completed']}
    assert doc['on']['workflow_dispatch']['inputs'] == {'dry_run':{
        'description':'只读验证；不请求行情、不写研究账本',
        'required':'true','default':'true','type':'boolean'}}
    assert doc['on']['push']['branches'] == ['main']
    assert all(path.startswith(('work/profit_1000_upgrade/', '.github/workflows/research_candidate_natural_settlement.yml'))
        for path in doc['on']['push']['paths'])


def test_writer_is_never_reachable_on_push_and_validation_is_unprivileged():
    doc = workflow()
    assert doc['permissions'] == {'contents':'read'}
    assert set(doc['jobs']) == {'validate','settle'}
    validate, settle = doc['jobs']['validate'], doc['jobs']['settle']
    assert 'permissions' not in validate
    assert settle['permissions'] == {'actions':'read','contents':'write'}
    assert settle['needs'] == 'validate'
    assert settle['if'] == "github.event_name == 'schedule' || github.event_name == 'workflow_run' || github.event_name == 'workflow_dispatch'"
    assert 'github.run_attempt == 1' in validate['if']
    assert 'github.ref == \'refs/heads/main\'' in validate['if']
    for field in ['workflow_id == 357027830','run_attempt == 1',
                  "conclusion == 'success'", "status == 'completed'",
                  "head_branch == 'main'", "event == 'workflow_run'", "event == 'workflow_dispatch'",
                  'repository.full_name == github.repository', 'head_repository.full_name == github.repository',
                  "path == '.github/workflows/research_candidate_natural_observer.yml'"]:
        assert 'github.event.workflow_run.' + field in validate['if']
    assert doc['concurrency']['cancel-in-progress'] == 'false'
    assert "github.event_name == 'push' && github.run_id" in doc['concurrency']['group']


def test_actions_and_installs_keep_reviewed_versions():
    doc = workflow()
    expected = {
        'actions/checkout':'fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09',
        'actions/setup-python':'ece7cb06caefa5fff74198d8649806c4678c61a1',
        'actions/upload-artifact':'ea165f8d65b6e75b540449e92b4886f43607fa02',
    }
    for job in doc['jobs'].values():
        assert job['runs-on'] == 'ubuntu-24.04'
        for step in job['steps']:
            if 'uses' in step:
                action, commit = step['uses'].split('@')
                assert expected[action] == commit
                if action == 'actions/checkout':
                    assert step['with']['persist-credentials'] == 'false'
                if action == 'actions/setup-python':
                    assert step['with']['python-version'] == '3.12.13'
            if 'pip install' in step.get('run',''):
                assert '--require-hashes -r requirements-dev.lock' in step['run']
                assert '--only-binary=:all:' in step['run']
    assert doc['jobs']['validate']['steps'][0]['with']['ref'] == '${{ github.sha }}'
    assert doc['jobs']['settle']['steps'][0]['with']['ref'] == 'main'


def test_secrets_only_exist_in_explicit_settlement_step():
    doc = workflow()
    assert 'secrets.' not in str(doc['jobs']['validate'])
    steps = doc['jobs']['settle']['steps']
    secret_steps = [s for s in steps if 'secrets.' in str(s)]
    assert len(secret_steps) == 1
    step = secret_steps[0]
    assert step['timeout-minutes'] == '45'
    assert doc['jobs']['settle']['timeout-minutes'] == '55'
    assert step['env'] == {
        'DC20_CANDIDATE_GITHUB_TOKEN':'${{ github.token }}',
        'TUSHARE_TOKEN':'${{ secrets.TUSHARE_TOKEN }}',
        'EXECUTE_RESEARCH':"${{ github.event_name != 'workflow_dispatch' || inputs.dry_run == false }}"}
    assert 'candidate_natural_settlement_workflow' in step['run']
    assert '--execute' in step['run'] and '$EXECUTE_RESEARCH' in step['run']
    assert not re.search(r'\b(?:curl|wget|ssh)\b|git\s+push|TUSHARE_TOKEN', step['run'])
    assert all('${{' not in s.get('run','') for job in doc['jobs'].values() for s in job['steps'])


def test_failure_state_retained_without_touching_checkout():
    steps = workflow()['jobs']['settle']['steps']
    assert steps[-2]['if'] == 'always()'
    assert 'git status --porcelain=v1' in steps[-2]['run']
    assert steps[-1]['if'] == 'always()'
    assert steps[-1]['with']['retention-days'] == '90'
    assert steps[-1]['with']['include-hidden-files'] == 'false'
    assert steps[-1]['with']['path'].splitlines() == [
        '${{ runner.temp }}/dc20-candidate-settlement-*', '/tmp/dc20-candidate-natural-state/']


@pytest.mark.parametrize('job_name',['validate','settle'])
def test_embedded_shell_syntax_without_execution(job_name):
    for step in workflow()['jobs'][job_name]['steps']:
        if 'run' in step:
            result = subprocess.run(['bash','-n'], input=step['run'], text=True,
                capture_output=True, timeout=10, check=False)
            assert result.returncode == 0, result.stderr


def test_real_source_guards_include_new_workflow_and_dependencies():
    local, state = m.code_guard(m.dependencies())
    assert local[m.WORKFLOW_PATH] == (m.ROOT / m.WORKFLOW_PATH).read_bytes()
    assert state
    for name, digest in m.PINS.items():
        relative = 'work/profit_1000_upgrade/' + name + '.py'
        assert m.sha(local[relative]) == digest
