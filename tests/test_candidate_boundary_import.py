"""Synthetic read-only boundary import proofs; no remote requests or publication."""
from copy import deepcopy
import pytest
from tests.test_candidate_suspension_boundary import boundary_case
from work.profit_1000_upgrade import candidate_natural_p0_github as gh
from work.profit_1000_upgrade.test_candidate_natural_p0_github import git_tree


def materials(tmp_path, monkeypatch):
    case, boundary, _, _ = boundary_case(tmp_path, monkeypatch)
    receipt, contract, day = case['receipt'], case['contract'], case['day']
    receipt['inputs']['git_head'] = boundary['git_head']
    registered, _, _, _ = gh.adapter._registration(receipt, contract, day)
    sources = {p: (case['root']/p).read_bytes() for p in set(registered) | set(gh.adapter._p0_paths(day).values())}
    return case, boundary, sources


def test_closed_inventory_accepts_only_typed_boundary_files(tmp_path, monkeypatch):
    case, boundary, sources = materials(tmp_path, monkeypatch)
    assert gh.validate_original_source_inventory(case['receipt'], case['contract'], sources, case['day']) == 30
    for changed in [{**sources, 'unregistered.csv': b'x'}, {p:b for p,b in sources.items() if p != boundary['files'][0]['path']}]:
        with pytest.raises(ValueError, match='EXACT_REGISTERED_ORIGINAL_SOURCE_SET_REQUIRED'):
            gh.validate_original_source_inventory(case['receipt'], case['contract'], changed, case['day'])


@pytest.mark.parametrize('mutation', [None, 'base_missing', 'base_changed', 'published_changed', 'head', 'commit_identity'])
def test_witness_membership_requires_original_base_and_published_tree(tmp_path, monkeypatch, mutation):
    case, boundary, sources = materials(tmp_path, monkeypatch)
    head = boundary['git_head']
    published = gh.validate_tree(git_tree(sources), git_tree(sources)['sha'])
    base_sources = dict(sources)
    witness = boundary['files'][0]['path']
    if mutation == 'base_missing': del base_sources[witness]
    if mutation == 'base_changed': base_sources[witness] += b'\n'
    if mutation == 'published_changed': published[witness]['sha'] = '0'*40
    if mutation == 'head': case['receipt']['inputs']['git_head'] = 'c'*40
    base = git_tree(base_sources)
    responses = {f'{gh.API_PREFIX}/git/commits/{head}': {'sha': head, 'tree': {'sha':base['sha']}},
                 f'{gh.API_PREFIX}/git/trees/{base["sha"]}?recursive=1':base}
    if mutation == 'commit_identity': responses[f'{gh.API_PREFIX}/git/commits/{head}']['sha'] = 'c'*40
    class Client:
        def __init__(self): self.calls = []
        def get_json(self, route):
            self.calls.append(route)
            return deepcopy(responses[route])
    client = Client()
    if mutation:
        with pytest.raises(ValueError, match='BOUNDARY_'):
            gh.validate_boundary_base_membership(case['receipt'], published, client)
    else:
        gh.validate_boundary_base_membership(case['receipt'], published, client)
        assert len(client.calls) == 2
    assert all('/git/' in route and 'main' not in route for route in client.calls)


def test_legacy_receipt_needs_no_boundary_network_reads():
    class Client:
        def get_json(self, route): pytest.fail('legacy format must not request additional evidence')
    gh.validate_boundary_base_membership({'inputs':{'runtime_consumed_market_files':[]}}, {}, Client())
