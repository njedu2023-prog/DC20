"""Actual approval validation reused by watchdog; fixtures cannot perform I/O."""
from copy import deepcopy
import pytest

from scripts import candidate_delivery_watchdog as watchdog
from scripts import candidate_runtime_preflight as gate

HEAD, APPROVED, TREE = 'a'*40, 'b'*40, 'c'*40


class ApprovalAPI(watchdog.GitHub):
    def __init__(self):
        self.reads = []
        self.entries = [{'path': path, 'mode': '100644', 'type': 'blob', 'sha': 'd'*40}
            for path in (gate.WORKFLOW, 'scripts/candidate_runtime_preflight.py',
                         'models/decision_model_freeze.json', 'requirements-dev.lock')]
        self.run = {'id': 123, 'event': 'push', 'status': 'completed', 'conclusion': 'success',
            'run_attempt': 1, 'head_branch': 'main', 'path': gate.WORKFLOW,
            'workflow_id': gate.WORKFLOW_ID, 'repository': {'full_name': gate.REPO},
            'head_repository': {'full_name': gate.REPO}, 'head_sha': APPROVED,
            'created_at': '2026-11-10T12:00:00Z'}
        self.job = {'name': gate.JOB, 'run_id': 123, 'head_sha': APPROVED,
            'status': 'completed', 'conclusion': 'success', 'steps': [
                {'name': name, 'status': 'completed', 'conclusion': 'success'}
                for name in (gate.REGRESSION_STEP, gate.FREEZE_STEP, gate.RECORD_STEP)]}
        self.total_jobs, self.truncated, self.fetched_run = 1, False, None

    def request(self, path, body=None):
        assert body is None, 'Approval preflight must never mutate or dispatch'
        self.reads.append(path)
        if path in ('/git/commits/'+HEAD, '/git/commits/'+APPROVED):
            return {'sha': path.rsplit('/', 1)[-1], 'tree': {'sha': TREE}}
        if path == '/git/trees/'+TREE+'?recursive=1':
            return {'sha': TREE, 'truncated': self.truncated, 'tree': deepcopy(self.entries)}
        if path.startswith('/actions/workflows/'):
            runs = [] if self.run is None else [deepcopy(self.run)]
            if 'status=success' in path:
                runs = [run for run in runs if run['conclusion'] == 'success']
            return {'total_count': len(runs), 'workflow_runs': runs}
        if path == '/actions/runs/123':
            return deepcopy(self.fetched_run or self.run)
        if path == '/actions/runs/123/attempts/1/jobs?per_page=100':
            return {'total_count': self.total_jobs, 'jobs': [deepcopy(self.job)]}
        raise AssertionError(path)


def test_watchdog_accepts_only_actual_matching_surface_and_full_ci_steps():
    api = ApprovalAPI()
    result = api.candidate_code_approval(HEAD)
    assert result['status'] == 'APPROVED_IDENTICAL_STATIC_SURFACE'
    assert result['current_head_sha'] == HEAD and result['approved_head_sha'] == APPROVED
    assert result['daily_source_evidence_admitted'] is False
    assert result['production_freeze_issued'] is False
    assert '/actions/runs/123/attempts/1/jobs?per_page=100' in api.reads


@pytest.mark.parametrize('status', ['queued', 'in_progress', 'waiting', 'pending', 'requested'])
def test_matching_incomplete_ci_is_read_only_waiting_not_a_failed_freeze(status):
    api = ApprovalAPI()
    api.run.update(status=status, conclusion=None)
    result = api.candidate_code_approval(HEAD)
    assert result['status'] == 'WAITING_FOR_CODE_CI_APPROVAL'
    assert result['pending_approval_run_id'] == 123
    assert result['current_head_sha'] == HEAD and not result['production_freeze_issued']
    assert '/actions/runs/123' in api.reads
    assert not any('/jobs?' in path for path in api.reads)


def test_no_approved_or_pending_ci_does_not_trigger_speculative_freeze():
    api = ApprovalAPI()
    api.run = None
    result = api.candidate_code_approval(HEAD)
    assert result['status'] == 'WAITING_FOR_CODE_CI_APPROVAL'
    assert result['pending_approval_run_id'] is None
    assert result['reason'] == 'NO_MATCHING_SUCCESSFUL_PUSH_CI'


@pytest.mark.parametrize('field,value', [('event', 'workflow_dispatch'), ('head_branch', 'fork'),
    ('repository', {'full_name': 'foreign/repo'}), ('head_repository', {'full_name': 'foreign/repo'}),
    ('path', '.github/workflows/other.yml'), ('workflow_id', 1), ('run_attempt', 2)])
def test_untrusted_green_run_never_grants_dispatch_approval(field, value):
    api = ApprovalAPI()
    api.run[field] = value
    result = api.candidate_code_approval(HEAD)
    assert result['status'] == 'WAITING_FOR_CODE_CI_APPROVAL'
    assert result['pending_approval_run_id'] is None


@pytest.mark.parametrize('field,value', [('head_branch', 'fork'), ('head_sha', 'f'*40),
    ('repository', {'full_name': 'foreign/repo'}), ('run_attempt', 2), ('id', 124),
    ('created_at', '2026-11-11T12:00:00Z')])
def test_pending_run_identity_is_rechecked_before_reporting_matching_wait(field, value):
    api = ApprovalAPI()
    api.run.update(status='in_progress', conclusion=None)
    api.fetched_run = {**api.run, field: value}
    with pytest.raises((ValueError, AssertionError)):
        api.candidate_code_approval(HEAD)


@pytest.mark.parametrize('conclusion', ['failure', 'timed_out', 'cancelled', 'action_required', 'skipped', 'neutral', None])
def test_failed_or_unapproved_completed_same_surface_ci_is_explicit_failure(conclusion):
    api = ApprovalAPI()
    api.run.update(status='completed', conclusion=conclusion)
    with pytest.raises(ValueError, match='CODE_CI_FAILED_REQUIRES_REPAIR'):
        api.candidate_code_approval(HEAD)


def test_success_arriving_between_reads_waits_for_full_jobs_verification():
    api = ApprovalAPI()
    api.run.update(status='in_progress', conclusion=None)
    api.fetched_run = {**api.run, 'status': 'completed', 'conclusion': 'success'}
    result = api.candidate_code_approval(HEAD)
    assert result['status'] == 'WAITING_FOR_CODE_CI_APPROVAL'
    assert result['pending_approval_run_id'] is None
    assert result['latest_matching_approval_run_id'] == 123
    assert result['reason'] == 'SUCCESSFUL_PUSH_CI_RECHECK_REQUIRED'
    assert not any('/jobs?' in path for path in api.reads)


@pytest.mark.parametrize('latest_failed', [True, False])
def test_latest_same_surface_attempt_is_selected_by_time_not_list_order(latest_failed):
    class Multiple(ApprovalAPI):
        def __init__(self):
            super().__init__()
            self.old = {**self.run, 'id': 456, 'status': 'in_progress' if latest_failed else 'completed',
                        'conclusion': None if latest_failed else 'failure'}
            self.new = {**self.run, 'id': 123, 'created_at': '2026-11-10T12:01:00Z',
                        'status': 'completed' if latest_failed else 'in_progress',
                        'conclusion': 'failure' if latest_failed else None}
        def request(self, path, body=None):
            assert body is None
            if path.startswith('/actions/workflows/'):
                rows = [] if 'status=success' in path else [self.old, self.new]
                return {'total_count': len(rows), 'workflow_runs': deepcopy(rows)}
            if path == '/actions/runs/123':
                return deepcopy(self.new)
            return super().request(path, body)
    api = Multiple()
    if latest_failed:
        with pytest.raises(ValueError, match='CODE_CI_FAILED_REQUIRES_REPAIR'):
            api.candidate_code_approval(HEAD)
    else:
        result = api.candidate_code_approval(HEAD)
        assert result['status'] == 'WAITING_FOR_CODE_CI_APPROVAL'
        assert result['pending_approval_run_id'] == 123


@pytest.mark.parametrize('field,value', [('run_id', 124), ('head_sha', 'f'*40), ('conclusion', 'skipped')])
def test_green_run_without_matching_successful_job_is_an_error_not_waiting(field, value):
    api = ApprovalAPI()
    api.job[field] = value
    with pytest.raises(ValueError, match='APPROVAL_REGRESSION_JOB_NOT_SUCCESSFUL'):
        api.candidate_code_approval(HEAD)


@pytest.mark.parametrize('name', [gate.REGRESSION_STEP, gate.FREEZE_STEP, gate.RECORD_STEP])
def test_green_badge_cannot_substitute_for_skipped_required_step(name):
    api = ApprovalAPI()
    next(step for step in api.job['steps'] if step['name'] == name)['conclusion'] = 'skipped'
    with pytest.raises(ValueError, match='APPROVAL_REQUIRED_STEP_NOT_RUN'):
        api.candidate_code_approval(HEAD)


@pytest.mark.parametrize('fault,reason', [('tree', 'APPROVAL_TREE_INCOMPLETE'),
    ('jobs', 'APPROVAL_JOBS_INCOMPLETE'), ('missing', 'INCOMPLETE_STATIC_APPROVAL_SURFACE')])
def test_incomplete_evidence_is_never_treated_as_empty_approval(fault, reason):
    api = ApprovalAPI()
    if fault == 'tree':
        api.truncated = True
    elif fault == 'jobs':
        api.total_jobs = 2
    else:
        api.entries.pop()
    with pytest.raises(ValueError, match=reason):
        api.candidate_code_approval(HEAD)


def test_api_read_failure_remains_failure_without_fallback():
    api = ApprovalAPI()
    def fail(path, body=None):
        raise ValueError('GITHUB_READ_UNAVAILABLE')
    api.request = fail
    with pytest.raises(ValueError, match='GITHUB_READ_UNAVAILABLE'):
        api.candidate_code_approval(HEAD)
