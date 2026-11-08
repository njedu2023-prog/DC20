"""Old publication evidence remains exact and bounded after the surface rotation."""
from copy import deepcopy

import pytest

from work.profit_1000_upgrade import candidate_natural_publication as publication
from work.profit_1000_upgrade import candidate_natural_evidence_publication as observer


def old_publication_tree():
    return {
        binding['path']: dict(type='blob', mode='100644', sha=binding['git_blob_sha1'])
        for binding in publication.PRE_NEXT_DAY_CODE_BINDINGS
    }


def old_observer_case(day='20261106'):
    capture_sha, verifier_sha = observer.capture.PRE_NEXT_DAY_CAPTURE_CONTRACT
    manifest = dict(signal_date=day, capture_code_sha256=capture_sha,
                    publication_verifier_sha256=verifier_sha)
    tree = {
        path: dict(type='blob', mode='100644', sha=digest)
        for path, digest in observer.PRE_NEXT_DAY_OBSERVER_PROFILE[1].items()
    }
    return manifest, tree


def test_pre_next_day_publication_accepts_complete_original_profile():
    local, _ = publication.code_guard()
    tree = old_publication_tree()
    actual = publication.match_publication_code(tree, tree, local, day='20261106')
    assert actual == sorted(publication.PRE_NEXT_DAY_CODE_BINDINGS, key=lambda b: b['path'])
    assert actual != [
        dict(path=path, sha256=publication.gh.sha256(raw), git_blob_sha1=publication.gh.git_blob(raw))
        for path, raw in sorted(local.items())
    ]


def test_pre_next_day_publication_rejects_new_signal_dates():
    local, _ = publication.code_guard()
    tree = old_publication_tree()
    with pytest.raises(ValueError, match='PRE_NEXT_DAY_PUBLICATION_DATE_OR_SET_CHANGED'):
        publication.match_publication_code(tree, tree, local, day='20261109')


@pytest.mark.parametrize('kind', ['current', 'unknown'])
def test_pre_next_day_publication_rejects_mixed_importer_bytes(kind):
    local, _ = publication.code_guard()
    tree = old_publication_tree()
    changed = deepcopy(tree)
    changed[publication.IMPORTER_PATH]['sha'] = (
        publication.gh.git_blob(local[publication.IMPORTER_PATH]) if kind == 'current' else 'f' * 40
    )
    assert changed[publication.IMPORTER_PATH] != tree[publication.IMPORTER_PATH]
    with pytest.raises(ValueError, match='PRE_NEXT_DAY_PUBLICATION_BYTES_CHANGED'):
        publication.match_publication_code(tree, changed, local, day='20261106')


def test_pre_next_day_observer_accepts_complete_original_profile():
    local, _ = observer.code_guard()
    manifest, tree = old_observer_case()
    assert observer._observer_profile(manifest) == observer.PRE_NEXT_DAY_OBSERVER_PROFILE
    observer.match_observer_code(tree, tree, local, manifest)


def test_pre_next_day_observer_rejects_new_signal_dates():
    manifest, _ = old_observer_case(day='20261109')
    with pytest.raises(ValueError, match='PRE_NEXT_DAY_OBSERVER_DATE_CHANGED'):
        observer._observer_profile(manifest)


@pytest.mark.parametrize('kind', ['current', 'unknown'])
def test_pre_next_day_observer_rejects_mixed_importer_bytes(kind):
    local, _ = observer.code_guard()
    manifest, tree = old_observer_case()
    changed = deepcopy(tree)
    changed[publication.IMPORTER_PATH]['sha'] = (
        publication.gh.git_blob(local[publication.IMPORTER_PATH]) if kind == 'current' else 'f' * 40
    )
    assert changed[publication.IMPORTER_PATH] != tree[publication.IMPORTER_PATH]
    with pytest.raises(ValueError, match='PRE_NEXT_DAY_PUBLICATION_BYTES_CHANGED'):
        observer.match_observer_code(tree, changed, local, manifest)
