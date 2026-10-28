"""Portable synthetic dual-run API fixture; never live forward evidence."""
from copy import deepcopy
from datetime import datetime, timezone
import base64
import hashlib
import io
import json
import zipfile
import zlib
import pytest
from work.profit_1000_upgrade import candidate_natural_p0_github as gh
from work.profit_1000_upgrade import candidate_natural_p0_continuation as m
from work.profit_1000_upgrade.test_candidate_natural_p0_github import fixture as source_fixture, git_tree, pages_zip

R0, R1 = '43212345678', '43212345679'
BASE, CAS, HEAD = 'c'*40, 'b'*40, 'd'*40
PRED, MARKET = 'e'*40, 'a'*40
NOW = datetime(2026,9,14,12,tzinfo=timezone.utc)


def literal(raw):
    compressed=zlib.compress(raw); lines=[f'literal {len(raw)}'.encode()]
    for pos in range(0,len(compressed),52):
        part=compressed[pos:pos+52]; n=len(part)
        lines.append(bytes([n+64 if n<=26 else n+70])+base64.b85encode(part+b'\0'*((-n)%4)))
    return b'\n'.join(lines)+b'\n\n'


class Client:
    def __init__(self,responses,archives): self.responses,self.archives,self.requests=responses,archives,[]
    def get_json(self,path): self.requests.append(path); return deepcopy(self.responses[path])
    def get_artifact_zip(self,aid): self.requests.append(('archive',aid)); return self.archives[aid]


def fixture(tmp_path,monkeypatch,*,suspension=False):
    c=source_fixture(tmp_path,monkeypatch,suspension=suspension)
    day=c['day']; dated=f'{day[:4]}-{day[4:6]}-{day[6:]}'
    receipt=c['receipt']; meta=c['meta']; sources=dict(c['sources'])
    meta.update(source_repo={'owner':'njedu2023-prog','repo':'a-share-top3-data','resolved_commit':MARKET},
        strict_dated_source=True,requested_trade_date=day,resolved_trade_date=day,
        upstream_meta={'requested_trade_date':day,'resolved_trade_date':day,'generated_at_bj':dated+' 18:00:00'})
    sources[receipt['inputs']['market']['meta_path']]=gh.json_bytes(meta)
    receipt['inputs']['market'].update(source_repository='njedu2023-prog/a-share-top3-data',
        meta_sha256=gh.sha256(gh.json_bytes(meta)))
    pred_path=f'data/pred/archive/pred_source_{day}.csv'
    pred=f'trade_date,verify_date,generated_at_utc\n{day},{c["contract"]["exec_date"]},{dated}T10:00:00Z\n'.encode()
    pred_meta={'resolved_commit':PRED,'source_repository':'njedu2023-prog/a-top10','resolved_trade_date':day,
        'source_ref':f'https://raw.githubusercontent.com/njedu2023-prog/a-top10/{PRED}/outputs/decisio/pred_decisio_{day}.csv',
        'sha256':gh.sha256(pred),'body_sha256':gh.sha256(pred)}
    receipt['inputs']['promotion_model']=dict(m.ORIGINAL_PROMOTION_MODEL)
    receipt['inputs']['committed_static_inputs']={'git_head':BASE,'runtime_code':deepcopy(m.ORIGINAL_RUNTIME_CODE_BINDINGS)}
    receipt['inputs']['candidate']={'path':pred_path,'meta_path':'data/pred/_pred_source_meta.json',
        'source_repository':'njedu2023-prog/a-top10','resolved_commit':PRED,'row_count':1,
        'sha256':gh.sha256(pred),'meta_sha256':gh.sha256(gh.json_bytes(pred_meta))}
    sources[gh.adapter._p0_paths(day)['receipt']]=gh.json_bytes(receipt)
    # The dual-run fixture describes the reviewed historical code profile.
    # Keep its original freeze document even when the live freeze gains metadata.
    freeze_path = 'models/decision_model_freeze.json'
    historical_freeze = (gh.CODE_ROOT / 'tests/fixtures/candidate_continuation/decision_model_freeze.json').read_bytes()
    assert gh.git_blob(historical_freeze) == m.ORIGINAL_P0_CODE_PROFILE[freeze_path]
    profile_files = {p:(gh.CODE_ROOT/p).read_bytes() for p in m.ORIGINAL_P0_CODE_PROFILE}
    profile_files[freeze_path] = historical_freeze
    # This is the unchanged, hash-checked historical producer, not today's code.
    producer_path = 'scripts/publish_primary_three_rank.py'
    historical_producer = (gh.CODE_ROOT / 'tests/fixtures/candidate_continuation/publish_primary_three_rank.py.txt').read_bytes()
    assert gh.git_blob(historical_producer) == m.ORIGINAL_P0_CODE_PROFILE[producer_path]
    profile_files[producer_path] = historical_producer
    all_new={**sources,pred_path:pred,'data/pred/_pred_source_meta.json':gh.json_bytes(pred_meta),
        **profile_files}
    changed=set(gh.adapter._p0_paths(day).values())|{receipt['inputs']['market']['meta_path'],
        receipt['inputs']['market']['tables']['daily']['path'],pred_path,'data/pred/_pred_source_meta.json'}
    old={p:b for p,b in all_new.items() if p not in changed}
    patch=b''
    for path in sorted(changed):
        raw=all_new[path]
        patch+=f'diff --git a/{path} b/{path}\nnew file mode 100644\nindex {"0"*40}..{gh.git_blob(raw)}\n'.encode()
        patch+=b'GIT binary patch\n'+literal(raw)+literal(b'')
    members={'base_sha.txt':(BASE+'\n').encode(),'primary-d.patch':patch,
        'primary-d.patch.sha256':(gh.sha256(patch)+'\n').encode(),
        'primary-d-target-envelope.json':gh.json_bytes({'signal_date':day,'generation_mode':'NATURAL',
            'pred_commit':PRED,'market_commit':MARKET,'dispatch_created_at':dated+'T11:00:00Z'})}
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
        for p,b in members.items(): z.writestr(p,b)
    candidate_zip=buf.getvalue()
    r0=deepcopy(c['run']);r0.update(head_sha=BASE,conclusion='failure',event='workflow_dispatch',
        name=f'DC20 controlled daily NATURAL | D={day}',display_title=f'DC20 controlled daily NATURAL | D={day}')
    jobs0=deepcopy(c['jobs']);jobs0[2]['conclusion']='failure'
    steps=[{'number':i+1,'name':name,'status':'completed','conclusion':'success'}
        for i,name in enumerate(('Resolve exact D and reject a completed duplicate',*m.SKIPPED_STEPS))]
    jobs0[0]['steps']=deepcopy(steps)
    r1=deepcopy(c['run']);r1.update(id=int(R1),head_sha=HEAD,created_at=dated+'T11:20:00Z',
        run_started_at=dated+'T11:20:01Z',updated_at=dated+'T11:30:00Z')
    jobs1=deepcopy(c['jobs'])
    for i,j in enumerate(jobs1):
        j.update(id=201+i,run_id=int(R1),started_at=dated+f'T11:2{1+i*2}:00Z',completed_at=dated+f'T11:2{2+i*2}:00Z')
    jobs1[1]['conclusion']='skipped';jobs1[0]['steps']=deepcopy(steps)
    for step in jobs1[0]['steps'][1:]: step['conclusion']='skipped'
    revision=deepcopy(c['revision']);revision.update(head_sha=HEAD,run_id=int(R1))
    page_zip=pages_zip(revision)
    art0={'id':67890,'name':f'primary-d-candidate-{R0}-1','size_in_bytes':len(candidate_zip),
        'digest':'sha256:'+gh.sha256(candidate_zip),'expired':False,'created_at':dated+'T11:02:00Z',
        'updated_at':dated+'T11:02:00Z','workflow_run':{'id':int(R0),'head_branch':'main','head_sha':BASE}}
    art1=deepcopy(c['artifact']);art1.update(size_in_bytes=len(page_zip),digest='sha256:'+gh.sha256(page_zip),
        created_at=dated+'T11:25:00Z',updated_at=dated+'T11:25:01Z',workflow_run={'id':int(R1),'head_branch':'main','head_sha':HEAD})
    responses={};trees={BASE:git_tree(old),CAS:git_tree(all_new),HEAD:git_tree(all_new)}
    for rid,run,jobs,artifact in ((R0,r0,jobs0,art0),(R1,r1,jobs1,art1)):
        path=gh.API_PREFIX+'/actions/runs/'+rid
        responses.update({path:run,path+'/attempts/1/jobs?per_page=100':{'total_count':3,'jobs':jobs},
            path+'/artifacts?per_page=100':{'total_count':1,'artifacts':[artifact]}})
    for sha,tree in trees.items():
        responses[gh.API_PREFIX+'/git/commits/'+sha]={'sha':sha,'tree':{'sha':tree['sha']},
            'parents':[] if sha==BASE else [{'sha':BASE if sha==CAS else CAS}],
            'committer':{'date':dated+'T11:03:30Z'}}
        responses[gh.API_PREFIX+'/git/trees/'+tree['sha']+'?recursive=1']=tree
    for b in {**old,**all_new}.values():
        sha=gh.git_blob(b);responses[gh.API_PREFIX+'/git/blobs/'+sha]={'sha':sha,'size':len(b),
            'encoding':'base64','content':base64.b64encode(b).decode()}
    for repository,sha in (('a-top10',PRED),('a-share-top3-data',MARKET)):
        responses[f'/repos/njedu2023-prog/{repository}/git/commits/{sha}']={'sha':sha,'committer':{'date':dated+'T10:01:00Z'}}
    responses[gh.API_PREFIX+'/git/ref/heads/main']={'ref':'refs/heads/main','object':{'type':'commit','sha':HEAD}}
    c.update(sources=sources,all_new=all_new,old=old,r0=r0,r1=r1,jobs0=jobs0,jobs1=jobs1,art0=art0,art1=art1,
        revision=revision,trees=trees,client=Client(responses,{67890:candidate_zip,12345:page_zip}))
    return c


def call(c,**kwargs):
    return m.verify_pair(gh,c['client'],original_run_id=R0,original_cas_commit=CAS,reuse_run_id=R1,
        require_absent=True,now=NOW,**kwargs)


@pytest.mark.parametrize('suspension',[False,True])
def test_closed_28_or_typed_29_pair_retains_original_failure_and_real_freeze_clock(tmp_path,monkeypatch,suspension):
    c=fixture(tmp_path,monkeypatch,suspension=suspension)
    proof,sources,_=call(c)
    assert sources==c['sources'] and proof['original_file_count']==28+int(suspension)
    assert proof['original_generation_run']['conclusion']=='failure'
    assert proof['successful_reuse_run']['conclusion']=='success'
    assert proof['original_cas_commit']==CAS and proof['successful_publication_git_sha']==HEAD
    assert proof['source_authority_issued'] is proof['new_prediction_freeze_verified'] is False
    root=c['output'];out=gh.import_redeployed_p0_sources(root,expected_run_id=R1,
        original_generation_run_id=R0,original_cas_commit=CAS,github_client=c['client'],clock=lambda:NOW)
    assert m.validate_import_receipt(gh,out)==out['continuation']
    assert {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}==sources


@pytest.mark.parametrize('mutation',['wrong_r0','wrong_head','old_attempt','r0_success','r1_dispatch','r1_failure',
    'cas_not_skipped','generation_not_skipped','other_job_failed','wrong_parent','wrong_tree','wrong_digest',
    'expired','same_run_pages_wrong_origin','upstream_future','wrong_revision','missing_source','partial_candidate'])
def test_dual_admission_rejects_mismatched_identity_and_sources(tmp_path,monkeypatch,mutation):
    c=fixture(tmp_path,monkeypatch);res=c['client'].responses
    if mutation=='wrong_r0': c['r0']['id']+=1
    elif mutation=='wrong_head': c['r0']['head_sha']='f'*40
    elif mutation=='old_attempt': c['r0']['run_attempt']=2
    elif mutation=='r0_success': c['r0']['conclusion']='success'
    elif mutation=='r1_dispatch': c['r1']['event']='workflow_dispatch'
    elif mutation=='r1_failure': c['r1']['conclusion']='failure'
    elif mutation=='cas_not_skipped': c['jobs1'][1]['conclusion']='success'
    elif mutation=='generation_not_skipped': c['jobs1'][0]['steps'][1]['conclusion']='success'
    elif mutation=='other_job_failed':
        path=gh.API_PREFIX+'/actions/runs/'+R0+'/attempts/1/jobs?per_page=100'
        res[path]['jobs'].append(dict(c['jobs0'][0],id=999,name='other',conclusion='failure'));res[path]['total_count']+=1
    elif mutation=='wrong_parent': res[gh.API_PREFIX+'/git/commits/'+CAS]['parents']=[{'sha':'f'*40}]
    elif mutation=='wrong_tree': res[gh.API_PREFIX+'/git/commits/'+CAS]['tree']['sha']='0'*40
    elif mutation=='wrong_digest': c['art0']['digest']='sha256:'+'0'*64
    elif mutation=='expired': c['art0']['expired']=True
    elif mutation=='same_run_pages_wrong_origin': c['art1']['workflow_run']['id']=int(R0)
    elif mutation=='upstream_future': res[f'/repos/njedu2023-prog/a-top10/git/commits/{PRED}']['committer']['date']='2099-01-01T00:00:00Z'
    elif mutation=='wrong_revision':
        r=deepcopy(c['revision']);r['head_sha']=CAS;b=pages_zip(r);c['client'].archives[12345]=b;c['art1'].update(size_in_bytes=len(b),digest='sha256:'+gh.sha256(b))
    elif mutation=='missing_source':
        key=next(p for p in res if '/git/blobs/' in p);res[key]['content']=base64.b64encode(b'changed').decode()
    elif mutation=='partial_candidate':
        t=git_tree({**c['all_new'],m.PREFIX+'day_'+c['day']+'.json':b'{}'})
        res[gh.API_PREFIX+'/git/commits/'+HEAD]['tree']['sha']=t['sha'];res[gh.API_PREFIX+'/git/trees/'+t['sha']+'?recursive=1']=t
    with pytest.raises((ValueError,KeyError)):call(c)


def test_deadline_and_explicit_replay_are_independent_of_old_generation_time(tmp_path,monkeypatch):
    c=fixture(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='DEADLINE'):
        m.verify_pair(gh,c['client'],original_run_id=R0,original_cas_commit=CAS,reuse_run_id=R1,
            now=datetime(2026,9,15,1,15,tzinfo=timezone.utc))
    revision,sha=gh.revision_from_zip(c['client'].archives[12345])
    with pytest.raises(ValueError,match='EXPLICIT_REPLAY'):call(c,cached_reuse_revision=(revision,sha))
    c['client'].continuation_replay=True
    assert call(c,cached_reuse_revision=(revision,sha))[0]==call(c)[0]


def test_old_single_run_validators_still_reject_each_half(tmp_path,monkeypatch):
    c=fixture(tmp_path,monkeypatch)
    with pytest.raises(ValueError):gh.validate_run(c['r0'],R0)
    with pytest.raises(ValueError):gh.validate_jobs({'total_count':3,'jobs':c['jobs1']},c['r1'],R1)


@pytest.mark.parametrize('logical', sorted(m.ORIGINAL_P0_CODE_PROFILE))
def test_each_reviewed_p0_code_binding_is_required_across_all_three_valid_trees(tmp_path,monkeypatch,logical):
    c=fixture(tmp_path,monkeypatch);res=c['client'].responses
    for sha,files in ((BASE,c['old']),(CAS,c['all_new']),(HEAD,c['all_new'])):
        tree=git_tree({**files,logical:files[logical]+b'\nchanged\n'})
        res[gh.API_PREFIX+'/git/commits/'+sha]['tree']['sha']=tree['sha']
        res[gh.API_PREFIX+'/git/trees/'+tree['sha']+'?recursive=1']=tree
    with pytest.raises(ValueError,match='REVIEWED_CODE_PROFILE'):call(c)


def test_original_cas_cannot_cross_d2330_even_with_ordered_later_jobs(tmp_path,monkeypatch):
    c=fixture(tmp_path,monkeypatch)
    c['jobs0'][1].update(started_at='2026-09-14T15:29:59Z',completed_at='2026-09-14T15:30:00Z')
    c['jobs0'][2].update(started_at='2026-09-14T15:31:00Z',completed_at='2026-09-14T15:32:00Z')
    c['r0']['updated_at']='2026-09-14T15:33:00Z'
    with pytest.raises(ValueError,match='D2330'):call(c)
