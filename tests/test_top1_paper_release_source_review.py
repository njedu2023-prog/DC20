"""Validate this release before exposing historical bytes to older source audits."""
import copy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

import pytest

from tests import top1_paper_source_delta as count_delta

ROOT = Path(__file__).resolve().parents[1]
REVIEW = 'models/decision_source_surface_review_20261111_top1_paper.json'
REVIEW_SHA = '16980a8b82ee0ce9ed40f0f0a63a5b0f9db9e463cbac3d1e570599cfd6b2be98'
MANIFEST = 'models/decision_model_freeze.json'
INVENTORY = 'forward/model_inventory.json'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def git_blob(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def read(path):
    assert isinstance(path, str) and path and not path.startswith('/') and '\\' not in path
    assert all(part not in ('', '.', '..') for part in path.split('/'))
    target = ROOT / path
    assert not any(node.is_symlink() for node in (target, *target.parents))
    return count_delta.before_count(path, target.read_bytes())


@lru_cache(maxsize=2)
def parse_review(raw):
    assert sha(raw) == REVIEW_SHA
    doc = json.loads(raw)
    assert doc['schema_version'] == 'dc20_top1_paper_source_review_v1'
    assert doc['approved_base_commit'] == 'b7b5b275ad8fecb43bb6aa341080f8d950d4e751'
    assert doc['approved_base_tree'] == '571dac4f20659de4fd75814d4cdb77f0207baaf2'
    assert (doc['start_signal_date'], doc['slot'], doc['per_trade_budget']) == ('20260914', 1, 100000)
    items = doc['source_changes']
    paths = {item['path'] for item in items}
    assert len(items) == len(paths)
    assert {MANIFEST, INVENTORY, 'decision.html', 'scripts/top1_realized_paper_account.cjs',
            'scripts/build_profit_paper_account.cjs', 'tests/test_daily_metadata_source_review.py'} <= paths
    for path in paths:
        assert (path in {MANIFEST, INVENTORY, 'decision.html'}
                or re.fullmatch(r'(?:scripts|tests|src/top10decision/decision|work/profit_1000_upgrade)/[^/]+\.(?:py|cjs)', path)
                or re.fullmatch(r'work/profit_1000_upgrade/candidate_(?:eligible|natural)_forward_registration\.json', path)
                or path in {'.github/workflows/verify_daily_cleanup.yml', '.github/workflows/deploy_dc20_pages.yml'}), path
    return doc


def review():
    return parse_review(read(REVIEW))


@lru_cache(maxsize=1)
def reviewed_existing():
    return {item['path']: item for item in review()['source_changes'] if item['baseline_exists']}


@lru_cache(maxsize=100)
def prior_bytes(path, current, review_raw):
    doc = parse_review(review_raw)
    item = next(v for v in doc['source_changes'] if v['path'] == path)
    assert len(current) == item['current_bytes'] and sha(current) == item['current_sha256'], path
    assert item['baseline_exists']
    lines = current.decode().splitlines(keepends=True)
    offset = end = 0
    for change in item['inverse_changes']:
        start = change['current_start'] - 1
        assert end <= start <= len(lines) and change['baseline_start'] - 1 == start + offset
        removed = len(change['current_lines'])
        assert lines[start:start + removed] == change['current_lines']
        end = start + removed
        offset += len(change['baseline_lines']) - removed
    for change in reversed(item['inverse_changes']):
        start = change['current_start'] - 1
        lines[start:start + len(change['current_lines'])] = change['baseline_lines']
    prior = ''.join(lines).encode()
    assert sha(prior) == item['baseline_sha256'] and len(prior) == item['baseline_bytes'], path
    assert git_blob(prior) == item['baseline_git_blob_sha1'], path
    return prior


def source_before_top1_release(path, raw):
    # A caller may pass live bytes or an already verified earlier view.
    if path in count_delta.files() and raw == (ROOT / path).read_bytes():
        raw = read(path)
    if path not in reviewed_existing():
        return raw
    current = read(path)
    prior = prior_bytes(path, current, read(REVIEW))
    assert raw in (current, prior), path
    return prior


def test_all_current_sources_exact_predecessors_and_prior_reviews():
    doc = review()
    for item in doc['source_changes']:
        path = item['path']
        current = read(path)
        assert len(current) == item['current_bytes'] and sha(current) == item['current_sha256'], path
        if item['baseline_exists']:
            prior = source_before_top1_release(path, current)
            assert sha(prior) == item['baseline_sha256'] and git_blob(prior) == item['baseline_git_blob_sha1']
    assert 'models/decision_source_surface_review_20261111_daily_metadata.json' in doc['prior_review_blobs']
    for path, expected in doc['prior_review_blobs'].items():
        assert git_blob(read(path)) == expected, path


def test_model_policy_weights_and_inventory_only_rotate_reviewed_pins():
    now_bytes = read(MANIFEST)
    now = json.loads(now_bytes)
    old = json.loads(source_before_top1_release(MANIFEST, now_bytes))
    pins = now.pop('pinned_files')
    old_pins = old.pop('pinned_files')
    assert now == old and pins.keys() == old_pins.keys()
    reviewed_paths = reviewed_existing()
    for path, digest in pins.items():
        if digest != old_pins[path]:
            assert path in reviewed_paths
            assert digest == sha(read(path))
    expected = copy.deepcopy(json.loads(source_before_top1_release(INVENTORY, read(INVENTORY))))
    matches = [v for v in expected['assets'] if v['path'] == MANIFEST]
    assert len(matches) == 1
    matches[0].update(bytes=len(now_bytes), sha256=sha(now_bytes))
    assert json.loads(read(INVENTORY)) == expected


@pytest.mark.parametrize('path', [REVIEW, MANIFEST, 'decision.html', 'scripts/top1_realized_paper_account.cjs'])
def test_tampering_cannot_be_hidden_by_view_or_cached_result(monkeypatch, path):
    real = Path.read_bytes
    original = read(path)
    if path == REVIEW:
        review()
    elif path in reviewed_existing():
        source_before_top1_release(path, original)
    monkeypatch.setattr(Path, 'read_bytes', lambda target: real(target) + (b'\n' if target == ROOT/path else b''))
    with pytest.raises(AssertionError):
        if path == REVIEW:
            review()
        elif path in reviewed_existing():
            source_before_top1_release(path, original)
        else:
            test_all_current_sources_exact_predecessors_and_prior_reviews()


def test_current_count_release_and_predecessors():
    files = count_delta.files()
    assert {'decision.html', MANIFEST, INVENTORY, 'tests/test_top1_paper_release_source_review.py'} <= files.keys()
    for path, record in files.items():
        assert not path.startswith(('data/', 'outputs/'))
        assert path == MANIFEST or not path.startswith('models/')
        current = (ROOT / path).read_bytes()
        assert sha(count_delta.before_count(path, current)) == record['base_sha256']
        with pytest.raises(AssertionError):
            count_delta.before_count(path, current + b'\n')
