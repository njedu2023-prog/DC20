"""Exact inverse audit of the reviewed suspension continuation successor.

This module is test-only. Production admission never imports or trusts it.
Historical reviews retain their original bytes; each newer byte is checked
before an older view is reconstructed for that historical audit.
"""
from pathlib import Path
import hashlib
import json
import pytest

ROOT = Path(__file__).resolve().parents[1]
REVIEW_PATH = 'models/decision_source_surface_review_20261110_suspension_continuation.json'
REVIEW_SHA = '7aed2e22ecd84c3b34ec1bf3b92862b1814892a75c09e51f1dbad22156cb5c32'
BASE = 'af3507a68ebe4dd85326e98e2eaa9d0cf62b10f0'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    assert type(path) is str and path and not path.startswith('/') and '\\' not in path
    assert all(p not in ('', '.', '..') for p in path.split('/'))
    target = ROOT / path
    assert not any(p.is_symlink() for p in (target, *target.parents))
    from tests.test_reliability_isolation_source_review import source_before_reliability_isolation
    return source_before_reliability_isolation(path, target.read_bytes())


def review():
    raw = read(REVIEW_PATH)
    assert sha(raw) == REVIEW_SHA
    doc = json.loads(raw)
    assert doc['schema_version'] == 'dc20_suspension_continuation_source_review_v1'
    assert doc['approved_base_commit'] == BASE
    assert doc['scope'] == 'EXACT_REVIEWED_MANIFEST_TRANSITION_AND_HISTORICAL_PROFILE_PRESERVATION'
    assert doc['boundaries'] == {
        'model_weights_changed': False,
        'frozen_lists_or_source_data_changed': False,
        'historical_review_bytes_changed': False,
        'time_CAS_or_source_guards_weakened': False,
        'actual_trading_enabled': False,
    }
    paths = [v['path'] for v in doc['source_changes']]
    assert len(paths) == len(set(paths)) and paths
    assert all(not p.startswith(('data/', 'outputs/')) for p in paths)
    assert all(not p.startswith('models/decision_source_surface_review_') for p in paths)
    return doc


def inverse(item, raw):
    assert type(raw) is bytes
    assert len(raw) == item['current_bytes'] and sha(raw) == item['current_sha256']
    assert item['baseline_exists'] is True and item['inverse_changes']
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
    old = ''.join(lines).encode()
    assert len(old) == item['baseline_bytes'] and sha(old) == item['baseline_sha256']
    return old


def source_before_suspension_continuation(path, raw):
    from tests.test_reliability_isolation_source_review import source_before_reliability_isolation
    raw = source_before_reliability_isolation(path, raw)
    changes = {v['path']: v for v in review()['source_changes']}
    if path not in changes:
        return raw
    assert read(path) == raw
    return inverse(changes[path], raw)


def test_exact_predecessors_and_preserved_model_source_freezes():
    for item in review()['source_changes']:
        assert sha(source_before_suspension_continuation(item['path'], read(item['path']))) == item['baseline_sha256']
    for item in review()['preserved_files']:
        raw = read(item['path'])
        assert len(raw) == item['bytes'] and sha(raw) == item['sha256'], item['path']


def test_no_new_model_or_original_p0_bytes_are_admitted_by_audit():
    doc = review()
    preserved = {v['path'] for v in doc['preserved_files']}
    assert {'models/decision_model_freeze.json', 'data/market/confirmed_suspensions.json',
        'outputs/decision/primary_d_receipt_20261110.json',
        'outputs/decision/primary_d_runtime_features_20261110.csv',
        'outputs/decision/three_rank_top10_20261110.json',
        'outputs/decision/three_rank_top10_20261110.csv'} <= preserved
    assert 'models/decision_source_surface_review_20261110_suspension_pin.json' in preserved


def test_each_changed_byte_and_review_mutation_is_rejected(monkeypatch):
    original = Path.read_bytes
    for item in review()['source_changes']:
        target = ROOT / item['path']
        with monkeypatch.context() as patch:
            patch.setattr(Path, 'read_bytes', lambda p: original(p) + (b'\n' if p == target else b''))
            with pytest.raises(AssertionError):
                source_before_suspension_continuation(item['path'], read(item['path']))
    target = ROOT / REVIEW_PATH
    monkeypatch.setattr(Path, 'read_bytes', lambda p: original(p) + (b'\n' if p == target else b''))
    with pytest.raises(AssertionError):
        review()
