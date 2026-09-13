"""Synthetic HTTP/Git state only; no credentials, natural evidence or writes."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import pytest

from work.profit_1000_upgrade import candidate_natural_workflow as w
from work.profit_1000_upgrade import candidate_natural_forward_test as fixtures

NOW = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
DEADLINE = datetime(2026, 9, 15, 1, 15, tzinfo=timezone.utc)


def files():
    return {w.PREFIX + kind + '_20260914.json': b'{"synthetic":true}\n'
        for kind in ('day', 'p0_sources', 'local_freeze', 'workflow')}


class FakeGit:
    def __init__(self, fault=None):
        self.fault, self.calls, self.patches, self.ref_reads = fault, [], [], 0
        self.old = [{'path': 'protected.txt', 'mode': '100644', 'type': 'blob', 'sha': 'f' * 40}]
        if fault == 'existing':
            self.old.append({'path': next(iter(files())), 'mode': '100644', 'type': 'blob', 'sha': 'e' * 40})
    def call(self, method, path, payload=None):
        self.calls.append((method, path, deepcopy(payload)))
        if path == '/git/ref/heads/main':
            self.ref_reads += 1
            return {'object': {'sha': 'e' * 40 if self.fault == 'race' and self.ref_reads > 1 else 'a' * 40}}
        if path == '/git/commits/' + 'a' * 40:
            return {'sha': 'a' * 40, 'tree': {'sha': 'b' * 40}}
        if path == '/git/trees/' + 'b' * 40 + '?recursive=1':
            return {'sha': 'b' * 40, 'truncated': self.fault == 'truncated', 'tree': deepcopy(self.old)}
        if path == '/git/blobs':
            import base64
            raw = base64.b64decode(payload['content'], validate=True)
            return {'sha': 'f' * 40 if self.fault == 'blob' else w.git_blob(raw)}
        if method == 'POST' and path == '/git/trees':
            assert payload['base_tree'] == 'b' * 40
            self.new = deepcopy(self.old) + deepcopy(payload['tree'])
            parents = {str(parent) for item in payload['tree'] for parent in w.PurePosixPath(item['path']).parents if str(parent) != '.'}
            self.new.extend({'path': path, 'mode': '040000', 'type': 'tree', 'sha': 'e'*40} for path in sorted(parents))
            if self.fault == 'old_change': self.new[0]['sha'] = 'e' * 40
            if self.fault == 'extra_file': self.new.append({'path': 'outputs/decision/bad.json', 'mode': '100644', 'type': 'blob', 'sha': 'e' * 40})
            if self.fault == 'duplicate': self.new.append(deepcopy(self.new[-1]))
            if self.fault == 'extra_tree': self.new.append({'path': 'unrelated-empty', 'mode': '040000', 'type': 'tree', 'sha': 'e'*40})
            return {'sha': 'd' * 40}
        if path == '/git/trees/' + 'd' * 40 + '?recursive=1':
            return {'sha': 'd' * 40, 'truncated': False, 'tree': deepcopy(self.new)}
        if path == '/git/commits':
            assert payload['parents'] == ['a' * 40] and payload['tree'] == 'd' * 40
            return {'sha': 'c' * 40, 'parents': [{'sha': 'f' * 40 if self.fault == 'parent' else 'a' * 40}], 'tree': {'sha': 'd' * 40}}
        if method == 'PATCH':
            self.patches.append(payload)
            assert path == '/git/refs/heads/main' and payload == {'sha': 'c' * 40, 'force': False}
            return {'object': {'sha': 'f' * 40 if self.fault == 'uncertain' else 'c' * 40}}
        raise AssertionError((method, path))


def test_single_api_commit_only_four_research_paths_and_no_admission():
    git = FakeGit()
    out = w.publish_exact_new_day(files(), github_client=git, deadline=DEADLINE, clock=lambda: NOW)
    assert out['status'] == 'GIT_PUBLICATION_ACKNOWLEDGED'
    assert len(out['files']) == 4 and out['commit_sha'] == 'c' * 40
    assert out['natural_forward_admission_issued'] is out['production_activation_allowed'] is False
    assert out['independent_job_timing_check_still_required'] is True
    assert len(git.patches) == 1


@pytest.mark.parametrize('fault', ['existing', 'truncated', 'blob', 'old_change', 'extra_file', 'extra_tree', 'duplicate', 'parent', 'race'])
def test_conflicts_and_unexpected_changes_never_move_ref(fault):
    git = FakeGit(fault)
    with pytest.raises(ValueError): w.publish_exact_new_day(files(), github_client=git, deadline=DEADLINE, clock=lambda: NOW)
    assert git.patches == []


@pytest.mark.parametrize('times', [[DEADLINE], [NOW, DEADLINE]])
def test_deadline_before_cas_does_not_publish(times):
    git, stamps = FakeGit(), iter(times)
    with pytest.raises(ValueError, match='DEADLINE'):
        w.publish_exact_new_day(files(), github_client=git, deadline=DEADLINE, clock=lambda: next(stamps))
    assert git.patches == []


def test_ack_after_deadline_is_not_admitted_or_deleted():
    git, stamps = FakeGit(), iter([NOW, NOW, DEADLINE])
    result = w.publish_exact_new_day(files(), github_client=git, deadline=DEADLINE, clock=lambda: next(stamps))
    assert result['status'] == 'LATE_PUBLICATION_NOT_ADMITTED' and len(git.patches) == 1
    assert not result['natural_forward_admission_issued']


def test_uncertain_ack_is_not_retried():
    git = FakeGit('uncertain')
    with pytest.raises(ValueError, match='RECONCILIATION'):
        w.publish_exact_new_day(files(), github_client=git, deadline=DEADLINE, clock=lambda: NOW)
    assert len(git.patches) == 1


@pytest.mark.parametrize('path', ['outputs/decision/day_20260914.json', w.PREFIX+'day_20260911.json',
    w.PREFIX+'../day_20260914.json', w.PREFIX+'day_20260915.json'])
def test_scope_or_mixed_dates_rejected_before_network(path):
    git, values = FakeGit(), files()
    values[path] = values.pop(next(iter(values)))
    with pytest.raises(ValueError): w.publish_exact_new_day(values, github_client=git, deadline=DEADLINE, clock=lambda: NOW)
    assert not git.calls


def make_preparation(tmp_path, monkeypatch):
    case = fixtures.setup_case(tmp_path, monkeypatch)
    local = fixtures.run(case)
    record = fixtures.record(local)
    # Explicit synthetic contract mutation, never returned by an actual import.
    record['clock_mode'] = local['clock_mode'] = 'HOST_SYSTEM_UTC'
    record.pop('snapshot_sha256')
    record['snapshot_sha256'] = fixtures.m.scorer.canonical_sha(record)
    raw = fixtures.m.storage.encoded(record)
    local.update(snapshot_file_sha256=w.digest(raw), snapshot_sha256=record['snapshot_sha256'])
    imported = {'schema_version': 'SYNTHETIC_IMPORT_SCHEMA', 'status': 'IMPORTED_ORIGINAL_P0_BYTES_REQUIRES_D_ADAPTER',
        'injected_client_for_test': False, **{k: record[k] for k in ('signal_date', 'exec_date', 'exit_date')},
        'source_root': record['input_arguments']['source_root'], 'expected_p0_sha256': case['expected'],
        'source_file_bindings': [{'path': b['receipt_path'], 'sha256': b['sha256'], 'bytes': b['bytes'],
            'git_blob_sha1': 'a'*40, 'git_mode': '100644'}
            for b in record['D_source_evidence']['source_file_bindings']],
        'observed_p0_cas_job_completed_at': '2026-09-14T11:59:00Z'}
    monkeypatch.setattr(w, 'dependencies', lambda: (fixtures.m, SimpleNamespace(SCHEMA='SYNTHETIC_IMPORT_SCHEMA')))
    context = {'repository': w.REPO, 'branch': 'main', 'run_attempt': 1, 'run_id': 123, 'code_head_sha': 'a' * 40}
    return raw, local, imported, context


def test_preparation_keeps_exact_four_file_bytes_and_qualification(tmp_path, monkeypatch):
    args = make_preparation(tmp_path, monkeypatch)
    prepared, cutoff = w.prepare_publication(*args, now=NOW)
    assert prepared[w.PREFIX+'day_20260914.json'] == args[0] and len(prepared) == 4
    assert cutoff == DEADLINE


@pytest.mark.parametrize('field,value', [('signal_date', '20260915'), ('injected_client_for_test', True),
    ('status', 'READY'), ('observed_p0_cas_job_completed_at', '2026-09-14T12:01:00Z')])
def test_import_drift_and_p0_time_cannot_be_new_freeze(tmp_path, monkeypatch, field, value):
    raw, local, imported, context = make_preparation(tmp_path, monkeypatch)
    imported[field] = value
    with pytest.raises(ValueError): w.prepare_publication(raw, local, imported, context, now=NOW)


def test_mismatched_source_byte_binding_is_rejected(tmp_path, monkeypatch):
    raw, local, imported, context = make_preparation(tmp_path, monkeypatch)
    imported['source_file_bindings'][0]['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='28_ORIGINAL'): w.prepare_publication(raw, local, imported, context, now=NOW)


@pytest.mark.parametrize('field,value', [('status', 'EXISTING_IDENTICAL_SNAPSHOT_REVALIDATED_NO_NEW_ADMISSION'),
    ('snapshot_file_sha256', '0'*64), ('clock_mode', 'INJECTED_TEST_CLOCK_RESEARCH_ONLY'),
    ('local_freeze_completed_before_cutoff', False)])
def test_no_promotion_of_untimely_or_changed_local_receipts(tmp_path, monkeypatch, field, value):
    raw, local, imported, context = make_preparation(tmp_path, monkeypatch)
    local[field] = value
    with pytest.raises(ValueError): w.prepare_publication(raw, local, imported, context, now=NOW)


def test_publisher_rejects_after_safety_cutoff(tmp_path, monkeypatch):
    args = make_preparation(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='SAFETY_WINDOW'): w.prepare_publication(*args, now=DEADLINE)


def test_duplicate_source_rows_are_not_collapsed_into_acceptance(tmp_path, monkeypatch):
    raw, local, imported, context = make_preparation(tmp_path, monkeypatch)
    imported['source_file_bindings'].append(deepcopy(imported['source_file_bindings'][0]))
    with pytest.raises(ValueError, match='28_SOURCE_ROWS'): w.prepare_publication(raw, local, imported, context, now=NOW)


def test_last_code_guard_failure_prevents_ref_update():
    git = FakeGit()
    def guard(): raise ValueError('CHANGED_CODE')
    with pytest.raises(ValueError, match='CHANGED_CODE'):
        w.publish_exact_new_day(files(), github_client=git, deadline=DEADLINE, clock=lambda: NOW, pre_cas_guard=guard)
    assert git.patches == []


def test_payload_mutation_during_network_is_not_published():
    git, values = FakeGit(), files()
    def guard(): values[next(iter(values))] = b'changed'
    with pytest.raises(ValueError, match='PAYLOAD_CHANGED'):
        w.publish_exact_new_day(values, github_client=git, deadline=DEADLINE, clock=lambda: NOW, pre_cas_guard=guard)
    assert git.patches == []


def test_patch_mutation_never_issues_wrong_ack_or_retries():
    values = files()
    class MutatingGit(FakeGit):
        def call(self, method, path, payload=None):
            result = super().call(method, path, payload)
            if method == 'PATCH': values[next(iter(values))] = b'changed after CAS'
            return result
    git = MutatingGit()
    with pytest.raises(ValueError, match='READ_ONLY_RECONCILIATION'):
        w.publish_exact_new_day(values, github_client=git, deadline=DEADLINE, clock=lambda: NOW)
    assert len(git.patches) == 1


def test_post_cas_source_failure_never_retries_or_grants_admission():
    git, checks = FakeGit(), []
    def guard():
        checks.append(True)
        if len(checks) == 2: raise ValueError('POST_CAS_SOURCE_CHANGED')
    with pytest.raises(ValueError, match='POST_CAS_SOURCE_CHANGED'):
        w.publish_exact_new_day(files(), github_client=git, deadline=DEADLINE, clock=lambda: NOW, pre_cas_guard=guard)
    assert len(git.patches) == 1


def test_reviewed_real_dependency_pins_are_wired_without_network():
    runner, importer = w.dependencies()
    assert w.RUNNER_SHA == w.digest(Path(runner.__file__).read_bytes())
    assert w.IMPORTER_SHA == w.digest(Path(importer.__file__).read_bytes())
    w.publication_guard()


def test_workflow_uses_original_p0_run_and_research_only_api_writes():
    import yaml
    doc = yaml.load((w.ROOT / '.github/workflows/research_candidate_natural_forward.yml').read_text(), Loader=yaml.BaseLoader)
    events = doc['on']
    assert events['workflow_dispatch']['inputs']['dry_run']['default'] == 'true'
    assert events['workflow_run']['workflows'] == ['DC2.0 · Publish Primary D List (P0)']
    assert 'schedule' not in events and doc['permissions'] == {'contents': 'read'}
    assert doc['jobs']['freeze']['needs'] == 'validate'
    assert doc['jobs']['freeze']['permissions'] == {'actions': 'read', 'contents': 'write'}
    assert '343703608' in doc['jobs']['validate']['if'] and 'run_attempt == 1' in doc['jobs']['validate']['if']
    for job in doc['jobs'].values():
        for step in job['steps']:
            if 'uses' in step:
                assert w.re.fullmatch(r'actions/[a-z-]+@[0-9a-f]{40}', step['uses'])
                if step['uses'].startswith('actions/checkout@'):
                    assert step['with'] == {'ref': '${{ github.sha }}', 'persist-credentials': 'false'}
            if 'run' in step:
                assert not any(x in step['run'] for x in ('git push', 'ssh ', '${{ inputs.', '${{ github.event.'))
    steps = doc['jobs']['freeze']['steps']
    active = next(x for x in steps if 'candidate_natural_workflow' in x.get('run', ''))
    assert active['env']['P0_RUN_ID'].endswith('inputs.p0_run_id }}')
    assert 'DC20_CANDIDATE_GITHUB_TOKEN' in active['env']
    assert not any('TUSHARE_TOKEN' in x.get('env', {}) for x in steps)


def test_original_model_asset_is_exact_accepted_evaluation():
    runner, _ = w.dependencies()
    raw = w.MODEL_PATH.read_bytes()
    assert w.digest(raw) == runner.EVALUATION_SHA
    doc = w.json.loads(raw)
    assert len(doc['predictions']) == 1548


def test_exact_deployed_model_asset_can_freeze_synthetic_p0(tmp_path, monkeypatch):
    monkeypatch.setattr(fixtures, 'ACTUAL_EVALUATION', w.MODEL_PATH)
    case = fixtures.setup_case(tmp_path, monkeypatch, actual=True)
    local = fixtures.run(case)
    frozen = fixtures.record(local)
    assert frozen['model_evaluation']['sha256'] == fixtures.FIXED_EVAL_SHA
    assert frozen['clock_mode'] == 'INJECTED_TEST_CLOCK_RESEARCH_ONLY'
    assert frozen['natural_forward_admission_issued'] is False


def test_failed_cas_keeps_original_local_receipts_without_success(tmp_path, monkeypatch):
    runner = SimpleNamespace(_path=lambda p: p, _read=lambda p: (b'synthetic-snapshot', None),
        freeze_natural_day=lambda *a, **k: {'snapshot_path': str(tmp_path / 'synthetic-day')})
    importer = SimpleNamespace(exact_id=lambda x: x, TOKEN_ENV='SYNTHETIC_TOKEN_ENV',
        GitHubReadClient=lambda token: object(),
        import_p0_sources=lambda *a, **k: {'signal_date': '20260914', 'expected_p0_sha256': {'synthetic': 'true'}})
    monkeypatch.setenv('SYNTHETIC_TOKEN_ENV', 'synthetic-non-network-token')
    monkeypatch.setattr(w, 'dependencies', lambda: (runner, importer))
    monkeypatch.setattr(w, 'execution_context', lambda: {'synthetic': True})
    monkeypatch.setattr(w, 'prepare_publication', lambda *a, **k: (files(), DEADLINE))
    def fail(*a, **k):
        work = next(tmp_path.glob('dc20-candidate-natural-*'))
        assert (work / 'p0_source_receipt.json').is_file()
        assert (work / 'local_freeze_receipt.json').is_file()
        assert not (work / 'publication.json').exists()
        raise ValueError('SYNTHETIC_REMOTE_CONFLICT_NO_RETRY')
    monkeypatch.setattr(w, 'publish_exact_new_day', fail)
    with pytest.raises(ValueError, match='SYNTHETIC_REMOTE_CONFLICT'):
        w.main(['--p0-run-id', '123', '--work-parent', str(tmp_path), '--publish'])
    assert not list(tmp_path.rglob('publication.json'))


@pytest.mark.parametrize('method,path', [('DELETE','/git/refs/heads/main'), ('POST','/issues'),
    ('PATCH','/git/refs/heads/other'), ('GET','https://evil.invalid'), ('GET','/contents/secrets')])
def test_writer_transport_cannot_expand_endpoint_or_host(method, path):
    writer = w.GitHubWriter('synthetic-test-token')
    with pytest.raises(ValueError, match='FORBIDDEN'): writer.call(method, path)
    assert writer.calls == 0
