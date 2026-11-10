"""Additive exact-byte audit; never used for production admission or inference."""
from pathlib import Path
import copy
import hashlib
import json
import pytest

ROOT = Path(__file__).resolve().parents[1]
REVIEW_PATH = 'models/decision_source_surface_review_20261110_suspension_pin.json'
REVIEW_SHA = 'a8ccc28d025cdaa9207a97f30f6d45fca4fa572cba4d38678287abe0a459f0ac'
DATA = 'data/market/confirmed_suspensions.json'
MANIFEST = 'models/decision_model_freeze.json'
INVENTORY = 'forward/model_inventory.json'
CHANGED = {DATA, MANIFEST, INVENTORY, 'tests/test_next_day_p0_profile_source_review.py',
           'tests/test_existing_continuation_source_review.py', 'tests/test_settlement_noop_source_review.py',
           'tests/test_next_day_statistics_source_review.py', '.github/workflows/verify_daily_cleanup.yml'}

def sha(raw): return hashlib.sha256(raw).hexdigest()

def read(path):
    assert isinstance(path, str) and path and not path.startswith('/') and '\\' not in path
    assert all(p not in ('', '.', '..') for p in path.split('/'))
    target = ROOT / path
    assert not any(p.is_symlink() for p in (target, *target.parents))
    return target.read_bytes()

def review():
    raw = read(REVIEW_PATH)
    assert sha(raw) == REVIEW_SHA
    doc = json.loads(raw)
    assert doc['schema_version'] == 'dc20_suspension_pin_source_review_v1'
    assert doc['approved_base_commit'] == '66bd8082306ec7c535fcea8b5574f0ac17504219'
    assert len(doc['source_changes']) == len(CHANGED)
    assert {v['path'] for v in doc['source_changes']} == CHANGED
    assert doc['boundaries'] and not any(doc['boundaries'].values())
    return doc

def source_before_suspension_pin(path, raw):
    if path not in CHANGED:
        return raw
    assert read(path) == raw
    item = next(v for v in review()['source_changes'] if v['path'] == path)
    assert len(raw) == item['current_bytes'] and sha(raw) == item['current_sha256']
    lines = raw.decode().splitlines(keepends=True)
    end = offset = 0
    for c in item['inverse_changes']:
        start = c['current_start'] - 1
        assert end <= start <= len(lines) and c['baseline_start'] - 1 == start + offset
        assert lines[start:start+len(c['current_lines'])] == c['current_lines']
        end = start + len(c['current_lines'])
        offset += len(c['baseline_lines']) - len(c['current_lines'])
    for c in reversed(item['inverse_changes']):
        start = c['current_start'] - 1
        lines[start:start+len(c['current_lines'])] = c['baseline_lines']
    old = ''.join(lines).encode()
    assert len(old) == item['baseline_bytes'] and sha(old) == item['baseline_sha256']
    return old

def test_exact_predecessors_and_unchanged_prior_evidence():
    for item in review()['source_changes']:
        assert sha(source_before_suspension_pin(item['path'], read(item['path']))) == item['baseline_sha256']
    for item in review()['preserved_files']:
        raw = read(item['path'])
        assert len(raw) == item['bytes'] and sha(raw) == item['sha256'], item['path']

def test_only_disclosure_pin_changes_and_current_inventory_is_bound():
    now = json.loads(read(MANIFEST))
    old = json.loads(source_before_suspension_pin(MANIFEST, read(MANIFEST)))
    expected = copy.deepcopy(old)
    expected['pinned_files'][DATA] = sha(read(DATA))
    assert now == expected
    inv = json.loads(read(INVENTORY))
    prior = json.loads(source_before_suspension_pin(INVENTORY, read(INVENTORY)))
    entry = next(a for a in prior['assets'] if a['path'] == MANIFEST)
    entry.update(sha256=sha(read(MANIFEST)), bytes=len(read(MANIFEST)))
    assert inv == prior

def test_disclosure_is_exact_addition_with_old_intervals_unchanged():
    now = json.loads(read(DATA)); old = json.loads(source_before_suspension_pin(DATA, read(DATA)))
    assert now['suspensions'][:-1] == old['suspensions']
    added = now['suspensions'][-1]
    assert (added['ts_code'], added['start_date'], added['resume_date']) == ('002623.SZ','20261103','20261110')
    assert len(added['sources']) == 3
    old['suspensions'].append(added); old['verified_at_utc'] = now['verified_at_utc']
    assert now == old

@pytest.mark.parametrize('path', sorted(CHANGED | {REVIEW_PATH}))
def test_mutation_rejected(monkeypatch, path):
    original = Path.read_bytes
    target = ROOT / path
    monkeypatch.setattr(Path, 'read_bytes', lambda p: original(p) + (b'\n' if p == target else b''))
    with pytest.raises(AssertionError):
        if path == REVIEW_PATH: review()
        else: source_before_suspension_pin(path, read(path))
