"""Exact old daily reader compatibility, with immutable audit predecessors."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import pytest
from work.profit_1000_upgrade import candidate_natural_settlement_workflow as m

ROOT = Path(__file__).resolve().parents[1]
REVIEW = 'models/decision_source_surface_review_20261111_daily_metadata.json'
REVIEW_SHA = 'be4206e61ec8e811a36eafeee2fedf1d47059767607d171fdbc33eed1177dadf'
CHANGED = {'work/profit_1000_upgrade/candidate_natural_settlement_workflow.py',
           'tests/test_reliability_isolation_source_review.py', '.github/workflows/verify_daily_cleanup.yml'}

def sha(b): return hashlib.sha256(b).hexdigest()
def read(path):
    assert isinstance(path,str) and path and not path.startswith('/') and '\\' not in path
    assert all(v not in ('','.','..') for v in path.split('/'))
    p=ROOT/path
    assert not any(v.is_symlink() for v in (p,*p.parents))
    return p.read_bytes()
def review():
    raw=read(REVIEW);assert sha(raw)==REVIEW_SHA
    doc=json.loads(raw)
    assert doc['approved_base_commit']=='b1f53188dc60e71e6915da71c3c99316da5d3669'
    assert doc['schema_version']=='dc20_daily_metadata_read_compatibility_review_v1'
    assert len(doc['source_changes'])==len(CHANGED) and {v['path'] for v in doc['source_changes']}==CHANGED
    assert not any(doc['boundaries'].values())
    return doc

def source_before_daily_metadata(path,raw):
    if path not in CHANGED:return raw
    assert read(path)==raw
    item=next(v for v in review()['source_changes'] if v['path']==path)
    assert sha(raw)==item['current_sha256'] and len(raw)==item['current_bytes']
    lines=raw.decode().splitlines(keepends=True);end=offset=0
    for c in item['inverse_changes']:
        start=c['current_start']-1
        assert end<=start<=len(lines) and c['baseline_start']-1==start+offset
        assert lines[start:start+len(c['current_lines'])]==c['current_lines']
        end=start+len(c['current_lines']);offset+=len(c['baseline_lines'])-len(c['current_lines'])
    for c in reversed(item['inverse_changes']):
        start=c['current_start']-1;lines[start:start+len(c['current_lines'])]=c['baseline_lines']
    old=''.join(lines).encode()
    assert sha(old)==item['baseline_sha256'] and len(old)==item['baseline_bytes']
    return old

def test_all_original_review_and_d10_evidence_preserved():
    for item in review()['source_changes']:source_before_daily_metadata(item['path'],read(item['path']))
    for item in review()['preserved_files']:
        raw=read(item['path']);assert len(raw)==item['bytes'] and sha(raw)==item['sha256'],item['path']

@pytest.mark.parametrize('day',['20261013','20261014','20261021','20261104','20261105','20261106','20261109'])
def test_real_original_journal_metadata_remains_readable(day):
    mods=m.dependencies();paths=sorted((ROOT/m.JOURNAL_PREFIX/day).glob('*/manifest.json'))
    p=paths[-1];manifest=json.loads(p.read_bytes());endpoint=manifest['endpoints']['daily_manifest']
    daily_path=p.parent/'blobs'/(endpoint['sha256']+'.bin')
    raw=daily_path.read_bytes();assert sha(raw)==endpoint['sha256']
    daily=json.loads(raw);frozen=json.loads(read(m.SNAPSHOT_PREFIX+'day_'+day+'.json'))
    assert m.daily_metadata_profile(daily,frozen,mods)
    # Exercise metadata's unchanged full Git/hash/schema/snapshot/status checks.
    rel=p.relative_to(ROOT).as_posix();snapshot=m.SNAPSHOT_PREFIX+'day_'+day+'.json'
    context=m.EVIDENCE_PREFIX+day+'/context.json'
    selected=[snapshot,context,rel,daily_path.relative_to(ROOT).as_posix()]
    gh=mods['evidence_publication'].gh
    tree={path:{'type':'blob','mode':'100644','sha':gh.git_blob(read(path)),'size':len(read(path))} for path in selected}
    reader=m.CheckoutReads(ROOT,tree,gh)
    row={'signal_date':day,'snapshot_path':snapshot,'context_path':context,
         'journals':{manifest['as_of_date']:rel},'latest_journal_asof':manifest['as_of_date']}
    result=m.metadata(row,reader,mods,asof='20261110');reader.guard()
    assert result['manifest_sha256']==sha(p.read_bytes())

@pytest.mark.parametrize('change',['writer','pins','mixed','date','snapshot'])
def test_predecessor_profile_rejects_unreviewed_combinations(change):
    mods=m.dependencies();frozen=json.loads(read(m.SNAPSHOT_PREFIX+'day_20261109.json'))
    writer,pins=m.PRE_RELIABILITY_DAILY_PROFILE
    daily={'daily_module_sha256':writer,'dependencies':deepcopy(pins)}
    assert m.daily_metadata_profile(daily,frozen,mods)
    if change=='writer':daily['daily_module_sha256']='0'*64
    if change=='pins':daily['dependencies']['unexpected']='0'*64
    if change=='mixed':daily['dependencies']=deepcopy(mods['daily'].PINS)
    if change=='date':frozen['signal_date']='20261111'
    if change=='snapshot':frozen['runner_sha256']='0'*64
    assert not m.daily_metadata_profile(daily,frozen,mods)

def test_current_writer_still_allowed_for_old_snapshot():
    mods=m.dependencies();frozen=json.loads(read(m.SNAPSHOT_PREFIX+'day_20261109.json'))
    assert m.daily_metadata_profile({'daily_module_sha256':m.PINS['candidate_natural_daily'],
                                    'dependencies':mods['daily'].PINS},frozen,mods)

@pytest.mark.parametrize('path',sorted(CHANGED|{REVIEW}))
def test_tamper_is_rejected(monkeypatch,path):
    original=Path.read_bytes;target=ROOT/path
    monkeypatch.setattr(Path,'read_bytes',lambda p:original(p)+(b'\n' if p==target else b''))
    with pytest.raises(AssertionError):
        if path==REVIEW:review()
        else:source_before_daily_metadata(path,read(path))
