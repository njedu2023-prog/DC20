"""Register the exact page-only P0 profile without rewriting historical proof."""
from pathlib import Path
import ast
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
REVIEW_PATH = 'models/decision_source_surface_review_20261108_next_day_p0_profile.json'
REVIEW_SHA = '56000697e943e75d985e48296de5197286d9acf249091c22b4662944578028c2'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def review():
    raw = (ROOT / REVIEW_PATH).read_bytes()
    assert sha(raw) == REVIEW_SHA
    value = json.loads(raw)
    assert value['schema_version'] == 'dc20_next_day_p0_profile_source_review_v1'
    assert value['approved_base_commit'] == '0a1035b945bf3df47c57a2d8620b4532af22ad92'
    assert value['historical_profile_last_signal_date'] == '20261106'
    assert value['boundaries'] and all(v is False for v in value['boundaries'].values())
    assert len(value['source_changes']) == 11
    return value


def inverse(item):
    target = ROOT / item['path']
    assert not target.is_symlink()
    raw = target.read_bytes()
    assert len(raw) == item['current_bytes'] and sha(raw) == item['current_sha256']
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


def source_before_next_day_profile(path):
    items = {item['path']: item for item in review()['source_changes']}
    if path in items:
        return inverse(items[path])
    target = ROOT / path
    assert not target.is_symlink()
    return target.read_bytes()


def test_review_restores_exact_pre_profile_bytes():
    for item in review()['source_changes']:
        inverse(item)


def test_models_inputs_prior_review_and_frozen_evidence_are_unchanged():
    items = review()['preserved_files']
    assert len(items) > 300
    for item in items:
        target = ROOT / item['path']
        assert not target.is_symlink()
        raw = target.read_bytes()
        assert len(raw) == item['bytes'] and sha(raw) == item['sha256'], item['path']


def test_formal_publisher_changes_only_evidence_issuer_digest():
    path = 'src/top10decision/decision/candidate_profit_publication.py'
    old = source_before_next_day_profile(path).decode()
    new = (ROOT / path).read_text()
    expected = sha((ROOT / 'work/profit_1000_upgrade/candidate_natural_evidence_publication.py').read_bytes())
    assert old.replace('4c73447bd460001724a7b22df60f012016c3b5f8e05f97e0a40967e65a0253de', expected) == new


def test_current_guard_chain_including_settlement_uses_exact_current_bytes():
    from work.profit_1000_upgrade import candidate_natural_evidence_publication as observer
    from work.profit_1000_upgrade import candidate_natural_settlement_workflow as settlement
    from top10decision.decision import candidate_profit_publication as formal
    observer.code_guard()
    formal._dependencies()
    modules = settlement.dependencies()
    assert set(modules) == {'evidence_publication','outcome_collect','daily','journal','journal_git','statistics'}
    modules['statistics']._publication_type()
    settlement.code_guard(modules)


def test_new_p0_profile_changes_only_surface_freeze_and_adds_review():
    from work.profit_1000_upgrade import candidate_natural_p0_continuation as continuation
    old = continuation.DELIVERY_HARDENED_P0_CODE_PROFILE
    new = continuation.NEXT_DAY_STATISTICS_P0_CODE_PROFILE
    assert set(new) == set(old) | {'models/decision_source_surface_review_20261108_next_day_statistics.json'}
    assert {path for path in old if old[path] != new[path]} == {'models/decision_model_freeze.json'}
    for path in {'models/decision_model_freeze.json','models/decision_source_surface_review_20261108_next_day_statistics.json'}:
        raw = (ROOT / path).read_bytes()
        actual = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        assert new[path] == actual
