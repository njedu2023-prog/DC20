"""Stub Git only: never network, token discovery, or actual publication."""
from datetime import datetime, timezone
import pytest
from work.profit_1000_upgrade import candidate_natural_evidence_git as m
from work.profit_1000_upgrade.test_candidate_natural_workflow import FakeGit

NOW = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
DEADLINE = datetime(2026, 9, 15, 1, 20, tzinfo=timezone.utc)


def files():
    prefix = m.PREFIX + '20260914/'
    raw = b'explicit synthetic response'
    return {prefix+'manifest.json': b'{"synthetic":true}', prefix+'context.json': b'{"test":true}',
        prefix+'bodies/'+m.digest(raw)+'.bin': raw}


def run(git, values=None, **kw):
    return m.publish_new_evidence(files() if values is None else values, github_client=git,
        deadline=DEADLINE, clock=kw.pop('clock', lambda: NOW), **kw)


def test_append_only_single_nonforce_cas_no_authority():
    git = FakeGit()
    result = run(git)
    assert result['status'] == 'EVIDENCE_GIT_ACKNOWLEDGED'
    assert len(result['files']) == 3 and len(git.patches) == 1
    assert result['natural_forward_admission_issued'] is result['production_activation_allowed'] is False
    assert result['existing_files_modified'] is False


@pytest.mark.parametrize('fault', ['truncated','blob','old_change','extra_file','extra_tree','duplicate','parent','race'])
def test_mutations_do_not_move_ref(fault):
    git = FakeGit(fault)
    with pytest.raises(ValueError):
        run(git)
    assert not git.patches


def test_any_partial_existing_day_rejected_before_blob_write():
    git = FakeGit()
    git.old.append({'path':m.PREFIX+'20260914/bodies/'+'0'*64+'.bin','mode':'100644','type':'blob','sha':'e'*40})
    with pytest.raises(ValueError, match='PARTIAL_DAY'):
        run(git)
    assert not any(method == 'POST' for method, _, _ in git.calls)


@pytest.mark.parametrize('path', ['outputs/decision/manifest.json', m.PREFIX+'20260911/manifest.json',
    m.PREFIX+'20260915/manifest.json',m.PREFIX+'20260914/../manifest.json',m.PREFIX+'20260914/extra.json'])
def test_path_rejected_before_network(path):
    values, git = files(), FakeGit()
    values[path] = values.pop(next(iter(values)))
    with pytest.raises(ValueError):
        run(git, values)
    assert not git.calls


def test_body_hash_rejected_before_network():
    values, git = files(), FakeGit()
    values[next(p for p in values if '/bodies/' in p)] = b'changed'
    with pytest.raises(ValueError, match='BODY_CHANGED'):
        run(git, values)
    assert not git.calls


def test_bounded_files():
    values, git = files(), FakeGit()
    for i in range(65):
        raw = ('extra '+str(i)).encode()
        values[m.PREFIX+'20260914/bodies/'+m.digest(raw)+'.bin'] = raw
    with pytest.raises(ValueError, match='BOUNDED_CAPSULE'):
        run(git, values)
    assert not git.calls


def test_payload_mutation_guard_rejected():
    values, git = files(), FakeGit()
    def guard():
        values[next(iter(values))] = b'changed'
    with pytest.raises(ValueError, match='PAYLOAD_CHANGED'):
        run(git, values, pre_cas_guard=guard)
    assert not git.patches


def test_late_ack_not_admitted_and_not_deleted():
    stamps, git = iter([NOW,NOW,DEADLINE]), FakeGit()
    result = run(git, clock=lambda: next(stamps))
    assert result['status'] == 'LATE_EVIDENCE_NOT_ADMITTED' and len(git.patches) == 1


def test_last_guard_cannot_change_acknowledged_payload():
    values, git, calls = files(), FakeGit(), []
    def guard():
        calls.append(True)
        if len(calls) == 2:
            values[next(iter(values))] = b'changed by final guard'
    with pytest.raises(ValueError, match='POST_GUARD_CHANGE'):
        run(git, values, pre_cas_guard=guard)
    assert len(git.patches) == 1


def test_uncertain_ack_not_retried():
    git = FakeGit('uncertain')
    with pytest.raises(ValueError, match='RECONCILIATION'):
        run(git)
    assert len(git.patches) == 1


def test_pre_cas_deadline():
    stamps, git = iter([NOW,DEADLINE]), FakeGit()
    with pytest.raises(ValueError, match='DEADLINE'):
        run(git, clock=lambda: next(stamps))
    assert not git.patches


@pytest.mark.parametrize('method,path', [('POST','/issues'),('PATCH','/git/refs/heads/other'),
    ('GET','https://evil.invalid'),('GET','/git/trees/'+'a'*40+'/../secrets')])
def test_transport_fixed_routes_no_call(method,path):
    writer = m.EvidenceGitWriter('EXPLICIT_TEST_TOKEN')
    with pytest.raises(ValueError, match='ROUTE'):
        writer.call(method,path)
    assert writer.calls == 0


def test_transport_cannot_reset_or_exceed_own_budget():
    writer = m.EvidenceGitWriter('EXPLICIT_TEST_TOKEN')
    writer.calls = 128
    with pytest.raises(ValueError, match='BUDGET'):
        writer.call('GET','/git/ref/heads/main')


def test_transport_rejects_token_in_payload_before_request():
    writer = m.EvidenceGitWriter('EXPLICIT_TEST_TOKEN')
    with pytest.raises(ValueError, match='CREDENTIAL'):
        writer.call('POST','/git/blobs',{'content':'EXPLICIT_TEST_TOKEN'})
