"""UI-only manifest successor and exact prior publication profile preservation.

No GitHub transport, model fitting, prediction, freeze, or remote write occurs.
"""
from copy import deepcopy
import json
import pytest
from work.profit_1000_upgrade import candidate_natural_p0_continuation as continuation
from work.profit_1000_upgrade import candidate_natural_evidence_publication as observer

publication = observer.publication
gh = observer.gh


def tree(profile):
    return {path: {'type': 'blob', 'mode': '100644', 'sha': value} for path, value in profile.items()}


def test_detail_successor_preserves_all_previous_profiles():
    old = continuation.LATE_RESEARCH_UI_P0_CODE_PROFILE
    new = continuation.LIST_DETAIL_UI_P0_CODE_PROFILE
    assert {p for p in old if old[p] != new[p]} == {continuation.MANIFEST_PATH}
    assert set(old) == set(new)
    assert old[continuation.MANIFEST_PATH] == 'adad1026ee4e995c49162c57036992c20c95fc2d'
    assert new[continuation.MANIFEST_PATH] == gh.git_blob((publication.ROOT / continuation.MANIFEST_PATH).read_bytes())
    assert len(continuation.RELIABILITY_PROFILES) == 10
    for profile in continuation.RELIABILITY_PROFILES:
        entries = tree(profile)
        day = '20261120' if profile in (new, old) else '20261119'
        matched, transition = continuation.validate_code_profiles(gh, None, entries, entries, entries,
            {'signal_date': day})
        assert matched == profile and transition is None
    entries = tree(new)
    with pytest.raises(ValueError, match='DETAIL_UI_PROFILE_ACTIVATION_DATE_REQUIRED'):
        continuation.validate_code_profiles(gh, None, entries, entries, entries, {'signal_date': '20261119'})
    bad = deepcopy(entries)
    bad['models/decision_three_engines/promotion.joblib']['sha'] = '0' * 40
    with pytest.raises(ValueError, match='CODE_PROFILE'):
        continuation.validate_code_profiles(gh, None, entries, entries, bad, {'signal_date': '20261120'})


def prior_tree():
    return tree({b['path']: b['git_blob_sha1'] for b in publication.PRE_DETAIL_CODE_BINDINGS})


def test_pre_detail_publication_complete_profile_and_date_are_required():
    local, _ = publication.code_guard()
    old = prior_tree()
    assert len(old) == len(local) == 24
    assert publication.match_publication_code(old, old, local, day='20261119') == publication.PRE_DETAIL_CODE_BINDINGS
    bad = deepcopy(old)
    bad[publication.IMPORTER_PATH]['sha'] = gh.git_blob(local[publication.IMPORTER_PATH])
    with pytest.raises(ValueError, match='PRE_DETAIL_PUBLICATION_BYTES_CHANGED'):
        publication.match_publication_code(old, bad, local, day='20261119')
    with pytest.raises(ValueError, match='PRE_DETAIL_PUBLICATION_DATE_OR_SET_CHANGED'):
        publication.match_publication_code(old, old, local, day='20261120')
    with pytest.raises(ValueError, match='PRE_DETAIL_PUBLICATION_DATE_OR_SET_CHANGED'):
        publication.match_publication_code(old, old, {p:b for p,b in local.items() if p != publication.COORDINATOR_PATH}, day='20261119')


@pytest.mark.parametrize('day', ['20261119'])
def test_original_d19_observer_materials_remain_verifiable(day):
    root = publication.ROOT / 'work/profit_1000_upgrade/candidate_natural_evidence' / day
    manifest = json.loads((root / 'manifest.json').read_bytes())
    context = json.loads((root / 'context.json').read_bytes())
    observation = json.loads((root / manifest['native_observation']['path']).read_bytes())
    assert (manifest['capture_code_sha256'], manifest['publication_verifier_sha256']) == observer.capture.PRE_DETAIL_CAPTURE_CONTRACT
    manifest_raw = (root / 'manifest.json').read_bytes()
    bodies = {item['path']: (root / item['path']).read_bytes() for item in manifest['files']}
    assert observer.capture.verify_materials(manifest_raw, bodies,
        expected_manifest_sha256=observer.sha(manifest_raw)) == manifest
    run = {'id': context['observer_run_id'], 'head_sha': context['code_head_sha']}
    observer._context(context, run, manifest, observation)
    local, _ = observer.code_guard()
    entries = tree(observer.PRE_DETAIL_OBSERVER_PROFILE[1])
    observer.match_observer_code(entries, entries, local, manifest)
    mixed = deepcopy(entries)
    mixed[observer.CAPTURE_PATH]['sha'] = gh.git_blob(local[observer.CAPTURE_PATH])
    with pytest.raises(ValueError, match='REVIEWED_OBSERVER_CODE_VERSION_CHANGED'):
        observer.match_observer_code(entries, mixed, local, manifest)
    with pytest.raises(ValueError, match='PRE_DETAIL_OBSERVER_DATE_CHANGED'):
        observer._observer_profile({**manifest, 'signal_date': '20261120'})
    wrong_writer = {**context, 'writer_sha256': observer.PRE_RELIABILITY_OBSERVER_WRITER_SHA}
    with pytest.raises(ValueError, match='EXACT_OBSERVER_CONTEXT_BINDING_REQUIRED'):
        observer._context(wrong_writer, run, manifest, observation)


def test_prior_detail_continuation_pair_remains_readable():
    proof = {'schema_version': continuation.SCHEMA, 'signal_date': '20261119',
        'original_generation_run': {'id': 1, 'conclusion': 'failure'},
        'successful_reuse_run': {'id': 2, 'conclusion': 'success'},
        'source_generation_and_republication_are_distinct': True}
    imported = {'schema_version': continuation.IMPORT_SCHEMA, 'continuation': proof,
        'signal_date': '20261119', 'run': proof['original_generation_run'],
        **deepcopy(continuation.PRE_DETAIL_IMPORT_PROFILE),
        'continuation_dependency_sha256': deepcopy(continuation.PRE_DETAIL_CONTINUATION_PINS)}
    assert continuation.validate_import_receipt(gh, imported) == proof
    mixed = {**imported, 'continuation_dependency_sha256': gh.CONTINUATION_PINS}
    with pytest.raises(ValueError, match='CONTINUATION_IMPORT_PROOF_OR_CURRENT_CODE_MISMATCH'):
        continuation.validate_import_receipt(gh, mixed)
    later = {**imported, 'signal_date': '20261120', 'continuation': {**proof, 'signal_date': '20261120'}}
    with pytest.raises(ValueError, match='PRE_DETAIL_CONTINUATION_IMPORT_DATE_CHANGED'):
        continuation.validate_import_receipt(gh, later)
