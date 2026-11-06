"""The approved cleanup may rotate delivery surface pins, never model policy."""
from pathlib import Path
import copy
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
REVIEW_PATH = 'models/decision_source_surface_review_20261107_system_cleanup.json'
REVIEW_SHA = "f05a1bb0f2794ca8bd347d9522446c1718fe857f574ef91034e54b8489ca1a63"
MANIFEST = 'models/decision_model_freeze.json'
INVENTORY = 'forward/model_inventory.json'
PIN_PATHS = {
    '.github/workflows/deploy_dc20_pages.yml',
    '.github/workflows/run_primary_profit_forward_shadow.yml',
    '.github/workflows/run_primary_profit_rankings.yml',
    'decision.html', 'scripts/build_compact_statistics_window.py',
    'tests/test_pages_truthfulness_workflow.py',
    'tests/test_primary_profit_forward_shadow_workflow.py',
    'tests/test_primary_profit_rankings_p1.py',
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def review():
    raw = (ROOT / REVIEW_PATH).read_bytes()
    assert sha(raw) == REVIEW_SHA
    value = json.loads(raw)
    assert value['schema_version'] == 'dc20_daily_cleanup_source_review_v1'
    assert value['approved_base_commit'] == '6a0f9e0a6ea5e57957fbac5c30849dd508d1daf2'
    assert value['updated_surface_pins'] == sorted(PIN_PATHS)
    assert value['boundaries'] and all(v is False for v in value['boundaries'].values())
    assert {v['path'] for v in value['source_changes']} == PIN_PATHS | {MANIFEST, INVENTORY}
    return value


def inverse(item):
    target = ROOT / item['path']
    assert not target.is_symlink()
    raw = target.read_bytes()
    assert len(raw) == item['current_bytes'] and sha(raw) == item['current_sha256']
    assert item['baseline_exists'] is True and item['inverse_changes']
    lines = raw.decode().splitlines(keepends=True)
    previous_end = offset = 0
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


def test_review_restores_all_exact_baseline_source_bytes():
    for item in review()['source_changes']:
        inverse(item)


def test_only_reviewed_surface_pins_change_no_model_or_trading_policy():
    value = review()
    baseline = json.loads(inverse(next(v for v in value['source_changes'] if v['path'] == MANIFEST)))
    current = json.loads((ROOT / MANIFEST).read_bytes())
    assert baseline.keys() == current.keys()
    assert {k:v for k,v in current.items() if k != 'pinned_files'} == {k:v for k,v in baseline.items() if k != 'pinned_files'}
    assert baseline['pinned_files'].keys() == current['pinned_files'].keys()
    assert {p for p in baseline['pinned_files'] if baseline['pinned_files'][p] != current['pinned_files'][p]} == PIN_PATHS
    for path, digest in current['pinned_files'].items():
        assert not (ROOT / path).is_symlink()
        assert sha((ROOT / path).read_bytes()) == digest, path


def test_inventory_changes_only_manifest_asset_digest_and_size():
    value = review()
    before = json.loads(inverse(next(v for v in value['source_changes'] if v['path'] == INVENTORY)))
    expected = copy.deepcopy(before)
    matches = [a for a in expected['assets'] if a['path'] == MANIFEST]
    assert len(matches) == 1
    raw = (ROOT / MANIFEST).read_bytes()
    matches[0]['sha256'] = sha(raw)
    matches[0]['bytes'] = len(raw)
    assert json.loads((ROOT / INVENTORY).read_bytes()) == expected


def test_all_prior_models_contracts_frozen_inputs_and_review_evidence_preserved():
    value = review()
    assert len(value['preserved_files']) > 100
    for item in value['preserved_files']:
        target = ROOT / item['path']
        assert not target.is_symlink()
        raw = target.read_bytes()
        assert len(raw) == item['bytes'] and sha(raw) == item['sha256'], item['path']
    assert sha((ROOT / value['archive_manifest']['path']).read_bytes()) == value['archive_manifest']['sha256']
