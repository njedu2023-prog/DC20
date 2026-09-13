"""Synthetic coordinator contracts; no real publication/source qualification."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from work.profit_1000_upgrade import candidate_natural_observer as m
from work.profit_1000_upgrade import candidate_natural_evidence_git as writer

NOW = datetime(2026,9,14,12,tzinfo=timezone.utc)


def setup(tmp_path, monkeypatch):
    # Explicit stubs exercise coordination only, never the source verifier.
    root = tmp_path.resolve()/'capsule'
    (root/'bodies').mkdir(parents=True)
    raw = writer.encoded({'exec_date':'20260915','research_prospective_publication_observed':True})
    binding = {'path':'bodies/'+m.digest(raw)+'.bin','sha256':m.digest(raw),'bytes':len(raw)}
    (root/binding['path']).write_bytes(raw)
    manifest = {'freeze_run_id':'123','native_observation':binding}
    manifest_raw = writer.encoded(manifest)
    (root/'manifest.json').write_bytes(manifest_raw)
    result = {'status':'LOCAL_UNPUBLISHED_OBSERVATION','test_transport_injected':False,
        'signal_date':'20260914','snapshot_file_sha256':'a'*64,'publication_observation_sha256':m.digest(raw),
        'manifest_path':'manifest.json','manifest_sha256':m.digest(manifest_raw),'output_root':str(root),
        'files':[binding,{'path':'manifest.json','sha256':m.digest(manifest_raw),'bytes':len(manifest_raw)}]}
    verified = {**deepcopy(result),'manifest':manifest}
    capture = SimpleNamespace(verify_local_evidence=lambda *a,**kw:deepcopy(verified), MAX_FILE_BYTES=8*1024**2,
        gh=SimpleNamespace(read=lambda p,*a:(p.read_bytes(),None),parse_json=json.loads))
    monkeypatch.setattr(m,'dependencies',lambda:(capture,writer))
    context = {'repository':writer.REPO,'branch':'main','observer_run_id':456,'run_attempt':1,
        'code_head_sha':'b'*40,'observer_workflow_path':m.WORKFLOW_PATH}
    return result,context,verified


def test_preparation_keeps_exact_capsule_and_adds_only_context(tmp_path,monkeypatch):
    captured, context, _ = setup(tmp_path,monkeypatch)
    files,deadline = m.prepare_evidence_files(captured,context,now=NOW)
    prefix = writer.PREFIX+'20260914/'
    bound = json.loads(files[prefix+'context.json'])
    assert len(files) == 3 and bound['schema_version'] == m.CONTEXT_SCHEMA
    assert bound['freeze_run_id'] == 123 and bound['observer_run_id'] == 456
    assert bound['evidence_natural_admission_issued'] is bound['production_activation_allowed'] is False
    assert deadline == datetime(2026,9,15,1,20,tzinfo=timezone.utc)
    assert not (Path(captured['output_root'])/'context.json').exists()
    for b in captured['files']:
        assert files[prefix+b['path']] == (Path(captured['output_root'])/b['path']).read_bytes()


@pytest.mark.parametrize('field,value',[('status','LOCAL_UNPUBLISHED_OBSERVATION_TEST_ONLY'),
    ('test_transport_injected',True),('test_transport_injected',0),('manifest_sha256','f'*64),
    ('signal_date','20260915'),('snapshot_file_sha256','f'*64)])
def test_capture_mismatch_or_test_only_rejected(tmp_path,monkeypatch,field,value):
    captured,context,_ = setup(tmp_path,monkeypatch)
    captured[field] = value
    with pytest.raises(ValueError):
        m.prepare_evidence_files(captured,context,now=NOW)


@pytest.mark.parametrize('field,value',[('repository','another/repo'),('branch','other'),('observer_run_id',True),
    ('observer_run_id',0),('run_attempt',True),('run_attempt',2),('code_head_sha','main'),('observer_workflow_path','other.yml')])
def test_context_exact_identity_required(tmp_path,monkeypatch,field,value):
    captured,context,_ = setup(tmp_path,monkeypatch)
    context[field] = value
    with pytest.raises(ValueError):
        m.prepare_evidence_files(captured,context,now=NOW)


def test_body_changed_is_rejected(tmp_path,monkeypatch):
    captured,context,_ = setup(tmp_path,monkeypatch)
    (Path(captured['output_root'])/'manifest.json').write_bytes(b'changed')
    with pytest.raises(ValueError,match='FILE_CHANGED'):
        m.prepare_evidence_files(captured,context,now=NOW)


def test_time_window_rejected(tmp_path,monkeypatch):
    captured,context,_ = setup(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='DEADLINE'):
        m.prepare_evidence_files(captured,context,now=datetime(2026,9,15,1,20,tzinfo=timezone.utc))


def test_exact_code_dependencies_and_original_verifier_pins():
    capture,actual_writer = m.dependencies()
    assert actual_writer is writer
    assert m.digest(Path(capture.__file__).read_bytes()) == m.CAPTURE_SHA
    assert m.digest(Path(writer.__file__).read_bytes()) == m.WRITER_SHA


def test_workflow_fixed_main_first_attempt_no_quote_secret_or_formal_changes():
    import yaml
    doc = yaml.load((m.ROOT/m.WORKFLOW_PATH).read_text(),Loader=yaml.BaseLoader)
    assert doc['name'] == 'DC20 · Preserve natural candidate publication (research)'
    assert doc['on']['workflow_run']['workflows'] == ['DC20 · Fixed candidate natural forward (research)']
    assert doc['on']['workflow_dispatch']['inputs']['dry_run']['default'] == 'true'
    assert doc['permissions'] == {'contents':'read'} and doc['concurrency']['cancel-in-progress'] == 'false'
    assert doc['jobs']['observe']['permissions'] == {'actions':'read','contents':'write'}
    assert doc['jobs']['validate']['name'] == 'Validate publication evidence preservation'
    assert doc['jobs']['observe']['name'] == 'Verify and preserve original publication evidence'
    assert '357010624' in doc['jobs']['validate']['if'] and "event != 'push'" in doc['jobs']['validate']['if']
    for job in doc['jobs'].values():
        for step in job['steps']:
            if 'uses' in step:
                assert m.re.fullmatch('actions/[a-z-]+@[0-9a-f]{40}',step['uses'])
                if step['uses'].startswith('actions/checkout@'):
                    assert step['with'] == {'ref':'${{ github.sha }}','persist-credentials':'false'}
            assert 'TUSHARE_TOKEN' not in step.get('env',{})
            assert not any(x in step.get('run','') for x in ('git push','ssh ','${{ inputs.','${{ github.event.'))
    active = next(x for x in doc['jobs']['observe']['steps'] if 'candidate_natural_observer' in x.get('run',''))
    assert 'FREEZE_RUN_ID' in active['env'] and 'DC20_CANDIDATE_GITHUB_TOKEN' in active['env']


@pytest.mark.parametrize('key,value',[('GITHUB_ACTIONS','false'),('GITHUB_REF','refs/heads/other'),
    ('GITHUB_RUN_ATTEMPT','2'),('GITHUB_SHA','main'),('GITHUB_RUN_ID','bad')])
def test_cli_context_rejected(key,value,monkeypatch):
    for k,v in {'GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':writer.REPO,'GITHUB_REF':'refs/heads/main',
        'GITHUB_RUN_ATTEMPT':'1','GITHUB_SHA':'a'*40,'GITHUB_RUN_ID':'123'}.items():
        monkeypatch.setenv(k,v)
    monkeypatch.setenv(key,value)
    with pytest.raises(ValueError):
        m.execution_context()
