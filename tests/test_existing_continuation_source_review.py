"""Preserve the exact prior gate review while admitting a read-only successor."""
from pathlib import Path
import hashlib
import json

import pytest

ROOT = Path(__file__).resolve().parents[1]
REVIEW_PATH = 'models/decision_source_surface_review_20261110_existing_continuation.json'
REVIEW_SHA = '8be4824932fae44fc49a6a7cd04f468b79fd3ec6f9a0a7b87fe50c44955cd8dd'
BASE = '451c5c1d25a0c90b4676717b92f068479115d3c8'
GATE = 'work/profit_1000_upgrade/candidate_natural_trigger_gate.py'
GATE_TEST = 'work/profit_1000_upgrade/test_candidate_natural_trigger_gate.py'
PRIOR_TEST = 'tests/test_settlement_noop_source_review.py'
PRIOR_REVIEW = 'models/decision_source_surface_review_20261110_settlement_noop.json'
CHANGED = {GATE, GATE_TEST, PRIOR_TEST}
BOUNDARIES = {
    'existing_complete_freeze_revalidated_from_original_observer_archive': True,
    'fresh_freeze_ancestry_or_time_guards_changed': False,
    'live_current_ref_fabricated': False,
    'live_api_budget_increased': False,
    'model_weights_or_profile_pins_changed': False,
    'production_237_pins_changed': False,
    'original_continuation_module_changed': False,
    'frozen_members_scores_or_source_bytes_changed': False,
    'original_observer_ack_rewritten': False,
    'same_asof_journal_protocol_changed': False,
    'existing_delivery_or_settlement_semantics_changed': False,
    'actual_trading_enabled': False,
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def raw_source(path):
    assert isinstance(path, str) and path and not path.startswith('/') and '\\' not in path
    assert all(part not in ('', '.', '..') for part in path.split('/'))
    target = ROOT / path
    assert not any(p.is_symlink() for p in (target, *target.parents))
    from tests.test_suspension_pin_source_review import source_before_suspension_pin
    return source_before_suspension_pin(path, target.read_bytes())


def review():
    raw = raw_source(REVIEW_PATH)
    assert sha(raw) == REVIEW_SHA
    doc = json.loads(raw)
    assert doc['schema_version'] == 'dc20_existing_continuation_source_review_v1'
    assert doc['approved_base_commit'] == BASE
    assert doc['scope'] == 'READ_ONLY_EXISTING_CONTINUATION_PROOF_FROM_ORIGINAL_CAPTURE'
    assert doc['boundaries'] == BOUNDARIES
    assert len(doc['source_changes']) == 3 and {v['path'] for v in doc['source_changes']} == CHANGED
    assert doc['predecessor_review'] == {'path': PRIOR_REVIEW,
        'sha256': 'e4846e54b387bab3d98f0c13064e04d0c1bedd23d6991de95a2dff7e3431a2a6'}
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
    old = ''.join(lines).encode()
    assert len(old) == item['baseline_bytes'] and sha(old) == item['baseline_sha256']
    return old


def source_before_existing_continuation(path, raw):
    if path not in CHANGED:
        return raw
    assert raw_source(path) == raw
    return inverse(next(v for v in review()['source_changes'] if v['path'] == path), raw)


def test_all_new_gate_and_adapter_bytes_restore_exact_predecessors():
    for item in review()['source_changes']:
        restored = source_before_existing_continuation(item['path'], raw_source(item['path']))
        assert sha(restored) == item['baseline_sha256']


def test_original_admission_model_settlement_and_reviews_unchanged():
    items = review()['preserved_files']
    assert len(items) >= 20 and len({v['path'] for v in items}) == len(items)
    assert {'models/decision_model_freeze.json',
        'work/profit_1000_upgrade/candidate_natural_p0_continuation.py',
        'work/profit_1000_upgrade/candidate_natural_p0_github.py',
        'work/profit_1000_upgrade/candidate_natural_evidence.py',
        'work/profit_1000_upgrade/candidate_natural_settlement_workflow.py', PRIOR_REVIEW} <= {v['path'] for v in items}
    for item in items:
        raw = raw_source(item['path'])
        assert len(raw) == item['bytes'] and sha(raw) == item['sha256'], item['path']
    manifest = json.loads(raw_source('models/decision_model_freeze.json'))
    assert manifest['active'] is True and len(manifest['pinned_files']) == 237
    assert not (set(manifest['pinned_files']) & (CHANGED | {REVIEW_PATH}))


def test_predecessor_review_still_checks_its_exact_gate_bytes():
    from tests import test_settlement_noop_source_review as prior
    prior.test_original_model_gate_journal_profiles_and_reviews_are_unchanged()
    old = json.loads(raw_source(PRIOR_REVIEW))
    preserved = {v['path']:v for v in old['preserved_files']}
    for path in (GATE, GATE_TEST):
        assert sha(source_before_existing_continuation(path, raw_source(path))) == preserved[path]['sha256']


def test_predecessor_adapter_is_only_the_exact_read_hook():
    raw = raw_source(PRIOR_TEST)
    old = source_before_existing_continuation(PRIOR_TEST, raw)
    before = (b"    for item in items:\n        raw = raw_source(item['path'])\n"
        b"        assert len(raw) == item['bytes'] and sha(raw) == item['sha256'], item['path']")
    after = (b'    from tests.test_existing_continuation_source_review import source_before_existing_continuation\n'
        b"    for item in items:\n        raw = source_before_existing_continuation(item['path'], raw_source(item['path']))\n"
        b"        assert len(raw) == item['bytes'] and sha(raw) == item['sha256'], item['path']")
    assert old.count(before) == 1 and old.replace(before, after) == raw


@pytest.mark.parametrize('path', sorted(CHANGED))
def test_unreviewed_changed_source_is_rejected(monkeypatch, path):
    original = Path.read_bytes
    target = ROOT / path
    monkeypatch.setattr(Path, 'read_bytes', lambda p: original(p) + (b'\n' if p == target else b''))
    with pytest.raises(AssertionError):
        source_before_existing_continuation(path, target.read_bytes())


def test_review_manifest_tamper_fails_closed(monkeypatch):
    original = Path.read_bytes
    target = ROOT / REVIEW_PATH
    monkeypatch.setattr(Path, 'read_bytes', lambda p: original(p) + (b'\n' if p == target else b''))
    with pytest.raises(AssertionError):
        review()
