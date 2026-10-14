"""Independent synthetic-input regression; never claims actual market outcomes."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import pytest
from work.profit_1000_upgrade import candidate_natural_outcomes as m
from work.profit_1000_upgrade import candidate_natural_outcome_collect as c
from work.profit_1000_upgrade.test_candidate_natural_outcomes import make_case, run, report, refresh
from work.profit_1000_upgrade.test_candidate_natural_outcome_collect import setup, collect


def historical_fixture(case):
    value = deepcopy(case['frozen'])
    value.update(deepcopy(m.natural.HISTORICAL_SNAPSHOT_PROFILE))
    value['snapshot_sha256'] = m.natural.scorer.canonical_sha({k:v for k,v in value.items() if k!='snapshot_sha256'})
    raw = m.natural.storage.encoded(value)
    case['snapshot'].write_bytes(raw)
    case['snapshot_sha'] = hashlib.sha256(raw).hexdigest()
    case['frozen'] = value
    return raw


def test_new_outcome_writer_accepts_complete_historical_snapshot(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch)
    raw = historical_fixture(case)
    old_plan = case['frozen']['registration_sha256']
    assert old_plan != m.natural.registration()[1]
    result = run(case)
    value = report(result)
    assert value['registration_sha256'] == old_plan
    assert value['writer_sha256'] == m.SELF_SHA
    assert value['dependencies'] == m.PINS
    assert value['snapshot_file_sha256'] == case['snapshot_sha']
    assert case['snapshot'].read_bytes() == raw
    assert value['actual_execution_claimed'] is False


def test_new_collector_accepts_complete_historical_snapshot(tmp_path, monkeypatch):
    case = setup(tmp_path, monkeypatch)
    raw = historical_fixture(case)
    assert case['frozen']['registration_sha256'] != m.natural.registration()[1]
    result, calls = collect(case)
    assert calls
    assert result['writer_sha256'] == c.SELF_SHA
    assert result['outcomes_wrapper_sha256'] == m.SELF_SHA
    assert result['snapshot_file_sha256'] == case['snapshot_sha']
    assert case['snapshot'].read_bytes() == raw
    assert result['callable_injected_for_test'] is True
    assert all(row['network_request_performed'] is False for row in result['requests'])


def as_historical_outcome(result):
    writer_sha, pins = m.HISTORICAL_REPORT_PROFILE
    path = Path(result['ledger_path'])
    ledger = json.loads(path.read_bytes())
    for version in ledger['versions']:
        version['writer_sha256'] = writer_sha
        version['dependencies'] = pins
        version['report_sha256'] = m.natural.scorer.canonical_sha({k:v for k,v in version.items() if k!='report_sha256'})
    ledger['ledger_sha256'] = m.natural.scorer.canonical_sha({k:v for k,v in ledger.items() if k!='ledger_sha256'})
    raw = m.natural.storage.encoded(ledger)
    path.write_bytes(raw)
    result['ledger_file_sha256'] = hashlib.sha256(raw).hexdigest()
    return deepcopy(ledger['versions'])


@pytest.mark.parametrize('mixed_prior_dependencies', [False, True])
def test_new_writer_appends_after_exact_old_writer_without_rewriting_version(tmp_path, monkeypatch, mixed_prior_dependencies):
    case = make_case(tmp_path, monkeypatch)
    raw = historical_fixture(case)
    refresh(case, asof=case['t'])
    first = run(case, as_of_date=case['t'])
    old = as_historical_outcome(first)
    refresh(case, asof=case['t1'])
    if mixed_prior_dependencies:
        ledger_path = Path(first['ledger_path'])
        ledger = json.loads(ledger_path.read_bytes())
        version = ledger['versions'][-1]
        version['dependencies'] = deepcopy(m.PINS)
        version['report_sha256'] = m.natural.scorer.canonical_sha({k:v for k,v in version.items() if k!='report_sha256'})
        ledger['ledger_sha256'] = m.natural.scorer.canonical_sha({k:v for k,v in ledger.items() if k!='ledger_sha256'})
        mixed_raw = m.natural.storage.encoded(ledger)
        ledger_path.write_bytes(mixed_raw)
        first['ledger_file_sha256'] = hashlib.sha256(mixed_raw).hexdigest()
        with pytest.raises(ValueError, match='PROFILE'):
            run(case, as_of_date=case['t1'], expected_existing_ledger_sha256=first['ledger_file_sha256'])
        assert ledger_path.read_bytes() == mixed_raw
        return
    next_result = run(case, as_of_date=case['t1'], expected_existing_ledger_sha256=first['ledger_file_sha256'])
    value = json.loads(Path(next_result['ledger_path']).read_bytes())
    assert value['versions'][:-1] == old
    assert value['versions'][-1]['writer_sha256'] == m.SELF_SHA
    assert value['versions'][-1]['dependencies'] == m.PINS
    assert all(v['registration_sha256'] == case['frozen']['registration_sha256'] for v in value['versions'])
    assert case['snapshot'].read_bytes() == raw


@pytest.mark.parametrize('mix_outcome_dependencies', [False, True])
def test_current_collector_continues_full_historical_collection_outcome_pair(tmp_path, monkeypatch, mix_outcome_dependencies):
    from work.profit_1000_upgrade.test_candidate_natural_outcome_collect import observe
    case = setup(tmp_path, monkeypatch)
    raw = historical_fixture(case)
    first, _ = collect(case)
    observed = observe(case, first)
    old_reports = as_historical_outcome(observed)
    old_receipt = deepcopy(first)
    old_receipt['writer_sha256'], old_receipt['outcomes_wrapper_sha256'] = c.HISTORICAL_COLLECTION_PROFILE
    old_receipt['receipt_sha256'] = m.natural.scorer.canonical_sha({k:v for k,v in old_receipt.items() if k!='receipt_sha256'})
    receipt_path = Path(first['collection_root']) / 'receipt.json'
    old_receipt_raw = m.natural.storage.encoded(old_receipt)
    receipt_path.write_bytes(old_receipt_raw)
    if mix_outcome_dependencies:
        ledger_path = Path(observed['ledger_path'])
        ledger = json.loads(ledger_path.read_bytes())
        version = ledger['versions'][-1]
        version['dependencies'] = deepcopy(m.PINS)
        version['report_sha256'] = m.natural.scorer.canonical_sha({k:v for k,v in version.items() if k!='report_sha256'})
        ledger['ledger_sha256'] = m.natural.scorer.canonical_sha({k:v for k,v in ledger.items() if k!='ledger_sha256'})
        mixed_raw = m.natural.storage.encoded(ledger)
        ledger_path.write_bytes(mixed_raw)
        observed['ledger_file_sha256'] = hashlib.sha256(mixed_raw).hexdigest()
        with pytest.raises(ValueError, match='PROFILE'):
            collect(case, previous=old_receipt, prior_outcome=observed, asof=case['t1'])
        assert case['snapshot'].read_bytes() == raw
        return
    second, calls = collect(case, previous=old_receipt, prior_outcome=observed, asof=case['t1'])
    assert calls and any(c.request_identity(row)[0]=='stk_mins' for row in calls)
    assert second['writer_sha256'] == c.SELF_SHA
    assert second['outcomes_wrapper_sha256'] == m.SELF_SHA
    assert receipt_path.read_bytes() == old_receipt_raw
    assert json.loads(Path(observed['ledger_path']).read_bytes())['versions'] == old_reports
    assert case['snapshot'].read_bytes() == raw


@pytest.mark.parametrize('historical_base', [True, False])
def test_cross_profile_legacy_coordinator_rejected(historical_base):
    from work.profit_1000_upgrade import candidate_natural_publication as p
    local,_ = p.code_guard()
    node = lambda digest: {'type':'blob','mode':'100644','sha':digest}
    if historical_base:
        body = {b['path']:node(b['git_blob_sha1']) for b in p.HISTORICAL_CODE_BINDINGS}
        wrong = p.gh.git_blob(local['work/profit_1000_upgrade/candidate_legacy_workflow.py'])
    else:
        body = {path:node(p.gh.git_blob(raw)) for path,raw in local.items()}
        wrong = p.LEGACY_COORDINATOR_BINDING['git_blob_sha1']
    body[p.COORDINATOR_PATH] = node(wrong)
    with pytest.raises(ValueError):
        p.match_publication_code(body, body, local)


@pytest.mark.parametrize('historical_observer', [True, False])
def test_observer_capture_and_publication_graph_cannot_mix(historical_observer):
    from work.profit_1000_upgrade import candidate_natural_evidence_publication as m
    local,_ = m.code_guard()
    p = m.publication
    node = lambda digest: {'type':'blob','mode':'100644','sha':digest}
    if historical_observer:
        contract = m.capture.HISTORICAL_CAPTURE_CONTRACT
        body = {path:node(m.gh.git_blob(raw)) for path,raw in local.items()}
    else:
        contract = (m.capture.SELF_SHA,m.capture.PUBLICATION_SHA)
        body = {b['path']:node(b['git_blob_sha1']) for b in p.HISTORICAL_CODE_BINDINGS}
    manifest = dict(zip(('capture_code_sha256','publication_verifier_sha256'),contract))
    _, overrides = m._observer_profile(manifest)
    for path in (m.CAPTURE_PATH,m.OBSERVER_PATH,m.WORKFLOW_PATH,m.WRITER_PATH):
        body[path] = node(overrides.get(path,m.gh.git_blob(local[path])))
    with pytest.raises(ValueError):
        m.match_observer_code(body,body,local,manifest)


@pytest.mark.parametrize('version', ['v1', 'v2'])
def test_old_report_profile_cannot_claim_current_snapshot(version):
    from work.profit_1000_upgrade import candidate_eligible_forward as eligible
    runner = m.natural if version == 'v1' else eligible
    frozen = {'schema_version': runner.SCHEMA, 'runner_sha256': runner.SELF_SHA,
        'registration_sha256': runner.registration()[1], 'dependency_sha256': runner.PINS}
    historical = {'writer_sha256': m.HISTORICAL_REPORT_PROFILE[0],
        'dependencies': m.HISTORICAL_REPORT_PROFILE[1]}
    with pytest.raises(ValueError, match='UNREGISTERED_OUTCOME_WRITER_PROFILE'):
        m.validate_report_profile(historical, frozen)
    current = {'writer_sha256': m.SELF_SHA, 'dependencies': m.PINS}
    m.validate_report_profile(current, frozen)
    frozen.update(deepcopy(runner.HISTORICAL_SNAPSHOT_PROFILE))
    m.validate_report_profile(historical, frozen)
    m.validate_report_profile(current, frozen)


def test_historical_collector_cannot_claim_current_snapshot(tmp_path, monkeypatch):
    case = setup(tmp_path, monkeypatch)
    first, _ = collect(case)
    old_receipt = deepcopy(first)
    old_receipt['writer_sha256'], old_receipt['outcomes_wrapper_sha256'] = c.HISTORICAL_COLLECTION_PROFILE
    old_receipt['receipt_sha256'] = m.natural.scorer.canonical_sha({k:v for k,v in old_receipt.items() if k!='receipt_sha256'})
    path = Path(first['collection_root']) / 'receipt.json'
    raw = m.natural.storage.encoded(old_receipt)
    path.write_bytes(raw)
    with pytest.raises(ValueError, match='PRIOR_COLLECTION_IDENTITY_CHANGED'):
        collect(case, previous=old_receipt, asof=case['t1'])
    assert path.read_bytes() == raw


def test_old_outcome_ledger_cannot_claim_current_snapshot(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch)
    refresh(case, asof=case['t'])
    first = run(case, as_of_date=case['t'])
    as_historical_outcome(first)
    raw = Path(first['ledger_path']).read_bytes()
    refresh(case, asof=case['t1'])
    with pytest.raises(ValueError, match='UNREGISTERED_OUTCOME_WRITER_PROFILE'):
        run(case, as_of_date=case['t1'], expected_existing_ledger_sha256=first['ledger_file_sha256'])
    assert Path(first['ledger_path']).read_bytes() == raw
