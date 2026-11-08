"""Retired research cannot block or impersonate current production evidence."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from unittest.mock import patch

import pytest
import yaml

from scripts import daily_delivery_policy as policy

ROOT = Path(__file__).resolve().parents[1]


def write_json(root, path, value):
    path = root / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


@pytest.fixture
def archived(tmp_path):
    write_json(tmp_path, policy.POLICY_PATH, json.loads((ROOT / policy.POLICY_PATH).read_bytes()))
    return tmp_path


def test_explicit_archive_policy_separates_generation_and_publication_dates(archived):
    value = policy.load_policy(archived)
    assert value['archive_through_signal_date'] == '20261106'
    assert value['effective_new_signal_date'] == '20261109'
    assert policy.legacy_archived(archived)
    assert policy.load_policy(archived / 'missing-root') is None
    for change in ({'legacy_profit_mode': 'ACTIVE'}, {'auxiliary_failure_policy': 'IGNORE'},
                   {'effective_new_signal_date': '20261106'}, {'unexpected': True}):
        write_json(archived, policy.POLICY_PATH, dict(value, **change))
        with pytest.raises(ValueError, match='DELIVERY_POLICY_INVALID'):
            policy.load_policy(archived)


def test_archive_preserves_original_paths_and_bytes_with_explicit_missing_state(archived, tmp_path):
    original = {}
    for i, relative in enumerate(policy.ARCHIVE_ROOTS):
        path = archived / relative / ('index.json' if i < 2 else 'selections/old.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'{"frozen": "unchanged"}\n')
        original[path.relative_to(archived).as_posix()] = path.read_bytes()
    for relative in policy.ARCHIVE_CONTRACTS:
        path = write_json(archived, relative, {'contract':'original'})
        original[relative] = path.read_bytes()
    site = tmp_path / 'site'
    result = policy.stage_legacy_archive(archived, site)
    assert result['status'] == 'ARCHIVED' and result['used_for_current_rankings'] is False
    assert result['files'] == [{'path': p, 'sha256': hashlib.sha256(raw).hexdigest()}
                               for p, raw in original.items()]
    assert all((site/p).read_bytes() == raw == (archived/p).read_bytes() for p, raw in original.items())
    (archived / policy.ARCHIVE_ROOTS[0] / 'index.json').unlink()
    (archived / policy.ARCHIVE_ROOTS[0]).rmdir()
    result = policy.stage_legacy_archive(archived, tmp_path/'partial')
    assert result['status'] == 'ARCHIVE_INCOMPLETE'
    assert result['failures'] == [{'path': policy.ARCHIVE_ROOTS[0], 'reason': 'MISSING_ARCHIVE'}]


def test_archive_does_not_follow_root_symlink(archived, tmp_path):
    other = tmp_path/'foreign'; other.mkdir(); (other/'private.json').write_text('secret')
    source = archived / policy.ARCHIVE_ROOTS[0]
    source.parent.mkdir(parents=True, exist_ok=True)
    source.symlink_to(other, target_is_directory=True)
    result = policy.stage_legacy_archive(archived, tmp_path/'site')
    assert result['status'] == 'ARCHIVE_INCOMPLETE' and not result['files']
    assert result['failures'][0]['reason'] == 'UNSAFE_SYMLINK'


@pytest.mark.parametrize('component', ['compact_statistics_window', 'profit_paper_account', 'next_day_statistics'])
def test_auxiliary_failure_removes_stale_bindings_but_not_main_rankings(tmp_path, component):
    primary = {'primary_d_signal_date': '20261106', 'head_sha': 'a'*40,
               'candidate_profit_index_sha256': 'b'*64, 'primary_three_rank_sha256': 'c'*64}
    revision = dict(primary, compact_statistics_window_url='old-window',
                    compact_statistics_window_sha256='d'*64,
                    profit_paper_account_url='old-account', profit_paper_account_sha256='e'*64,
                    next_day_statistics_url='old-next-day', next_day_statistics_sha256='f'*64)
    write_json(tmp_path, 'revision.json', revision)
    write_json(tmp_path, 'outputs/decision/compact_statistics_window.json', {'old': True})
    write_json(tmp_path, 'outputs/decision/profit_paper_account/account.json', {'old': True})
    write_json(tmp_path, 'outputs/decision/next_day_statistics.json', {'old': True})
    policy.mark_auxiliary_unavailable(tmp_path, component, 'broken evidence')
    updated = json.loads((tmp_path/'revision.json').read_bytes())
    assert all(updated[k] == v for k,v in primary.items())
    if component != 'next_day_statistics':
        assert not any(k.startswith('profit_paper_account_') for k in updated)
        assert not (tmp_path/'outputs/decision/profit_paper_account/account.json').exists()
    else:
        assert updated['profit_paper_account_sha256'] == 'e'*64
        assert (tmp_path/'outputs/decision/profit_paper_account/account.json').exists()
    if component != 'profit_paper_account':
        assert not any(k.startswith('next_day_statistics_') for k in updated)
        assert not (tmp_path/'outputs/decision/next_day_statistics.json').exists()
    else:
        assert updated['next_day_statistics_sha256'] == 'f'*64
    assert updated['auxiliary_delivery'][component] == {'status': 'UNAVAILABLE', 'reason': 'broken evidence'}
    if component == 'compact_statistics_window':
        assert not any(k.startswith('compact_statistics_window_') for k in updated)
        assert not (tmp_path/'outputs/decision/compact_statistics_window.json').exists()
    else:
        assert updated['compact_statistics_window_sha256'] == 'd'*64


def test_retired_compact_build_never_reads_old_profit_and_keeps_promotion(archived):
    from scripts import build_compact_statistics_window as window
    write_json(archived, window.CONFIG_PATH, window.CONFIG)
    calendar = archived / window.settlement.CALENDAR_PATH
    calendar.parent.mkdir(parents=True, exist_ok=True)
    calendar.write_bytes(b'pinned-calendar')
    with patch.object(window.settlement, '_strict_open_dates', return_value=['20260910', '20261106']), \
         patch.object(window.settlement, 'CALENDAR_SHA256', hashlib.sha256(calendar.read_bytes()).hexdigest()), \
         patch.object(window, 'profit_section', side_effect=AssertionError('retired profit read')), \
         patch.object(window, 'promotion_section', return_value={'status':'UNAVAILABLE','reason':'NO_PROMOTION_FIXTURE'}):
        value = window.build_window(archived, signal_date='20261106')
    assert value['profit'] == {'status':'UNAVAILABLE','reason':'LEGACY_P1_SHADOW_ARCHIVED'}
    assert value['promotion']['reason'] == 'NO_PROMOTION_FIXTURE'
    assert policy.POLICY_PATH in [item['path'] for item in value['source_files']]
    assert not any('executable_profit' in item['path'] for item in value['source_files'])
    window.validate_window(value)


def test_primary_pages_checks_run_before_auxiliary_and_remain_blocking():
    workflow = yaml.safe_load((ROOT/'.github/workflows/deploy_dc20_pages.yml').read_text())
    steps = workflow['jobs']['deploy']['steps']
    names = [s.get('name','') for s in steps]
    main = names.index('Verify public DC2.0 Pages revision')
    candidate = names.index('Verify public active candidate activation and versioned ledger')
    assert main < names.index('Verify public compact statistics window')
    assert candidate < names.index('Verify public compact statistics window')
    for i in (main, candidate):
        assert not steps[i].get('continue-on-error', False)
    for name in ('Build independent compact statistics window','Generate and bind simulated account with the main publication',
                 'Verify public simulated account and publication binding','Verify public compact statistics window'):
        step = next(s for s in steps if s.get('name') == name)
        assert step.get('continue-on-error') is True
    guard = next(s for s in steps if s.get('id') == 'legacy_profit_relative')
    assert guard['if'] == "${{ env.LEGACY_PROFIT_ARCHIVED != 'true' }}"


def test_retired_writers_do_not_hold_current_research_delivery_barrier():
    from scripts.profit_research_delivery import blocking_runs
    runs = [dict(id=i,path='.github/workflows/'+path,status='in_progress') for i,path in enumerate([
        'run_primary_profit_daily.yml','run_primary_profit_shadow.yml',
        'research_candidate_natural_settlement.yml'])]
    assert blocking_runs(runs) == [2]


def test_current_ridge_payload_renders_old_research_as_archive_without_shadow_access():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required')
    html = (ROOT/'decision.html').read_text()
    body = re.search(r'    function renderExecutableProfitResearch\(plan\) \{.*?(?=\n    function validatedPFillShadowTop2)', html, re.S).group(0)
    script = """
const els = new Proxy({}, {get(target,key){return target[key] ??= {hidden:false,innerHTML:'',textContent:''}}});
const state = {index:0,currentExecutableProfitResearch:{status:'ready',kind:'candidate_fixed_ridge',projection:{signal_date:'20261106'}}};
""" + body + """
renderExecutableProfitResearch({});
console.log(JSON.stringify({state:els.executableProfitResearchState.textContent,
  content:els.executableProfitResearchContent.innerHTML,hidden:els.executableProfitShadowPanel.hidden}));
"""
    result = subprocess.run([node, '-'], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value['hidden'] is True and value['state'] == '旧 P1 / Shadow · 历史归档'
    assert '固定 Ridge' in value['content'] and 'daily_mixed_top2_index.json' in value['content']
