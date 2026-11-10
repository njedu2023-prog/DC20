"""Exact additive review of the read-only observer-to-settlement filter.

This adapter restores only reviewed source bytes for older audit readers. It
does not change runtime profiles, production pins, frozen data or old reviews.
"""
from pathlib import Path
import hashlib
import json

import pytest

ROOT = Path(__file__).resolve().parents[1]
REVIEW_PATH = 'models/decision_source_surface_review_20261110_settlement_noop.json'
REVIEW_SHA = 'e4846e54b387bab3d98f0c13064e04d0c1bedd23d6991de95a2dff7e3431a2a6'
BASE = '203e7b04cc46c26d41492090b0b1155915962034'
CHANGED = {
    '.github/workflows/research_candidate_natural_settlement.yml',
    'work/profit_1000_upgrade/test_candidate_natural_settlement_contract.py',
    'tests/test_decision_source_surface_rotation.py',
}
ADDED = {'scripts/candidate_settlement_trigger.py', 'tests/test_candidate_settlement_trigger.py'}
BOUNDARIES = {
    'verified_noop_observer_suppresses_quote_collection': True,
    'model_weights_changed': False,
    'production_pin_manifest_changed': False,
    'original_trigger_gate_changed': False,
    'frozen_members_or_scores_changed': False,
    'historical_journal_or_empty_http_changed': False,
    'same_asof_rules_changed': False,
    'source_validation_weakened': False,
    'schedule_or_manual_semantics_changed': False,
    'publisher_recovery_changed': False,
    'actual_trading_enabled': False,
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def raw_source(path):
    assert isinstance(path, str) and path and not path.startswith('/') and '\\' not in path
    assert all(part not in ('', '.', '..') for part in path.split('/'))
    target = ROOT / path
    assert not any(p.is_symlink() for p in (target, *target.parents))
    return target.read_bytes()


def review():
    raw = raw_source(REVIEW_PATH)
    assert sha(raw) == REVIEW_SHA
    doc = json.loads(raw)
    assert doc['schema_version'] == 'dc20_settlement_noop_source_review_v1'
    assert doc['approved_base_commit'] == BASE
    assert doc['scope'] == 'READ_ONLY_NOOP_OBSERVER_SETTLEMENT_FILTER'
    assert doc['boundaries'] == BOUNDARIES
    assert len(doc['source_changes']) == 3 and {v['path'] for v in doc['source_changes']} == CHANGED
    assert len(doc['added_files']) == 2 and {v['path'] for v in doc['added_files']} == ADDED
    return doc


def inverse(item, raw):
    assert item['path'] in CHANGED and item['baseline_exists'] is True
    assert type(raw) is bytes and len(raw) == item['current_bytes'] and sha(raw) == item['current_sha256']
    lines = raw.decode().splitlines(keepends=True)
    previous_end = offset = 0
    assert item['inverse_changes']
    for change in item['inverse_changes']:
        start = change['current_start'] - 1
        assert previous_end <= start <= len(lines)
        assert change['baseline_start'] - 1 == start + offset
        assert lines[start:start + len(change['current_lines'])] == change['current_lines']
        previous_end = start + len(change['current_lines'])
        offset += len(change['baseline_lines']) - len(change['current_lines'])
    for change in reversed(item['inverse_changes']):
        start = change['current_start'] - 1
        lines[start:start + len(change['current_lines'])] = change['baseline_lines']
    restored = ''.join(lines).encode()
    assert len(restored) == item['baseline_bytes'] and sha(restored) == item['baseline_sha256']
    return restored


def source_before_settlement_noop(path, raw):
    if path not in CHANGED:
        return raw
    assert raw_source(path) == raw
    item = next(v for v in review()['source_changes'] if v['path'] == path)
    return inverse(item, raw)


def test_review_restores_all_exact_source_bytes_and_binds_additions():
    doc = review()
    for item in doc['source_changes']:
        assert source_before_settlement_noop(item['path'], raw_source(item['path'])) == inverse(item, raw_source(item['path']))
    for item in doc['added_files']:
        raw = raw_source(item['path'])
        assert len(raw) == item['bytes'] and sha(raw) == item['sha256']


def test_original_model_gate_journal_profiles_and_reviews_are_unchanged():
    items = review()['preserved_files']
    paths = {v['path'] for v in items}
    assert len(paths) == len(items) and len(items) >= 15
    assert {'models/decision_model_freeze.json',
        'models/decision_candidate_profit_activation_v1.json',
        'work/profit_1000_upgrade/candidate_natural_trigger_gate.py',
        'work/profit_1000_upgrade/candidate_natural_journal_git.py',
        'work/profit_1000_upgrade/candidate_natural_outcomes.py'} <= paths
    for item in items:
        raw = raw_source(item['path'])
        assert len(raw) == item['bytes'] and sha(raw) == item['sha256'], item['path']
    manifest = json.loads(raw_source('models/decision_model_freeze.json'))
    assert manifest['active'] is True and len(manifest['pinned_files']) == 237
    assert not (set(manifest['pinned_files']) & (CHANGED | ADDED | {REVIEW_PATH}))


def test_adapter_has_only_the_exact_two_line_hook():
    path = 'tests/test_decision_source_surface_rotation.py'
    current = raw_source(path)
    old = source_before_settlement_noop(path, current)
    before = b'    raw = _statistics_refresh_previous(path, target.read_bytes())'
    after = (b'    from tests.test_settlement_noop_source_review import source_before_settlement_noop\n'
        b'    raw = _statistics_refresh_previous(path, source_before_settlement_noop(path, target.read_bytes()))')
    assert old.count(before) == 1 and old.replace(before, after) == current


def test_exact_prior_yaml_reconnects_to_historical_review():
    from tests import test_decision_source_surface_rotation as prior
    old = prior._source_before_reliability('.github/workflows/research_candidate_natural_settlement.yml')
    original_review = json.loads(raw_source('models/decision_source_surface_review_20260915_reliability.json'))
    item = next(v for v in original_review['source_changes'] if v['path'] == '.github/workflows/research_candidate_natural_settlement.yml')
    assert sha(old) == item['baseline_sha256']


@pytest.mark.parametrize('path', sorted(CHANGED))
def test_mutation_cannot_use_old_review_bytes(monkeypatch, path):
    original = Path.read_bytes
    target = ROOT / path
    monkeypatch.setattr(Path, 'read_bytes', lambda p: original(p) + (b'\n' if p == target else b''))
    with pytest.raises(AssertionError):
        source_before_settlement_noop(path, target.read_bytes())


def test_review_hash_cannot_be_changed(monkeypatch):
    original = Path.read_bytes
    target = ROOT / REVIEW_PATH
    monkeypatch.setattr(Path, 'read_bytes', lambda p: original(p) + (b'\n' if p == target else b''))
    with pytest.raises(AssertionError):
        review()
