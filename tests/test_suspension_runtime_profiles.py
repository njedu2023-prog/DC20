"""Reviewed pre-repair publications remain readable with their exact old code."""
from copy import deepcopy
import pytest
from work.profit_1000_upgrade import candidate_natural_publication as p
from work.profit_1000_upgrade import candidate_natural_evidence_publication as e


def old_publication_tree():
    return {b['path']: {'type': 'blob', 'mode': '100644', 'sha': b['git_blob_sha1']}
            for b in p.PRE_SUSPENSION_CODE_BINDINGS}


def test_pre_suspension_publication_is_exact_and_date_bounded():
    local, _ = p.code_guard()
    tree = old_publication_tree()
    assert p.match_publication_code(tree, tree, local, day='20261109') == p.PRE_SUSPENSION_CODE_BINDINGS
    with pytest.raises(ValueError, match='PRE_SUSPENSION_PUBLICATION_DATE_OR_SET_CHANGED'):
        p.match_publication_code(tree, tree, local, day='20261110')


@pytest.mark.parametrize('side', ['code', 'published'])
@pytest.mark.parametrize('path', [b['path'] for b in p.PRE_SUSPENSION_CODE_BINDINGS])
def test_old_publication_rejects_each_changed_code_blob(side, path):
    local, _ = p.code_guard()
    code = old_publication_tree(); published = deepcopy(code)
    (code if side == 'code' else published)[path]['sha'] = '0' * 40
    with pytest.raises(ValueError):
        p.match_publication_code(code, published, local, day='20261109')


def test_pre_suspension_observer_is_exact_and_date_bounded():
    local, _ = e.code_guard()
    manifest = {'capture_code_sha256': e.capture.PRE_SUSPENSION_CAPTURE_CONTRACT[0],
                'publication_verifier_sha256': e.capture.PRE_SUSPENSION_CAPTURE_CONTRACT[1],
                'signal_date': '20261109'}
    tree = {path: {'type': 'blob', 'mode': '100644', 'sha': digest}
            for path, digest in e.PRE_SUSPENSION_OBSERVER_PROFILE[1].items()}
    e.match_observer_code(tree, tree, local, manifest)
    for path in tree:
        changed = deepcopy(tree); changed[path]['sha'] = '0' * 40
        with pytest.raises(ValueError):
            e.match_observer_code(tree, changed, local, manifest)
    with pytest.raises(ValueError, match='PRE_SUSPENSION_OBSERVER_DATE_CHANGED'):
        e.match_observer_code(tree, tree, local, {**manifest, 'signal_date': '20261110'})
