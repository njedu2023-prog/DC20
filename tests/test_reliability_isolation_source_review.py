"""Reviewed current bytes plus immutable predecessor views, test use only.

Production never imports this module. Historical audits continue to verify
their original review documents, after this successor's exact bytes pass.
"""
from pathlib import Path
import hashlib
import json
import re
import pytest

ROOT = Path(__file__).resolve().parents[1]
REVIEW = 'models/decision_source_surface_review_20261110_reliability_isolation.json'
REVIEW_SHA = '55532b6589658c2f1cc7f5da174335f41cb166d181777c8ff5c4607fdc73c790'
BASE = '65d4e0c1d80c177645eff1d1bbd53fee71cffda9'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def raw_read(path):
    assert type(path) is str and path and not path.startswith('/') and '\\' not in path
    assert all(p not in ('', '.', '..') for p in path.split('/'))
    target = ROOT / path
    assert not any(p.is_symlink() for p in (target, *target.parents))
    return target.read_bytes()


def review():
    raw = raw_read(REVIEW)
    assert sha(raw) == REVIEW_SHA, 'RELIABILITY_REVIEW_CHANGED'
    doc = json.loads(raw)
    assert doc['schema_version'] == 'dc20_reliability_isolation_source_review_v1'
    assert doc['approved_base_commit'] == BASE
    assert doc['suspension_registry_start_D'] == '20261111'
    assert doc['model_weights_changed'] is False
    assert doc['selection_or_trading_rules_changed'] is False
    assert doc['historical_frozen_artifacts_changed'] is False
    paths = [v['path'] for v in doc['source_changes']]
    assert paths and len(paths) == len(set(paths))
    assert not any(v['baseline_exists'] and v['path'].startswith(('data/', 'outputs/'))
                   for v in doc['source_changes'])
    return doc


def inverse(item, raw):
    assert len(raw) == item['current_bytes'] and sha(raw) == item['current_sha256']
    assert item['baseline_exists'] is True
    lines = raw.decode().splitlines(keepends=True)
    end = offset = 0
    for change in item['inverse_changes']:
        start = change['current_start'] - 1
        assert end <= start <= len(lines)
        assert change['baseline_start'] - 1 == start + offset
        assert lines[start:start + len(change['current_lines'])] == change['current_lines']
        end = start + len(change['current_lines'])
        offset += len(change['baseline_lines']) - len(change['current_lines'])
    for change in reversed(item['inverse_changes']):
        start = change['current_start'] - 1
        lines[start:start + len(change['current_lines'])] = change['baseline_lines']
    restored = ''.join(lines).encode()
    assert len(restored) == item['baseline_bytes'] and sha(restored) == item['baseline_sha256']
    return restored


def source_before_reliability_isolation(path, raw):
    """Accept only exact current/predecessor bytes with current disk verified."""
    changes = {v['path']: v for v in review()['source_changes']}
    item = changes.get(path)
    if not item:
        return raw
    current = raw_read(path)
    assert len(current) == item['current_bytes'] and sha(current) == item['current_sha256'], path
    assert item['baseline_exists'], 'NEW_FILE_HAS_NO_HISTORICAL_PREDECESSOR'
    prior = inverse(item, current)
    assert raw in (current, prior), 'UNREVIEWED_HISTORICAL_VIEW:' + path
    return prior


def test_reviewed_current_bytes_and_exact_predecessors():
    for item in review()['source_changes']:
        current = raw_read(item['path'])
        assert len(current) == item['current_bytes'] and sha(current) == item['current_sha256'], item['path']
        if item['baseline_exists']:
            assert sha(source_before_reliability_isolation(item['path'], current)) == item['baseline_sha256']


def test_frozen_business_inputs_weights_and_outputs_preserved():
    for item in review()['preserved_files']:
        raw = raw_read(item['path'])
        assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == item['git_blob_sha1'], item['path']


def test_historical_audit_does_not_pin_daily_mutable_data():
    paths = {item['path'] for item in review()['preserved_files']}
    assert not paths.intersection({'data/market/trade_cal_sse.csv',
        'outputs/decision/primary_d_runtime_index.json', 'outputs/decision/profit_research/latest.json',
        'outputs/decision/candidate_profit_v1/index.json', 'outputs/decision/candidate_profit_v1/summary.json'})
    assert not any(p.endswith('/_sync_meta.json') or p.startswith('outputs/decision/candidate_profit_v1/') for p in paths)
    assert 'work/profit_1000_upgrade/candidate_natural_model/evaluation.json' in paths


def test_model_manifest_changes_only_reviewed_code_bindings():
    path = 'models/decision_model_freeze.json'
    current = json.loads(raw_read(path))
    prior = json.loads(source_before_reliability_isolation(path, raw_read(path)))
    current_pins = current.pop('pinned_files'); prior_pins = prior.pop('pinned_files')
    assert current == prior
    assert set(prior_pins) <= set(current_pins)
    for path, digest in current_pins.items():
        if prior_pins.get(path) != digest:
            assert ((path.startswith(('src/', 'scripts/', 'work/profit_1000_upgrade/')) and path.endswith('.py'))
                    or (path.startswith('.github/workflows/') and path.endswith(('.yml', '.yaml')))), path
            assert sha(raw_read(path)) == digest
    assert current_pins['data/market/confirmed_suspensions.json'] == prior_pins['data/market/confirmed_suspensions.json']


@pytest.mark.parametrize('name', ['candidate_natural_forward', 'candidate_eligible_forward'])
def test_registration_business_rules_unchanged(name):
    path = 'work/profit_1000_upgrade/' + name + '_registration.json'
    current = json.loads(raw_read(path))
    prior = json.loads(source_before_reliability_isolation(path, raw_read(path)))
    current_pins = current.pop('code_dependencies')
    prior_pins = prior.pop('code_dependencies')
    assert current == prior
    assert set(prior_pins) <= set(current_pins)
    for code_path, digest in current_pins.items():
        assert sha(raw_read(code_path)) == digest


def test_existing_schedule_times_are_unchanged():
    for item in review()['source_changes']:
        path = item['path']
        if path.startswith('.github/workflows/') and item['baseline_exists']:
            current = raw_read(path)
            prior = source_before_reliability_isolation(path, current)
            assert re.findall(rb'^\s*-?\s*cron:.*$', current, re.M) == re.findall(rb'^\s*-?\s*cron:.*$', prior, re.M), path


def test_modified_current_source_cannot_be_hidden_by_historical_view(monkeypatch):
    original = Path.read_bytes
    item = next(v for v in review()['source_changes'] if v['path'] == 'scripts/publish_primary_three_rank.py')
    target = ROOT / item['path']
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'read_bytes', lambda p: original(p) + (b'\n' if p == target else b''))
        with pytest.raises(AssertionError):
            source_before_reliability_isolation(item['path'], original(target))
    target = ROOT / REVIEW
    monkeypatch.setattr(Path, 'read_bytes', lambda p: original(p) + (b'\n' if p == target else b''))
    with pytest.raises(AssertionError):
        review()
