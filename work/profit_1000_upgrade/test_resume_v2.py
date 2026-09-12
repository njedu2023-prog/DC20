"""Safe immutable base import, without network or production writes."""
import copy
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from work.profit_1000_upgrade import run, resume_v2
from work.profit_1000_upgrade.policy_v2 import CONTRACT


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture(tmp_path, monkeypatch, *, extra=None, duplicate=False):
    plan = copy.deepcopy(run.load_plan('v2'))
    root = run.initialize_mirror(tmp_path/'mirror',plan_version='v2')
    receipt = json.dumps({'production_writes':False,'existing_truth_overwritten':False}).encode()
    plan['base_archive'].update(daily_partitions=1,limit_partitions=1,collection_receipt_sha256=digest(receipt))
    files={'.dc20-profit-1000-research-root.json':json.dumps({
        'schema_version':'dc20_profit_1000_research_mirror_v1','production_writes':False,
        'plan_sha256':plan['base_archive']['plan_sha256']}).encode(),
        'collection_receipt.json':receipt,
        'research_results/candidate.json':json.dumps({'execution_provenance':{
            'run_id':plan['base_archive']['run_id'],'run_commit':plan['base_archive']['run_commit']}}).encode(),
        'data/market/raw/2022/20221114/daily.csv':b'ts_code,trade_date,open,close\n600000.SH,20221114,10,10\n',
        'data/market/raw/2022/20221114/stk_limit.csv':b'ts_code,trade_date,up_limit,down_limit\n600000.SH,20221114,11,9\n',
        'data/market/raw/2022/20221114/stk_auction_o.csv':b'OLD AUCTION NEVER IMPORT',
        'data/market/exit_1000_1m/2022/20221114/600000_SH.csv':b'OLD MINUTE NEVER IMPORT',
        'research_results/labels.json':b'OLD LABEL NEVER IMPORT'}
    files.update({b['path']:(run.ROOT/b['path']).read_bytes() for b in plan['source_inputs'].values()})
    archive=tmp_path/'base.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as bundle:
        for name,raw in files.items():
            bundle.writestr(name,raw)
        if extra:
            bundle.writestr(extra,b'unsafe')
        if duplicate:
            bundle.writestr('collection_receipt.json',receipt)
    plan['base_archive']['zip_sha256']=run.sha(archive)
    old_load=run.load_plan
    monkeypatch.setattr(run,'load_plan',lambda version='v1':plan if version=='v2' else old_load())
    return root,archive,plan


def test_import_reuses_daily_limits_not_old_sources_or_outcomes(tmp_path,monkeypatch):
    root,archive,plan=fixture(tmp_path,monkeypatch)
    before=archive.read_bytes()
    result=resume_v2.import_verified_archive(root,archive,plan)
    assert result['imported_partitions']==2
    assert len(result['source_files'])==2
    assert archive.read_bytes()==before
    assert not list(root.rglob('*stk_auction_o*'))
    assert not (root/'data/market/exit_1000_1m').exists()
    assert not (root/'research_results').exists()
    assert run.plan_version_for(root)=='v2'
    assert (root/'research_inputs/base_archive_import.json').is_file()


@pytest.mark.parametrize('extra',['../escape','/absolute','x/../../escape','x\\escape'])
def test_bad_member_rejected_before_source_copy(tmp_path,monkeypatch,extra):
    root,archive,plan=fixture(tmp_path,monkeypatch,extra=extra)
    with pytest.raises(ValueError,match='unsafe'):
        resume_v2.import_verified_archive(root,archive,plan)
    assert not (root/'data/market/raw').exists()


def test_duplicate_zip_member_rejected(tmp_path,monkeypatch):
    with pytest.warns(UserWarning):
        root,archive,plan=fixture(tmp_path,monkeypatch,duplicate=True)
    with pytest.raises(ValueError,match='duplicate'):
        resume_v2.import_verified_archive(root,archive,plan)


def test_sha_wrong_or_reimport_never_overwrites(tmp_path,monkeypatch):
    root,archive,plan=fixture(tmp_path,monkeypatch)
    raw=archive.read_bytes()
    archive.write_bytes(raw+b'changed')
    with pytest.raises(ValueError,match='SHA'):
        resume_v2.import_verified_archive(root,archive,plan)
    archive.write_bytes(raw)
    resume_v2.import_verified_archive(root,archive,plan)
    with pytest.raises(ValueError,match='immutable'):
        resume_v2.import_verified_archive(root,archive,plan)


def test_present_conflicting_target_not_overwritten(tmp_path,monkeypatch):
    root,archive,plan=fixture(tmp_path,monkeypatch)
    path=root/'data/market/raw/2022/20221114/daily.csv'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'user original')
    with pytest.raises(ValueError,match='already exists'):
        resume_v2.import_verified_archive(root,archive,plan)
    assert path.read_bytes()==b'user original'


def test_aliased_archive_rejected(tmp_path,monkeypatch):
    root,archive,plan=fixture(tmp_path,monkeypatch)
    link=tmp_path/'alias.zip'
    link.symlink_to(archive)
    with pytest.raises(ValueError,match='aliased'):
        resume_v2.import_verified_archive(root,link,plan)


def test_import_wrong_plan_before_writes(tmp_path,monkeypatch):
    root,archive,plan=fixture(tmp_path,monkeypatch)
    bad=copy.deepcopy(plan)
    bad['base_archive']['run_id']='999'
    with pytest.raises(ValueError,match='registered'):
        resume_v2.import_verified_archive(root,archive,bad)
    assert not (root/'data/market/raw').exists()


def test_registered_v2_keeps_original_engineering_plan_and_source_hashes():
    old,new=run.load_plan(),run.load_plan('v2')
    for key in ('data_gates','candidate_model','training_cutoff_date','validation_end_date','future_holdout_start_date',
                'cost_rate','stress_cost_rate','source_inputs','scope','sell_rule'):
        assert new[key]==old[key]
    assert new['supersedes_plan_sha256']==run.sha(run.plan_path('v1'))
    assert all(new[k]==v for k,v in CONTRACT.items())
    for binding in new['adapter_sources']:
        assert run.safe_input(run.ROOT,binding).is_file()


def collection_fixture(tmp_path,monkeypatch,*,day='20221114'):
    from work.profit_1000_upgrade import auction_truth
    root,archive,plan=fixture(tmp_path,monkeypatch)
    resume_v2.import_verified_archive(root,archive,plan)
    base_path=root/'research_inputs/base_archive_import.json'
    item={'trade_date':day,'endpoint':'stk_auction','ts_code':None,'network_request_performed':False,
          'status':'HISTORY_BEFORE_CANONICAL_COVERAGE','source_policy_id':CONTRACT['auction_source_policy_id'],
          'plan_sha256':run.sha(run.plan_path('v2'))}
    files=[]
    if day>='20250101':
        request=auction_truth.request_contract(day)
        raw=json.dumps({'code':0,'data':{'fields':list(auction_truth.FIELDS),'items':[], 'count':0,'has_more':False}}).encode()
        bodies=auction_truth.source_bytes(raw,day,request=request,fetched_at_utc='2026-09-12T09:00:00Z',network_request_performed=True)
        paths=auction_truth.source_paths(root,day)
        paths[0].parent.mkdir(parents=True)
        for p,b in zip(paths,bodies):
            p.write_bytes(b)
            files.append({'path':p.relative_to(root).as_posix(),'sha256':digest(b)})
        item.update(status='EXACT_TRUTH_WRITTEN',network_request_performed=True,request=request,
                    http_response_sha256=digest(raw),source_status='CANONICAL_TABLE_EMPTY')
    manifest={'rows':[{'exec_date':day}], 'source_bindings':[], **dict(CONTRACT)}
    receipt={'schema_version':'dc20_profit_1000_collection_receipt_v2','as_of_date':plan['as_of_date'],
        'auction_attempts_complete':True,'auction_evidence_complete':True,
        'production_writes':False,'existing_truth_overwritten':False,'credential_persisted':False,
        'candidate_source_bindings':[], 'new_source_files':files,'existing_source_files':[],
        'request_receipts':[item], 'plan_sha256':run.sha(run.plan_path('v2')),
        'collection_request_sha256':run.sha(run.HERE/'COLLECTION_V2.json'),
        'base_archive_import_binding':{'path':'research_inputs/base_archive_import.json','sha256':run.sha(base_path)}, **dict(CONTRACT)}
    (root/'collection_receipt.json').write_text(json.dumps(receipt))
    return root,manifest,plan,receipt


@pytest.mark.parametrize('day',['20221114','20260817'])
def test_shared_gate_accepts_only_real_qualified_source_or_precoverage(tmp_path,monkeypatch,day):
    root,manifest,plan,_=collection_fixture(tmp_path,monkeypatch,day=day)
    bindings,summary=run.validate_collection_evidence(root,manifest,plan)
    assert summary['verified_auction_dates']==int(day>='20250101')
    assert summary['verified_coverage_declarations']==int(day<'20250101')
    assert 'research_inputs/base_archive_import.json' in bindings


@pytest.mark.parametrize('change',['attempt_only','wrong_body_sha','wrong_request','old_endpoint','duplicate','missing','wrong_policy','wrong_plan','corrupt_pair'])
def test_shared_gate_rejects_false_canonical_qualification(tmp_path,monkeypatch,change):
    root,manifest,plan,receipt=collection_fixture(tmp_path,monkeypatch,day='20260817')
    item=receipt['request_receipts'][0]
    if change=='attempt_only': item['status']='PENDING_NETWORK_ERROR'
    elif change=='wrong_body_sha': item['http_response_sha256']='0'*64
    elif change=='wrong_request': item['request']['params']['trade_date']='20260818'
    elif change=='old_endpoint': item['endpoint']='stk_auction_o'
    elif change=='duplicate': receipt['request_receipts'].append(copy.deepcopy(item))
    elif change=='missing': receipt['request_receipts']=[]
    elif change=='wrong_policy': receipt['auction_source_policy_id']='old_o'
    elif change=='wrong_plan': receipt['plan_sha256']='0'*64
    else:
        path=root/receipt['new_source_files'][0]['path']
        path.write_bytes(b'corrupt')
    (root/'collection_receipt.json').write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        run.validate_collection_evidence(root,manifest,plan)


def test_precoverage_cannot_claim_a_network_request(tmp_path,monkeypatch):
    root,manifest,plan,receipt=collection_fixture(tmp_path,monkeypatch)
    receipt['request_receipts'][0]['network_request_performed']=True
    (root/'collection_receipt.json').write_text(json.dumps(receipt))
    with pytest.raises(ValueError,match='pre-coverage'):
        run.validate_collection_evidence(root,manifest,plan)


def test_old_mirror_not_relabelled_as_v2(tmp_path,monkeypatch):
    root,_,_=fixture(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='binding changed'):
        run.require_research_mirror(root,plan_version='v1')
    marker=root/'.dc20-profit-1000-research-root.json'
    value=json.loads(marker.read_text())
    value['schema_version']='dc20_profit_1000_research_mirror_v1'
    marker.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='binding changed'):
        run.require_research_mirror(root)


def test_collection_cannot_smuggle_old_auction_path(tmp_path,monkeypatch):
    root,manifest,plan,receipt=collection_fixture(tmp_path,monkeypatch)
    old=root/'data/market/raw/2022/20221114/stk_auction_o.csv'
    old.write_bytes(b'old source')
    receipt['new_source_files'].append({'path':old.relative_to(root).as_posix(),'sha256':run.sha(old)})
    (root/'collection_receipt.json').write_text(json.dumps(receipt))
    with pytest.raises(ValueError,match='whitelist'):
        run.validate_collection_evidence(root,manifest,plan)


@pytest.mark.parametrize('mode',['valid','wrong_body','missing_receipt','duplicate_binding','duplicate_receipt'])
def test_research_minute_receipt_must_bind_actual_query_and_body(tmp_path,monkeypatch,mode):
    from work.profit_1000_upgrade import minute_truth
    root,manifest,plan,receipt=collection_fixture(tmp_path,monkeypatch)
    day,code='20260818','600000.SH'
    from top10decision.decision.shadow_exit_minute_truth import expected_bar_ends
    rows=[{'ts_code':code,'trade_time':stamp,'open':10,'high':10,'low':10,'close':10,'vol':100,'amount':1000}
          for stamp in expected_bar_ends(day)]
    raw=json.dumps({'code':0,'data':{'fields':list(minute_truth.FIELDS),
        'items':[[row[field] for field in minute_truth.FIELDS] for row in rows],'count':0,'has_more':False}}).encode()
    params=minute_truth.request_parameters(day,code)
    bodies=minute_truth.source_bytes(raw,day,code,request_params=params,fetched_at_utc='2026-09-12T09:00:00Z')
    paths=minute_truth.paths(root,day,code)
    paths[0].parent.mkdir(parents=True)
    for path,body in zip(paths,bodies):
        path.write_bytes(body)
        receipt['new_source_files'].append({'path':path.relative_to(root).as_posix(),'sha256':run.sha(path)})
    receipt['request_receipts'].append({'endpoint':'stk_mins','trade_date':day,'ts_code':code,
        'status':'EXACT_TRUTH_WRITTEN','network_request_performed':True,
        'http_response_sha256':'0'*64 if mode=='wrong_body' else digest(raw),
        'request':{'api_name':'stk_mins','params':params,'fields':list(minute_truth.FIELDS)}})
    if mode=='missing_receipt': receipt['request_receipts'].pop()
    elif mode=='duplicate_binding': receipt['new_source_files'].append(copy.deepcopy(receipt['new_source_files'][-1]))
    elif mode=='duplicate_receipt': receipt['request_receipts'].append(copy.deepcopy(receipt['request_receipts'][-1]))
    (root/'collection_receipt.json').write_text(json.dumps(receipt))
    if mode!='valid':
        with pytest.raises(ValueError,match='minute'):
            run.validate_collection_evidence(root,manifest,plan)
    else:
        bindings,_=run.validate_collection_evidence(root,manifest,plan)
        assert paths[0].relative_to(root).as_posix() in bindings
