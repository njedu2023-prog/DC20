"""Complete synthetic bounded import -> projection -> publication observer -> capsule."""
import csv
import io
from copy import deepcopy
import socket
import pytest
from tests.test_candidate_suspension_boundary import boundary_case
from work.profit_1000_upgrade import test_candidate_natural_p0_github as p0
from work.profit_1000_upgrade import test_candidate_natural_publication as publication
from work.profit_1000_upgrade import test_candidate_natural_evidence as evidence
from work.profit_1000_upgrade.test_candidate_d_source_adapter import csv_bytes, json_bytes, write, update_receipt


def test_complete_boundary_capsule_keeps_closed_sources_and_no_authority(tmp_path, monkeypatch):
    original_fixture = p0.fixture
    def make_boundary(*args, **kwargs):
        case, boundary, disclosure, payload = boundary_case(tmp_path, monkeypatch)
        dates = case['receipt']['inputs']['calendar']['runtime_context_dates']
        phantom = '600999.SH'
        # The witness stock is outside the scored two-stock synthetic pool.
        resume_rows = list(csv.DictReader(io.StringIO((case['root']/boundary['files'][-1]['path']).read_text())))
        restored = next(row for row in resume_rows if row['ts_code'] == case['runtime'][0]['ts_code'])
        for index, binding in enumerate(boundary['files']):
            path = case['root']/binding['path']
            rows = list(csv.DictReader(io.StringIO(path.read_text())))
            if index == 0: rows[0]['ts_code'] = phantom
            if index == 1: rows.append({**restored, 'trade_date':dates[0]})
            if index == 2: rows.append({**restored, 'ts_code':phantom})
            raw = csv_bytes(rows,rows[0]);binding.update(sha256=write(path,raw),git_blob_sha1=p0.m.git_blob(raw))
            for collection in [case['receipt']['inputs']['runtime_consumed_market_files'],case['receipt']['inputs']['committed_history_context']['files']]:
                for item in collection:
                    if item.get('path') == binding['path']:item.update(binding)
        disclosure['used_intervals'][0]['ts_code'] = phantom
        payload['suspensions'][0]['ts_code'] = phantom
        raw = json_bytes(payload);disclosure.update(sha256=write(case['root']/p0.m.adapter.SUSPENSION_PATH,raw),git_blob_sha1=p0.m.git_blob(raw))
        boundary['git_head'] = p0.BASE
        case['receipt']['inputs']['committed_history_context']['git_head'] = p0.BASE
        update_receipt(case)
        return case
    monkeypatch.setattr(p0,'make_case',make_boundary)
    def fixture(*args,**kwargs):
        case = original_fixture(*args,**kwargs)
        base = p0.git_tree(case['sources'])
        case['client'].responses[p0.m.API_PREFIX+'/git/commits/'+p0.BASE] = {'sha':p0.BASE,'tree':{'sha':base['sha']}}
        case['client'].responses[p0.m.API_PREFIX+'/git/trees/'+base['sha']+'?recursive=1'] = base
        return case
    monkeypatch.setattr(p0,'fixture',fixture)
    case = publication.make_case(tmp_path,monkeypatch)
    monkeypatch.setattr(socket,'socket',lambda *a,**k:pytest.fail('NETWORK_FORBIDDEN'))
    case['opener'] = evidence.Opener(case)
    case['destination'] = tmp_path/'boundary-capsule'
    result,raw,manifest,bodies = evidence.materials(case)
    assert len(manifest['original_source_bindings']) == 30
    assert 'boundary_registration' in manifest
    assert result['test_transport_injected'] is True
    assert evidence.m.verify_materials(raw,bodies,expected_manifest_sha256=evidence.m.sha(raw)) == manifest
    # Removing the exact typed receipt cannot retain an expanded inventory.
    bad = deepcopy(manifest);del bad['boundary_registration']
    changed = p0.m.json_bytes(bad)
    with pytest.raises(ValueError,match='ORIGINAL_SOURCE_GIT_BINDINGS_CHANGED'):
        evidence.m.verify_materials(changed,bodies,expected_manifest_sha256=evidence.m.sha(changed))
