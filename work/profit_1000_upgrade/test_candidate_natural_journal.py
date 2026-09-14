"""Synthetic files created only in a per-test root. No network or qualification."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from work.profit_1000_upgrade import candidate_natural_journal as m


def setup(tmp_path):
    root=tmp_path.resolve()/'state'
    base=root/'20260914'/'20260915'
    blobs={'collection_0/candidate_natural_outcome_sources/receipt.json':b'{"synthetic":"receipt"}',
        'final/candidate_natural_outcomes/day_20260914.json':b'{"synthetic":"native"}',
        'daily.json':b'{"synthetic":"daily"}',
        'collection_0/candidate_natural_outcome_sources/http/example.response.json':b'{"rows":[]}'}
    bindings=[]
    for rel,raw in blobs.items():
        path=base/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        bindings.append({'origin_path':str(path),'sha256':m.sha(raw),'bytes':len(raw)})
    result={'signal_date':'20260914','as_of_date':'20260915','snapshot_file_sha256':'a'*64,
        'publishable':False,'test_only':True,'publishable_file_bindings':bindings}
    for role,binding in zip(('final_collection_receipt','final_outcomes','daily_manifest'),bindings[:3]):
        result[role+'_path']=binding['origin_path'];result[role+'_sha256']=binding['sha256']
    publication={'evidence_commit':'b'*40,'observer_run_id':123,'evidence_manifest_sha256':'c'*64,'snapshot_file_sha256':'a'*64}
    return root,base,result,publication


def build(case):
    root,_,daily,publication=case
    return m.build_journal(daily,publication_binding=publication,test_state_root=root)


def materials(built):
    prefix=m.PREFIX+'20260914/20260915/'
    return built['files'][prefix+'manifest.json'],{p.removeprefix(prefix):raw for p,raw in built['files'].items() if p!=prefix+'manifest.json'}


def test_build_and_cross_process_exact_absolute_restore(tmp_path):
    case=setup(tmp_path);root,base,daily,_=case
    built=build(case);raw,bodies=materials(built)
    assert len(built['files'])==5 and built['manifest']['source_authority_issued'] is False
    original={b['origin_path']:Path(b['origin_path']).read_bytes() for b in daily['publishable_file_bindings']}
    # Recoverable rename of only this test's files simulates a destroyed runner.
    base.rename(base.with_name('saved-original'))
    result=m.restore_journal(raw,bodies,expected_manifest_sha256=built['manifest_sha256'],test_state_root=root)
    assert result['status']=='ORIGINAL_BYTES_RESTORED_STORAGE_ONLY'
    assert all(Path(p).read_bytes()==body for p,body in original.items())
    assert not result['production_activation_allowed']


def test_existing_same_day_never_overwritten(tmp_path):
    case=setup(tmp_path);built=build(case);raw,bodies=materials(built)
    with pytest.raises(ValueError,match='ALREADY_EXISTS'):
        m.restore_journal(raw,bodies,expected_manifest_sha256=built['manifest_sha256'],test_state_root=case[0])


@pytest.mark.parametrize('budget_offset',[-1,0,1])
def test_deduplicated_build_uses_exact_restore_budget(tmp_path,monkeypatch,budget_offset):
    case=setup(tmp_path);root,base,daily,_=case
    body=b'x'*4096
    for binding in daily['publishable_file_bindings']:
        Path(binding['origin_path']).write_bytes(body)
        binding.update(sha256=m.sha(body),bytes=len(body))
    for role in ('final_collection_receipt','final_outcomes','daily_manifest'):
        daily[role+'_sha256']=m.sha(body)
    original=build(case);raw,bodies=materials(original)
    assert len(bodies)==1
    original_bytes=sum(b['bytes'] for b in daily['publishable_file_bindings'])
    budget=original_bytes+len(raw)+budget_offset
    assert original_bytes<=budget and sum(map(len,bodies.values()))+len(raw)<=budget
    monkeypatch.setattr(m,'MAX_BYTES',budget)
    if budget_offset<0:
        with pytest.raises(ValueError,match='EXACT_JOURNAL_BODIES_REQUIRED'):
            build(case)
    else:
        built=build(case)
        assert built==original
        base.rename(base.with_name('saved-original'))
        restored=m.restore_journal(raw,bodies,expected_manifest_sha256=built['manifest_sha256'],test_state_root=root)
        assert restored['status']=='ORIGINAL_BYTES_RESTORED_STORAGE_ONLY'
    assert all(Path(b['origin_path']).read_bytes()==body for b in daily['publishable_file_bindings'])


@pytest.mark.parametrize('kind',['outside','otherday','otherasof','traversal','duplicate','sizebool','sha','missingendpoint','mode','notpublishable'])
def test_invalid_allowlist_rejected_before_read(tmp_path,monkeypatch,kind):
    case=setup(tmp_path);root,base,result,_=case
    b=result['publishable_file_bindings'][0]
    if kind=='outside':b['origin_path']=str(tmp_path/'outside.json')
    elif kind=='otherday':b['origin_path']=b['origin_path'].replace('20260914','20260916')
    elif kind=='otherasof':b['origin_path']=b['origin_path'].replace('20260915','20260916')
    elif kind=='traversal':b['origin_path']=str(base)+'/../x.json'
    elif kind=='duplicate':result['publishable_file_bindings'].append(deepcopy(b))
    elif kind=='sizebool':b['bytes']=True
    elif kind=='sha':b['sha256']='bad'
    elif kind=='missingendpoint':result['final_outcomes_sha256']='f'*64
    elif kind=='mode':result['test_only']=False
    elif kind=='notpublishable':result['publishable']=True
    monkeypatch.setattr(m,'read_bound',lambda *a:pytest.fail('UNVALIDATED_FILE_READ'))
    with pytest.raises(ValueError):build(case)


def test_unknown_files_are_not_scanned_or_read(tmp_path,monkeypatch):
    case=setup(tmp_path)
    (case[1]/'unknown-secret.json').write_bytes(b'MUST_NOT_READ')
    original=m.read_bound
    def read(path,binding):
        assert path.name!='unknown-secret.json'
        return original(path,binding)
    monkeypatch.setattr(m,'read_bound',read)
    assert len(build(case)['files'])==5


@pytest.mark.parametrize('kind',['body','unknown','manifestsha','authority','contentpath','origin','endpoint','duplicate','mode'])
def test_restore_tampering_rejected_before_writes(tmp_path,kind):
    case=setup(tmp_path);root,base,_,_=case
    built=build(case);raw,bodies=materials(built)
    base.rename(base.with_name('saved-original'))
    manifest=json.loads(raw);expected=built['manifest_sha256']
    if kind=='body':bodies[next(iter(bodies))]+=b'changed'
    elif kind=='unknown':bodies['other.json']=b'unknown'
    elif kind=='manifestsha':expected='0'*64
    elif kind=='authority':manifest['source_authority_issued']=True
    elif kind=='contentpath':manifest['files'][0]['content_path']='../escape'
    elif kind=='origin':manifest['files'][0]['origin_path']=str(tmp_path/'escape')
    elif kind=='endpoint':manifest['endpoints']['final_outcomes']['sha256']='0'*64
    elif kind=='duplicate':manifest['files'].append(deepcopy(manifest['files'][0]))
    elif kind=='mode':manifest['test_only']=False
    if kind in {'authority','contentpath','origin','endpoint','duplicate','mode'}:
        raw=m.encoded(manifest);expected=m.sha(raw)
    with pytest.raises((ValueError,KeyError)):
        m.restore_journal(raw,bodies,expected_manifest_sha256=expected,test_state_root=root)
    assert not base.exists()


@pytest.mark.parametrize('kind',['symlink','hardlink','bytes'])
def test_bound_source_not_substituted(tmp_path,kind):
    case=setup(tmp_path);p=Path(case[2]['publishable_file_bindings'][0]['origin_path'])
    if kind=='symlink':
        old=p.with_name('saved-receipt');p.rename(old);p.symlink_to(old)
    elif kind=='hardlink':p.with_name('alias-receipt').hardlink_to(p)
    else:p.write_bytes(b'CHANGED')
    with pytest.raises(ValueError):build(case)


def test_read_changed_during_build_rejected(tmp_path,monkeypatch):
    case=setup(tmp_path);original=m.read_bound;calls=[]
    def read(path,binding):
        raw=original(path,binding);calls.append(path)
        if len(calls)==4:
            Path(case[2]['publishable_file_bindings'][0]['origin_path']).write_bytes(b'changed after initial read')
        return raw
    monkeypatch.setattr(m,'read_bound',read)
    with pytest.raises(ValueError):build(case)


def test_final_metadata_alias_cannot_change_sealed_manifest(tmp_path,monkeypatch):
    case=setup(tmp_path);original=m.read_bound;calls=[]
    def read(path,binding):
        raw=original(path,binding);calls.append(path)
        if len(calls)==8:
            case[3]['evidence_commit']='f'*40
        return raw
    monkeypatch.setattr(m,'read_bound',read)
    with pytest.raises(ValueError,match='METADATA_CHANGED'):
        build(case)


def test_symlink_substitution_before_final_read_rejected(tmp_path,monkeypatch):
    case=setup(tmp_path);original=m.read_bound;calls=[]
    first=Path(case[2]['publishable_file_bindings'][0]['origin_path'])
    def read(path,binding):
        raw=original(path,binding);calls.append(path)
        if len(calls)==4:
            saved=first.with_name('saved-original');first.rename(saved);first.symlink_to(saved)
        return raw
    monkeypatch.setattr(m,'read_bound',read)
    with pytest.raises(ValueError,match='SYMLINK'):
        build(case)


def test_restore_rechecks_earlier_disk_file_after_last_write(tmp_path,monkeypatch):
    case=setup(tmp_path);built=build(case);raw,bodies=materials(built)
    case[1].rename(case[1].with_name('saved-original'))
    original=m.read_bound;calls=[]
    first=Path(built['manifest']['files'][0]['origin_path'])
    def read(path,binding):
        content=original(path,binding);calls.append(path)
        if len(calls)==4:
            first.write_bytes(b'changed after earlier disk verification')
        return content
    monkeypatch.setattr(m,'read_bound',read)
    with pytest.raises(ValueError):
        m.restore_journal(raw,bodies,expected_manifest_sha256=built['manifest_sha256'],test_state_root=case[0])


@pytest.mark.parametrize('kind',['publicationkeys','observerbool','snapshot','previous','unknown','duplicate','nonfinite'])
def test_manifest_metadata_is_symmetric_and_strict(tmp_path,kind):
    case=setup(tmp_path);built=build(case);raw,bodies=materials(built)
    value=json.loads(raw)
    if kind=='publicationkeys':value['publication_binding']['extra']=True
    elif kind=='observerbool':value['publication_binding']['observer_run_id']=True
    elif kind=='snapshot':value['snapshot_file_sha256']='f'*64
    elif kind=='previous':value['previous_manifest_sha256']='bad'
    elif kind=='unknown':value['extra']='bad'
    raw=m.encoded(value)
    if kind=='duplicate':raw=raw[:-2]+b',"test_only": true}\n'
    if kind=='nonfinite':raw=raw[:-2]+b',"extra":NaN}\n'
    with pytest.raises(ValueError):
        m.validate_journal(raw,bodies,expected_manifest_sha256=m.sha(raw),test_state_root=case[0])
