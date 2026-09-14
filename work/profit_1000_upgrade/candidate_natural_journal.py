"""Immutable, content-addressed transport of bounded research day state.

This storage layer proves bytes/paths only, never publication/source/fill
authority. It restores original absolute roots, never rewrites a source receipt.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

PREFIX = 'work/profit_1000_upgrade/candidate_natural_journal/'
STATE_ROOT = Path('/tmp/dc20-candidate-natural-state')
SCHEMA = 'dc20_candidate_natural_day_journal_v1'
MAX_FILES, MAX_BYTES, MAX_FILE = 1024, 128 * 1024**2, 8 * 1024**2


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()


def parsed(raw):
    def unique(items):
        value = {}
        for key,item in items:
            require(key not in value,'DUPLICATE_JOURNAL_KEY')
            value[key] = item
        return value
    return json.loads(raw,object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('NONFINITE_JOURNAL_JSON')))


def publication_reference(value, snapshot_sha):
    require(type(value) is dict and set(value) == {'evidence_commit','observer_run_id',
        'evidence_manifest_sha256','snapshot_file_sha256'} and type(value['evidence_commit']) is str
        and re.fullmatch('[0-9a-f]{40}',value['evidence_commit'])
        and type(value['observer_run_id']) is int and value['observer_run_id'] > 0,
        'EXACT_PUBLICATION_REFERENCE_REQUIRED')
    sha256(value['evidence_manifest_sha256']);sha256(value['snapshot_file_sha256']);sha256(snapshot_sha)
    require(value['snapshot_file_sha256'] == snapshot_sha,'SNAPSHOT_REFERENCE_CHANGED')


def date(value):
    from datetime import datetime
    require(type(value) is str and re.fullmatch('[0-9]{8}',value), 'EXACT_D_DATE_REQUIRED')
    datetime.strptime(value,'%Y%m%d')
    return value


def sha256(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}',value), 'EXTERNAL_SHA_REQUIRED')
    return value


def root_path(test_state_root=None):
    require(test_state_root is not None or sys.platform == 'linux', 'NATURAL_JOURNAL_REQUIRES_LINUX')
    root = STATE_ROOT if test_state_root is None else Path(test_state_root)
    require(root.is_absolute() and str(root) == str(root.absolute()) and '..' not in root.parts,
        'EXACT_ABSOLUTE_STATE_ROOT_REQUIRED')
    require(not any(p.is_symlink() for p in (root,*root.parents)), 'STATE_ROOT_SYMLINK_REJECTED')
    return root


def binding_path(binding, root, day, asof):
    require(type(binding) is dict and set(binding) == {'origin_path','sha256','bytes'}, 'EXACT_FILE_BINDING_REQUIRED')
    sha256(binding['sha256'])
    require(type(binding['bytes']) is int and 0 < binding['bytes'] <= MAX_FILE, 'BOUNDED_FILE_SIZE_REQUIRED')
    p = Path(binding['origin_path'])
    require(type(binding['origin_path']) is str and p.is_absolute() and str(p) == binding['origin_path']
        and '..' not in p.parts and '\\' not in str(p), 'EXACT_ORIGINAL_PATH_REQUIRED')
    base = root/day/asof
    require(base in p.parents and re.fullmatch(r'[A-Za-z0-9_./-]+',str(p)), 'ONLY_THIS_DAY_STATE_ALLOWED')
    require(not any(x.is_symlink() for x in (p,*p.parents)), 'BOUND_FILE_SYMLINK_REJECTED')
    return p


def read_bound(path, binding):
    require(not any(p.is_symlink() for p in (path,*path.parents)), 'BOUND_READ_SYMLINK_REJECTED')
    def identity(s):
        return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_nlink,s.st_mode)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == binding['bytes'], 'REGULAR_ORIGINAL_FILE_REQUIRED')
    require(hasattr(os,'O_NOFOLLOW'),'NOFOLLOW_READ_REQUIRED')
    fd = os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as handle:
        require(identity(os.fstat(handle.fileno())) == identity(before),'BOUND_READ_FILE_CHANGED')
        raw = handle.read(MAX_FILE+1)
        require(identity(os.fstat(handle.fileno())) == identity(before),'BOUND_READ_FILE_CHANGED')
    require(not any(p.is_symlink() for p in (path,*path.parents)) and identity(path.lstat()) == identity(before)
        and sha(raw) == binding['sha256'] and len(raw) == binding['bytes'], 'ORIGINAL_STATE_BYTES_CHANGED')
    return raw


def build_journal(daily_result, *, publication_binding, previous_manifest_sha256=None, test_state_root=None):
    """Copy only the final daily coordinator's explicit publication allowlist."""
    require(type(daily_result) is dict, 'DAILY_RESULT_REQUIRED')
    input_daily,input_publication = daily_result,publication_binding
    daily_seal,publication_seal = encoded(input_daily),encoded(input_publication)
    daily_result,publication_binding = parsed(daily_seal),parsed(publication_seal)
    day,asof = date(daily_result['signal_date']),date(daily_result['as_of_date'])
    require('20260914' <= day <= asof, 'NATURAL_D_ASOF_REQUIRED')
    if previous_manifest_sha256 is not None:
        sha256(previous_manifest_sha256)
    root = root_path(test_state_root)
    require(daily_result.get('publishable') is (test_state_root is None) and daily_result.get('test_only') is (test_state_root is not None),
        'ONLY_COMPLETE_MATCHING_MODE_DAILY_RESULT_REQUIRED')
    publication_reference(publication_binding,daily_result['snapshot_file_sha256'])
    bindings = daily_result['publishable_file_bindings']
    require(type(bindings) is list and 3 <= len(bindings) <= MAX_FILES, 'BOUNDED_EXACT_DAILY_ALLOWLIST_REQUIRED')
    paths = [binding_path(b,root,day,asof) for b in bindings]
    require(len(set(paths)) == len(paths) and sum(b['bytes'] for b in bindings) <= MAX_BYTES, 'DUPLICATE_OR_OVERSIZE_STATE')
    index = {b['origin_path']:b for b in bindings}
    endpoints = {}
    for role in ('final_collection_receipt','final_outcomes','daily_manifest'):
        path,digest = daily_result[role+'_path'],sha256(daily_result[role+'_sha256'])
        require(path in index and index[path]['sha256'] == digest, 'FINAL_ENDPOINT_NOT_IN_ALLOWLIST')
        endpoints[role] = index[path]
    require(Path(endpoints['final_collection_receipt']['origin_path']).name == 'receipt.json'
        and Path(endpoints['final_outcomes']['origin_path']).name == 'day_'+day+'.json', 'FINAL_NATIVE_BASENAMES_REQUIRED')
    bodies = {}
    rows = []
    for path,binding in zip(paths,bindings):
        raw = read_bound(path,binding)
        relative = 'blobs/'+sha(raw)+'.bin'
        bodies[relative] = raw
        rows.append({**binding,'content_path':relative})
    manifest = {'schema_version':SCHEMA,'signal_date':day,'as_of_date':asof,
        'snapshot_file_sha256':daily_result['snapshot_file_sha256'],'publication_binding':publication_binding,
        'previous_manifest_sha256':previous_manifest_sha256,'state_root':str(root),
        'files':sorted(rows,key=lambda b:b['origin_path']), 'endpoints':endpoints,
        'test_only':test_state_root is not None,'storage_integrity_only':True,'natural_forward_admission_issued':False,
        'source_authority_issued':False,'actual_execution_claimed':False,'production_activation_allowed':False}
    raw_manifest = encoded(manifest)
    require(len(raw_manifest) <= MAX_FILE and sum(map(len,bodies.values()))+len(raw_manifest)<=MAX_BYTES, 'COMPLETE_JOURNAL_BUDGET_EXCEEDED')
    # Re-read the entire explicit list before handing immutable bytes to Git.
    for path,binding in zip(paths,bindings):
        require(read_bound(path,binding) == bodies['blobs/'+binding['sha256']+'.bin'], 'SOURCE_CHANGED_DURING_JOURNAL')
    require(encoded(input_daily) == daily_seal and encoded(input_publication) == publication_seal
        and encoded(manifest) == raw_manifest, 'JOURNAL_INPUT_METADATA_CHANGED')
    # Deduplication must not hide the original-byte budget required by restore.
    validate_journal(raw_manifest,bodies,expected_manifest_sha256=sha(raw_manifest),
        test_state_root=test_state_root)
    prefix = PREFIX+day+'/'+asof+'/'
    return {'manifest':manifest,'manifest_sha256':sha(raw_manifest),'files':{prefix+'manifest.json':raw_manifest,
        **{prefix+p:b for p,b in bodies.items()}},'storage_integrity_only':True,'production_activation_allowed':False}


def validate_journal(manifest_raw, bodies, *, expected_manifest_sha256, test_state_root=None):
    require(type(manifest_raw) is bytes and len(manifest_raw)<=MAX_FILE and sha(manifest_raw)==sha256(expected_manifest_sha256),
        'EXTERNAL_MANIFEST_SHA_MISMATCH')
    value = parsed(manifest_raw)
    require(type(value) is dict and set(value) == {'schema_version','signal_date','as_of_date',
        'snapshot_file_sha256','publication_binding','previous_manifest_sha256','state_root','files','endpoints',
        'test_only','storage_integrity_only','natural_forward_admission_issued','source_authority_issued',
        'actual_execution_claimed','production_activation_allowed'}
        and value.get('schema_version')==SCHEMA and value.get('test_only') is (test_state_root is not None), 'JOURNAL_SCHEMA_OR_MODE_CHANGED')
    publication_reference(value['publication_binding'],value['snapshot_file_sha256'])
    if value['previous_manifest_sha256'] is not None:
        sha256(value['previous_manifest_sha256'])
    root = root_path(test_state_root)
    require(value['state_root']==str(root),'ORIGINAL_STATE_ROOT_CHANGED')
    day,asof = date(value['signal_date']),date(value['as_of_date'])
    require('20260914' <= day <= asof,'JOURNAL_DATE_CHANGED')
    for k,v in {'storage_integrity_only':True,'natural_forward_admission_issued':False,'source_authority_issued':False,
        'actual_execution_claimed':False,'production_activation_allowed':False}.items():
        require(value.get(k) is v,'STORAGE_CANNOT_ISSUE_AUTHORITY')
    rows=value['files']; require(type(rows) is list and 3<=len(rows)<=MAX_FILES and type(bodies) is dict,'BOUNDED_JOURNAL_REQUIRED')
    originals,wanted,total=set(),set(),len(manifest_raw)
    for row in rows:
        require(type(row) is dict and set(row)=={'origin_path','sha256','bytes','content_path'},'EXACT_JOURNAL_ROW_REQUIRED')
        b={k:row[k] for k in ('origin_path','sha256','bytes')}
        p=binding_path(b,root,day,asof)
        require(str(p) not in originals and row['content_path']=='blobs/'+row['sha256']+'.bin','DUPLICATE_OR_CHANGED_CONTENT_PATH')
        originals.add(str(p)); wanted.add(row['content_path']); total+=row['bytes']
        raw=bodies[row['content_path']]
        require(type(raw) is bytes and len(raw)==row['bytes'] and sha(raw)==row['sha256'],'JOURNAL_BODY_CHANGED')
    require(set(bodies)==wanted and total<=MAX_BYTES,'EXACT_JOURNAL_BODIES_REQUIRED')
    require(set(value['endpoints'])=={'final_collection_receipt','final_outcomes','daily_manifest'},'EXACT_ENDPOINTS_REQUIRED')
    rows_by_path={r['origin_path']:{k:r[k] for k in ('origin_path','sha256','bytes')} for r in rows}
    require(all(rows_by_path.get(b['origin_path'])==b for b in value['endpoints'].values()),'ENDPOINT_BINDING_CHANGED')
    require(Path(value['endpoints']['final_collection_receipt']['origin_path']).name == 'receipt.json'
        and Path(value['endpoints']['final_outcomes']['origin_path']).name == 'day_'+day+'.json','FINAL_NATIVE_BASENAMES_REQUIRED')
    return value


def restore_journal(manifest_raw,bodies,*,expected_manifest_sha256,test_state_root=None):
    value=validate_journal(manifest_raw,bodies,expected_manifest_sha256=expected_manifest_sha256,test_state_root=test_state_root)
    body_seal=tuple(sorted(bodies.items()))
    base=root_path(test_state_root)/value['signal_date']/value['as_of_date']
    require(not base.exists(),'STATE_ALREADY_EXISTS_KEEP_ORIGINAL')
    base.mkdir(parents=True,exist_ok=False)
    for row in value['files']:
        binding={k:row[k] for k in ('origin_path','sha256','bytes')}
        path=binding_path(binding,root_path(test_state_root),value['signal_date'],value['as_of_date'])
        path.parent.mkdir(parents=True,exist_ok=True)
        binding_path(binding,root_path(test_state_root),value['signal_date'],value['as_of_date'])
        with path.open('xb') as handle:
            handle.write(bodies[row['content_path']])
        read_bound(path,binding)
    for row in value['files']:
        binding={k:row[k] for k in ('origin_path','sha256','bytes')}
        path=binding_path(binding,root_path(test_state_root),value['signal_date'],value['as_of_date'])
        require(read_bound(path,binding) == bodies[row['content_path']],'RESTORED_FILE_CHANGED')
    require(tuple(sorted(bodies.items())) == body_seal,'RESTORE_INPUT_BODIES_CHANGED')
    require(validate_journal(manifest_raw,bodies,expected_manifest_sha256=expected_manifest_sha256,test_state_root=test_state_root)==value,
        'JOURNAL_CHANGED_DURING_RESTORE')
    return {'status':'ORIGINAL_BYTES_RESTORED_STORAGE_ONLY','signal_date':value['signal_date'],
        'as_of_date':value['as_of_date'],'endpoints':value['endpoints'],'storage_integrity_only':True,'production_activation_allowed':False}
