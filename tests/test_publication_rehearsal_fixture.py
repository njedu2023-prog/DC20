"""Historical publication uses fixed data and each checkout's own code."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

from scripts import rehearse_daily_publication as rehearsal


DAY = '20261106'
BASELINE = '943e62b27102c671cd440c45a160054a3bb092f9'


def write(root, relative, content):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def inventory(root):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in root.rglob('*') if p.is_file() and not p.is_symlink()}


def checkout(root, day, code):
    root.mkdir()
    write(root, 'data/market/raw/' + day + '/daily.csv', 'symbol,close\n000001,10\n')
    write(root, 'outputs/decision/primary_d_runtime_index.json',
          json.dumps({'latest_signal_date': day}))
    write(root, 'outputs/decision/day_' + day + '.json', '{"ready":true}\n')
    write(root, 'outputs/decision/forward_preview/app.js', '/* generated stable asset */\n')
    for directory in rehearsal.BUSINESS_DIRECTORIES[2:]:
        write(root, directory + '/' + day + '/context.json', '{"frozen":true}\n')
    write(root, 'scripts/build.py', code)
    write(root, '.github/workflows/deploy_dc20_pages.yml', code)
    write(root, 'models/decision_model_freeze.json', code)
    write(root, 'work/profit_1000_upgrade/candidate_registration_v1.json', code)
    return root


@pytest.fixture
def roots(tmp_path):
    tmp_path = tmp_path.resolve()
    return (checkout(tmp_path/'code', '20261109', 'current code\n'),
            checkout(tmp_path/'baseline', DAY, 'original code\n'))


def test_same_historical_data_keeps_each_code_version_and_fixture_unchanged(roots, tmp_path):
    current, source = roots
    original_code = tmp_path.resolve()/'original-code'
    shutil.copytree(source, original_code)
    before_source = inventory(source)
    preserved = {p: raw for p, raw in inventory(current).items()
                 if not p.startswith(tuple(d+'/' for d in rehearsal.BUSINESS_DIRECTORIES))}
    for root in (current, original_code):
        report = rehearsal.stage_business_inputs(root, source, DAY)
        assert report['signal_date'] == DAY
        assert report['copied_files'] > 0
        assert not any('20261109' in p for p in inventory(root))
    for directory in rehearsal.BUSINESS_DIRECTORIES:
        assert inventory(current/directory) == inventory(original_code/directory)
    assert all((current/p).read_bytes() == raw for p, raw in preserved.items())
    assert (current/'scripts/build.py').read_bytes() != (original_code/'scripts/build.py').read_bytes()
    assert inventory(source) == before_source


def test_new_required_business_file_does_not_leak_from_current_checkout(roots):
    current, source = roots
    write(current, 'data/new_required.csv', 'must not be used\n')
    rehearsal.stage_business_inputs(current, source, DAY)
    with pytest.raises(FileNotFoundError):
        (current/'data/new_required.csv').read_bytes()


def test_optional_absent_fixture_tree_removes_current_tree(roots):
    current, source = roots
    directory = rehearsal.BUSINESS_DIRECTORIES[-1]
    shutil.rmtree(source/directory)
    rehearsal.stage_business_inputs(current, source, DAY)
    assert not (current/directory).exists()


@pytest.mark.parametrize('error', ['wrong-day', 'missing-data', 'missing-outputs', 'bad-date'])
def test_invalid_fixture_rejected_before_mutation(roots, error):
    current, source = roots
    before = inventory(current)
    day = DAY
    if error == 'wrong-day':
        write(source, 'outputs/decision/primary_d_runtime_index.json',
              '{"latest_signal_date":"20261109"}')
    elif error.startswith('missing-'):
        shutil.rmtree(source/error.removeprefix('missing-'))
    else:
        day = '2026-11-06'
    with pytest.raises((ValueError, FileNotFoundError)):
        rehearsal.stage_business_inputs(current, source, day)
    assert inventory(current) == before


@pytest.mark.parametrize('side', ['source', 'current'])
@pytest.mark.parametrize('kind', ['nested', 'ancestor'])
def test_symlinked_business_paths_are_rejected_before_mutation(roots, tmp_path, side, kind):
    current, source = roots
    target = source if side == 'source' else current
    outside = tmp_path.resolve()/'outside'
    outside.mkdir()
    write(outside, 'unrelated.txt', 'do not touch')
    if kind == 'nested':
        (target/'data/link').symlink_to(outside, target_is_directory=True)
    else:
        shutil.rmtree(target/'work')
        (target/'work').symlink_to(outside, target_is_directory=True)
    before = inventory(current)
    with pytest.raises(ValueError, match='symlinks'):
        rehearsal.stage_business_inputs(current, source, DAY)
    assert inventory(current) == before
    assert (outside/'unrelated.txt').read_text() == 'do not touch'


def test_overlapping_and_aliased_roots_are_rejected(roots, tmp_path):
    current, source = roots
    with pytest.raises(ValueError, match='disjoint'):
        rehearsal.stage_business_inputs(source, source, DAY)
    nested = source/'nested'
    nested.mkdir()
    with pytest.raises(ValueError, match='disjoint'):
        rehearsal.stage_business_inputs(nested, source, DAY)
    alias = tmp_path.resolve()/'alias'
    alias.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError, match='Aliased'):
        rehearsal.stage_business_inputs(current, alias, DAY)


@pytest.mark.parametrize('kind', ['changed-js', 'new-python', 'executable', 'changed-mode'])
def test_fixture_cannot_replace_code_in_data_surface(roots, kind):
    current, source = roots
    if kind == 'changed-js':
        write(current, 'outputs/decision/forward_preview/app.js', 'new implementation')
    elif kind == 'new-python':
        write(current, 'data/helper.py', 'new implementation')
    else:
        path = write(source, 'data/executable', 'old implementation')
        path.chmod(0o755)
        write(current, 'data/executable', 'old implementation' if kind == 'changed-mode' else 'new implementation')
    before = inventory(current)
    with pytest.raises(ValueError, match='must not replace code'):
        rehearsal.stage_business_inputs(current, source, DAY)
    assert inventory(current) == before


def test_source_sha_and_committed_data_are_verified_with_real_git(roots):
    current, source = roots
    def git(*args):
        return subprocess.check_output(['git', '-C', str(source), *args],
                                       text=True, stderr=subprocess.PIPE).strip()
    git('init', '-q')
    git('add', '.')
    git('-c', 'user.name=Fixture Test', '-c', 'user.email=fixture@example.invalid',
        'commit', '-qm', 'immutable fixture')
    source_head = git('rev-parse', 'HEAD')
    before = inventory(current)
    with pytest.raises(ValueError, match='immutable commit'):
        rehearsal.stage_business_inputs(current, source, DAY, 'f'*40)
    assert inventory(current) == before
    write(source, 'data/extra.csv', 'uncommitted')
    with pytest.raises(ValueError, match='committed bytes'):
        rehearsal.stage_business_inputs(current, source, DAY, source_head)
    assert inventory(current) == before
    (source/'data/extra.csv').unlink()
    write(source, '.gitignore', 'data/ignored.csv\n')
    git('add', '.gitignore')
    git('-c', 'user.name=Fixture Test', '-c', 'user.email=fixture@example.invalid',
        'commit', '-qm', 'ignore cache')
    source_head = git('rev-parse', 'HEAD')
    write(source, 'data/ignored.csv', 'not committed')
    with pytest.raises(ValueError, match='committed bytes'):
        rehearsal.stage_business_inputs(current, source, DAY, source_head)
    assert inventory(current) == before
    (source/'data/ignored.csv').unlink()
    report = rehearsal.stage_business_inputs(current, source, DAY, source_head)
    assert report['source_head'] == source_head


def test_output_cannot_modify_source_fixture(roots):
    current, source = roots
    before = inventory(source)
    with pytest.raises(ValueError, match='outside the fixed fixture'):
        rehearsal.run(current, source/'new-result', source_root=source,
                      expected_signal_date=DAY)
    assert inventory(source) == before


def test_run_uses_staged_pointer_before_build_and_retains_guards(roots, tmp_path, monkeypatch):
    current, source = roots
    pointer = {'latest_signal_date': DAY}
    for key in ('latest_receipt_url', 'latest_runtime_features_url',
                'latest_three_rank_json_url', 'latest_three_rank_csv_url'):
        relative = 'outputs/decision/'+key+'.json'
        pointer[key] = relative
        write(source, relative, '{"frozen":true}')
    write(source, 'outputs/decision/primary_d_runtime_index.json', json.dumps(pointer))
    build_script = """mkdir -p _site
printf '%s' '{"signal_date":"20261106","profit_paper_account_url":"account.json"}' > _site/revision.json
printf '%s' '{"trades":[],"missing_returns":null}' > _site/account.json
"""
    write(current, '.github/workflows/deploy_dc20_pages.yml', yaml.safe_dump(
        {'jobs': {'deploy': {'steps': [{'name': 'fixture build', 'run': build_script}]}}}))
    monkeypatch.setattr(rehearsal, 'STEPS', ('fixture build',))
    result = rehearsal.run(current, tmp_path.resolve()/'result', source_root=source,
                           expected_signal_date=DAY)
    assert result['status'] == 'PASS'
    assert result['signal_date'] == DAY
    assert result['source_fixture']['signal_date'] == DAY
    assert result['protected_files_unchanged'] >= 4
    assert result['natural_freeze_created'] is False
    assert result['public_deploy_performed'] is False


def test_workflow_uses_one_immutable_source_for_both_code_versions():
    workflow = yaml.safe_load((Path(__file__).resolve().parents[1]/
                              '.github/workflows/verify_daily_cleanup.yml').read_text())
    steps = workflow['jobs']['historical-linked-replay']['steps']
    baseline = next(s for s in steps if s['name'] == 'Checkout original baseline')
    assert baseline['with']['ref'] == BASELINE
    immediate = next(s for s in steps if s['name'] == 'Checkout immediate reliability baseline for matched latest-D replay')
    immediate_sha = '65d4e0c1d80c177645eff1d1bbd53fee71cffda9'
    assert immediate['with']['ref'] == immediate_sha
    builds = next(s['run'] for s in steps if s['name'].startswith('Rehearse three'))
    assert 'cp -a "$version" "$rehearsal"' in builds
    assert '--source-root reliability-baseline --expected-signal-date 20261110' in builds
    assert '--expected-source-head '+immediate_sha in builds
    assert BASELINE not in builds and '--source-root baseline ' not in builds
    assert steps.index(immediate) < next(i for i,s in enumerate(steps) if s['name'].startswith('Rehearse three'))
    historical = next(s['run'] for s in steps if s['name'].startswith('Compare historical rankings'))
    assert BASELINE in historical and 'historical-ranking-comparison.json' in historical
    assert 'business_signature_sha256' in historical and 'profit_top2' in historical
    comparison = next(s['run'] for s in steps if s['name'].startswith('Compare business'))
    for guard in ('source_fixture', 'business_input_inventory_sha256',
                  'Simulated account business values changed', 'protected_files_unchanged',
                  'natural_freeze_created', 'public_deploy_performed'):
        assert guard in comparison
    assert 'publication-comparison.json' in comparison and immediate_sha in comparison
    # The realized ledger can legitimately exit on DAY without changing the replay signal date.
    assert BASELINE not in comparison and DAY not in comparison.replace("top1['last_exit_date']=='20261106'", '')
    latest = next(s['run'] for s in steps if s['name'] == 'Recompute latest original freeze with immediate old and new code')
    assert '--source-root reliability-baseline --signal-date 20261110' in latest
    assert 'for trial in 1 2 3' in latest and 'reliability-comparison.json' in latest


@pytest.mark.parametrize('fault', [None, 'wrong_day', 'wrong_fixture', 'different_inputs',
    'different_account', 'incorrect_top1_total', 'incorrect_top1_best', 'incorrect_top1_worst',
    'incorrect_old_nav', 'no_protection', 'new_freeze', 'actual_deployment'])
def test_publication_comparison_rejects_incompatible_fixture_or_changed_business_values(tmp_path, monkeypatch, fault):
    workflow = yaml.safe_load((Path(__file__).resolve().parents[1]/
                              '.github/workflows/verify_daily_cleanup.yml').read_text())
    step = next(s for s in workflow['jobs']['historical-linked-replay']['steps']
                if s['name'] == 'Compare business results and measured durations')
    code = step['run'].split("python - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    for version in ('reliability-baseline', 'cleaned'):
        for trial in (1, 2, 3):
            item = {'status': 'PASS', 'signal_date': '20261110',
                'source_fixture': {'signal_date': '20261110',
                    'source_head': '65d4e0c1d80c177645eff1d1bbd53fee71cffda9'},
                'business_input_inventory_sha256': 'a'*64,
                'natural_freeze_created': False, 'public_deploy_performed': False,
                'protected_files_unchanged': 20,
                'total_seconds': 10.0 + trial}
            if version == 'reliability-baseline':
                item['account'] = {'schema_version': 'dc20_paper_capital_account_v1',
                    'start_signal_date': '20261019', 'last_valid_date': '20261109',
                    'initial_capital': 735000, 'per_trade_budget': 100000,
                    'metrics': {'nav': 707771.09 if fault != 'incorrect_old_nav' else 0}}
            else:
                item['account'] = {'schema_version': 'dc20_top1_realized_paper_account_v1',
                    'start_signal_date': '20260914', 'last_exit_date': '20261106',
                    'per_trade_budget': 100000, 'closed_trades': 27,
                    'metrics': {'cumulative_pnl': 117878.58,'max_profit': 32482.59,'max_loss': -14685.93}}
                wrong_metric = {'incorrect_top1_total':'cumulative_pnl', 'incorrect_top1_best':'max_profit',
                    'incorrect_top1_worst':'max_loss'}.get(fault)
                if wrong_metric:
                    item['account']['metrics'][wrong_metric] = 0
            if version == 'cleaned' and trial == 3:
                if fault == 'wrong_day': item['signal_date'] = DAY
                if fault == 'wrong_fixture': item['source_fixture']['source_head'] = BASELINE
                if fault == 'different_inputs': item['business_input_inventory_sha256'] = 'b'*64
                if fault == 'different_account': item['account']['closed_trades'] += 1
                if fault == 'no_protection': item['protected_files_unchanged'] = 0
                if fault == 'new_freeze': item['natural_freeze_created'] = True
                if fault == 'actual_deployment': item['public_deploy_performed'] = True
            target = tmp_path/'acceptance'/f'{version}-publication-{trial}'/'result.json'
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(item))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(tmp_path/'summary.md'))
    monkeypatch.setenv('GITHUB_SHA', 'c'*40)
    if fault:
        with pytest.raises(AssertionError):
            exec(compile(code, 'publication-comparison-workflow', 'exec'), {})
        assert not (tmp_path/'acceptance/publication-comparison.json').exists()
    else:
        exec(compile(code, 'publication-comparison-workflow', 'exec'), {})
        result = json.loads((tmp_path/'acceptance/publication-comparison.json').read_text())
        assert result['signal_date'] == '20261110' and result['repaired_commit'] == 'c'*40
        assert result['baseline_build_seconds'] == result['cleaned_build_seconds'] == 12
        assert result['build_seconds_saved'] == 0
