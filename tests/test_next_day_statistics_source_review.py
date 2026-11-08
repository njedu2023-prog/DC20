"""Published descriptive statistics may rotate surface pins, never model policy."""
from pathlib import Path
import copy
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
REVIEW_PATH = 'models/decision_source_surface_review_20261108_next_day_statistics.json'
REVIEW_SHA = '12755015da81238931d7a55b3fee87b407b36353e9c362af85559b8258228336'
MANIFEST = 'models/decision_model_freeze.json'
INVENTORY = 'forward/model_inventory.json'
PIN_PATHS = {'.github/workflows/deploy_dc20_pages.yml', 'decision.html'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def review():
    raw = (ROOT / REVIEW_PATH).read_bytes()
    assert sha(raw) == REVIEW_SHA
    value = json.loads(raw)
    assert value['schema_version'] == 'dc20_next_day_statistics_source_review_v1'
    assert value['approved_base_commit'] == '7034de1b95c384f4307db018737a0a2635453488'
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


def source_before_next_day(path):
    """Restore verified pre-optimization bytes for the immutable review chain."""
    changes = {item['path']: item for item in review()['source_changes']}
    if path in changes:
        return inverse(changes[path])
    target = ROOT / path
    assert not target.is_symlink()
    return target.read_bytes()


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
    assert 'scripts/build_next_day_statistics.cjs' not in current['pinned_files']


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


def test_models_contracts_frozen_inputs_and_prior_review_evidence_preserved():
    value = review()
    assert len(value['preserved_files']) > 300
    preserved = {item['path'] for item in value['preserved_files']}
    assert {
        'models/decision_candidate_profit_activation_v1.json',
        'models/decision_shadow_exit_policy_1000_v1.json',
        'models/decision_source_surface_review_20261107_delivery_hardening.json',
        'work/profit_1000_upgrade/candidate_natural_model/evaluation.json',
        'work/profit_1000_upgrade/candidate_natural_forward/day_20261106.json',
        'work/profit_1000_upgrade/candidate_natural_forward/local_freeze_20261106.json',
    } <= preserved
    for item in value['preserved_files']:
        target = ROOT / item['path']
        assert not target.is_symlink()
        raw = target.read_bytes()
        assert len(raw) == item['bytes'] and sha(raw) == item['sha256'], item['path']
    assert sha((ROOT / value['archive_manifest']['path']).read_bytes()) == value['archive_manifest']['sha256']


def test_exact_baseline_connects_to_immutable_prior_review():
    prior = json.loads((ROOT / 'models/decision_source_surface_review_20261107_delivery_hardening.json').read_bytes())
    current = {item['path']: item for item in review()['source_changes']}
    for item in prior['source_changes']:
        assert current[item['path']]['baseline_sha256'] == item['current_sha256']
        assert current[item['path']]['baseline_bytes'] == item['current_bytes']
