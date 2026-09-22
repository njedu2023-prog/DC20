"""One explicit P0 run -> original sources -> fixed research snapshot -> Git CAS.

No SSH/push, market requests, fitting, formal ledger or frontend writes. The
CLI obtains source provenance in this process, not from a caller-made receipt.
Publication acknowledgement is not independent prospective-result admission;
the successful workflow/job timing must still be observed at settlement.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import urllib.request
import urllib.error

ROOT = Path(__file__).absolute().parents[2]
REPO = 'njedu2023-prog/DC20'
API = '/repos/' + REPO
PREFIX = 'work/profit_1000_upgrade/candidate_natural_forward/'
MODEL_PATH = ROOT / 'work/profit_1000_upgrade/candidate_natural_model/evaluation.json'
RUNNER_SHA = '5a3967c88829be0e6b9a0d7c384b6d2cf12ac7c257bac4dff40d1a15ce2d5c15'
# Independently reviewed original-source importer, including no unknown-file reads.
IMPORTER_SHA = '6adc11beaef7c5b940043a4063dc812f6f56c169c71ecaafd9c9be6364729cb4'
SELF_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(obj):
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def git_blob(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def sha40(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{40}', value), 'EXACT_GIT_SHA_REQUIRED')
    return value


def dependencies():
    own = Path(__file__).absolute()
    require(own.is_file() and own.stat().st_nlink == 1 and not any(p.is_symlink() for p in (own, *own.parents))
        and digest(own.read_bytes()) == SELF_SHA, 'WORKFLOW_SELF_SOURCE_CHANGED')
    for name, sha in (('candidate_natural_forward.py', RUNNER_SHA), ('candidate_natural_p0_github.py', IMPORTER_SHA)):
        p = ROOT / 'work/profit_1000_upgrade' / name
        require(sha != '0' * 64 and not p.is_symlink() and digest(p.read_bytes()) == sha,
                'REVIEWED_WORKFLOW_DEPENDENCY_REQUIRED')
    from work.profit_1000_upgrade import candidate_natural_forward as runner
    from work.profit_1000_upgrade import candidate_natural_p0_github as importer
    require(Path(runner.__file__).absolute() == ROOT / 'work/profit_1000_upgrade/candidate_natural_forward.py'
        and Path(importer.__file__).absolute() == ROOT / 'work/profit_1000_upgrade/candidate_natural_p0_github.py',
        'WORKFLOW_IMPORT_ORIGIN_CHANGED')
    return runner, importer


def publication_guard():
    runner, importer = dependencies()
    runner._guard()
    importer.code_guard()


def aware(value):
    require(type(value) is str, 'AWARE_TIME_REQUIRED')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(result.tzinfo is not None, 'AWARE_TIME_REQUIRED')
    return result.astimezone(timezone.utc)


def execution_context():
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('GITHUB_REPOSITORY') == REPO
        and os.environ.get('GITHUB_REF') == 'refs/heads/main', 'MAIN_GITHUB_ACTIONS_CONTEXT_REQUIRED')
    for key in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'):
        require(re.fullmatch('[1-9][0-9]*', os.environ.get(key, '')), 'EXACT_WORKFLOW_ID_REQUIRED')
    require(os.environ['GITHUB_RUN_ATTEMPT'] == '1', 'FIRST_ATTEMPT_REQUIRED')
    return {'repository': REPO, 'run_id': int(os.environ['GITHUB_RUN_ID']), 'run_attempt': 1,
        'code_head_sha': sha40(os.environ.get('GITHUB_SHA')), 'branch': 'main',
        'workflow_path': '.github/workflows/research_candidate_natural_forward.yml'}


def prepare_publication(snapshot_raw, local_receipt, imported, context, *, now):
    """Validate trusted same-process returns and keep their original bytes."""
    runner, importer = dependencies()
    record = runner._json(snapshot_raw)
    runner._sealed(record)
    plan, plan_sha, _ = runner.registration()
    day = runner.scorer._date(record['signal_date'], 'signal_date')
    require(day >= '20260914' and record['runner_sha256'] == RUNNER_SHA
        and record['registration_sha256'] == plan_sha
        and record['model_canonical_sha256'] == runner.MODEL_SHA
        and record['model_evaluation']['sha256'] == runner.EVALUATION_SHA,
        'EXACT_REGISTERED_NEW_MODEL_SNAPSHOT_REQUIRED')
    require(all(record.get(k) == v and type(record.get(k)) is type(v) for k, v in runner.BOUNDARIES.items()),
        'RESEARCH_BOUNDARIES_CHANGED')
    require(imported['schema_version'] == importer.SCHEMA
        and imported['status'] == 'IMPORTED_ORIGINAL_P0_BYTES_REQUIRES_D_ADAPTER'
        and imported['injected_client_for_test'] is False, 'ACTUAL_IN_PROCESS_GITHUB_SOURCE_IMPORT_REQUIRED')
    require([record[k] for k in ('signal_date', 'exec_date', 'exit_date')]
        == [imported[k] for k in ('signal_date', 'exec_date', 'exit_date')], 'P0_D_T_T1_MISMATCH')
    require(record['input_arguments']['expected_p0_sha256'] == imported['expected_p0_sha256']
        and record['input_arguments']['source_root'] == imported['source_root'], 'P0_SOURCE_ARGUMENTS_CHANGED')
    original = imported['source_file_bindings']
    projected = record['D_source_evidence']['source_file_bindings']
    require(type(original) is list and type(projected) is list and len(original) == len(projected) == 28,
        'EXACT_28_SOURCE_ROWS_REQUIRED')
    require(all(type(b) is dict and set(b) == {'path', 'sha256', 'git_blob_sha1', 'git_mode', 'bytes'}
        and b['git_mode'] == '100644' for b in original), 'EXACT_ORIGINAL_SOURCE_BINDING_SHAPE')
    require(all(type(b) is dict and set(b) == {'receipt_path', 'origin_path', 'sha256', 'bytes', 'explicit_original_blob_mapping'}
        for b in projected), 'EXACT_PROJECTED_SOURCE_BINDING_SHAPE')
    expected = {b['path']: (b['sha256'], b['bytes']) for b in original}
    actual = {b['receipt_path']: (b['sha256'], b['bytes']) for b in projected}
    require(len(expected) == len(actual) == 28 and actual == expected, 'EXACT_28_ORIGINAL_SOURCE_BINDINGS_REQUIRED')
    require(local_receipt['status'] == 'LOCAL_RESEARCH_SNAPSHOT_FROZEN'
        and local_receipt['snapshot_file_sha256'] == digest(snapshot_raw)
        and local_receipt['snapshot_sha256'] == record['snapshot_sha256']
        and local_receipt['new_snapshot_written'] is True
        and local_receipt['local_freeze_completed_before_cutoff'] is True
        and local_receipt['clock_mode'] == record['clock_mode'] == 'HOST_SYSTEM_UTC', 'TIMELY_NEW_HOST_CLOCK_SNAPSHOT_REQUIRED')
    generated, frozen, finished = map(aware, (record['prediction_generated_at_utc'],
        record['pre_cas_freeze_at_utc'], local_receipt['local_operation_completed_at_utc']))
    close, auction = runner._window(day, record['exec_date'])
    cutoff = auction - timedelta(minutes=10)
    require(close <= aware(imported['observed_p0_cas_job_completed_at']) <= generated <= frozen <= finished <= now < cutoff,
        'NEW_PUBLICATION_T0915_SAFETY_WINDOW_REQUIRED')
    require(type(context) is dict and context['repository'] == REPO and context['branch'] == 'main'
        and context['run_attempt'] == 1 and type(context['run_id']) is int and context['run_id'] > 0,
        'EXACT_PUBLISHING_WORKFLOW_CONTEXT_REQUIRED')
    sha40(context['code_head_sha'])
    files = {'day_' + day + '.json': snapshot_raw,
        'p0_sources_' + day + '.json': encoded(imported),
        'local_freeze_' + day + '.json': encoded(local_receipt),
        'workflow_' + day + '.json': encoded({**context, 'signal_date': day,
            'snapshot_file_sha256': digest(snapshot_raw), 'source_import_receipt_sha256': digest(encoded(imported)),
            'local_freeze_receipt_sha256': digest(encoded(local_receipt)), 'publication_timing_verified': False,
            'production_activation_allowed': False, 'independent_job_timing_check_still_required': True})}
    return {PREFIX + path: raw for path, raw in files.items()}, cutoff


class GitHubWriter:
    """Bounded repository Git API only; never forwards credentials or errors."""
    def __init__(self, token):
        require(type(token) is str and token and not any(c.isspace() for c in token), 'DEDICATED_TOKEN_REQUIRED')
        self.token = token
        self.calls = 0
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        self.opener = urllib.request.build_opener(NoRedirect())

    def call(self, method, suffix, payload=None):
        allowed = ((method == 'GET' and (suffix == '/git/ref/heads/main' or re.fullmatch(r'/git/(commits|trees)/[0-9a-f]{40}(\?recursive=1)?', suffix)))
            or (method == 'POST' and suffix in ('/git/blobs', '/git/trees', '/git/commits'))
            or (method == 'PATCH' and suffix == '/git/refs/heads/main'))
        require(allowed, 'NON_GIT_REPOSITORY_WRITE_FORBIDDEN')
        self.calls += 1
        require(self.calls <= 30, 'GIT_API_BUDGET_EXCEEDED')
        body = None if payload is None else encoded(payload)
        require(body is None or self.token.encode() not in body, 'CREDENTIAL_IN_PAYLOAD_REJECTED')
        request = urllib.request.Request('https://api.github.com' + API + suffix, data=body, method=method,
            headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                'Content-Type': 'application/json', 'User-Agent': 'DC20-natural-research-CAS',
                'X-GitHub-Api-Version': '2022-11-28'})
        try:
            with self.opener.open(request, timeout=20) as response:
                require(response.status in (200, 201), 'GIT_API_STATUS_REJECTED')
                raw = response.read(8_000_001)
                require(len(raw) <= 8_000_000 and self.token.encode() not in raw, 'GIT_RESPONSE_REJECTED')
                return json.loads(raw)
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ValueError('GIT_API_FAILED_NO_FORCE_OR_AUTOMATIC_RETRY') from None


def publish_exact_new_day(files, *, github_client, deadline, clock=lambda: datetime.now(timezone.utc), pre_cas_guard=None):
    """One non-force Git CAS; conflicts never retime, overwrite or retry."""
    require(type(files) is dict and len(files) == 4, 'FOUR_EXACT_D_FILES_REQUIRED')
    dates = set()
    for path, raw in files.items():
        m = re.fullmatch(re.escape(PREFIX) + r'(day|p0_sources|local_freeze|workflow)_([0-9]{8})\.json', path)
        require(m is not None and type(raw) is bytes and 0 < len(raw) <= 4_000_000, 'SCOPED_RESEARCH_FILE_REQUIRED')
        dates.add(m[2])
    require(len(dates) == 1 and min(dates) >= '20260914', 'ONE_NEW_NATURAL_D_REQUIRED')
    datetime.strptime(next(iter(dates)), '%Y%m%d')
    sealed_files = tuple(sorted(files.items()))
    require(clock() < deadline, 'PUBLICATION_DEADLINE_PASSED')
    head = sha40(github_client.call('GET', '/git/ref/heads/main')['object']['sha'])
    commit = github_client.call('GET', '/git/commits/' + head)
    require(commit['sha'] == head, 'REMOTE_PARENT_CHANGED')
    tree_sha = sha40(commit['tree']['sha'])
    tree = github_client.call('GET', '/git/trees/' + tree_sha + '?recursive=1')
    require(tree['sha'] == tree_sha and tree['truncated'] is False, 'COMPLETE_PARENT_TREE_REQUIRED')
    entries = {x['path']: x for x in tree['tree']}
    require(len(entries) == len(tree['tree']) and not any(p in entries for p in files), 'D_ALREADY_FROZEN_OR_PARTIAL_KEEP_ORIGINAL')
    additions = []
    for path, raw in sorted(files.items()):
        result = github_client.call('POST', '/git/blobs', {'encoding': 'base64', 'content': base64.b64encode(raw).decode()})
        require(result['sha'] == git_blob(raw), 'CREATED_BLOB_BYTES_DIFFER')
        additions.append({'path': path, 'mode': '100644', 'type': 'blob', 'sha': result['sha']})
    built = github_client.call('POST', '/git/trees', {'base_tree': tree_sha, 'tree': additions})
    new_tree = sha40(built['sha'])
    verify = github_client.call('GET', '/git/trees/' + new_tree + '?recursive=1')
    require(verify['sha'] == new_tree and verify['truncated'] is False, 'COMPLETE_NEW_TREE_REQUIRED')
    old_blobs = {p: (x['mode'], x['type'], x['sha']) for p, x in entries.items() if x['type'] != 'tree'}
    parents = {str(parent) for path in files for parent in PurePosixPath(path).parents if str(parent) != '.'}
    new_entries = {x['path']: x for x in verify['tree']}
    require(set(new_entries) == set(entries) | set(files) | parents,
        'UNEXPECTED_TREE_DIRECTORY_STRUCTURE')
    require(all(new_entries[p]['type'] == 'tree' and new_entries[p]['mode'] == '040000' for p in parents),
        'RESEARCH_PARENT_NOT_DIRECTORY')
    require(all(new_entries[p] == item for p, item in entries.items() if item['type'] == 'tree' and p not in parents),
        'UNRELATED_TREE_CHANGED')
    wanted = {**old_blobs, **{x['path']: (x['mode'], x['type'], x['sha']) for x in additions}}
    got = {x['path']: (x['mode'], x['type'], x['sha']) for x in verify['tree'] if x['type'] != 'tree'}
    require(got == wanted and len({x['path'] for x in verify['tree']}) == len(verify['tree']), 'UNEXPECTED_GIT_TREE_CHANGE')
    made = github_client.call('POST', '/git/commits', {'message': 'research: freeze fixed candidate D ' + next(iter(dates)),
        'tree': new_tree, 'parents': [head]})
    new_head = sha40(made['sha'])
    require(made['tree']['sha'] == new_tree and [x['sha'] for x in made['parents']] == [head], 'NEW_COMMIT_PARENT_OR_TREE_CHANGED')
    require(github_client.call('GET', '/git/ref/heads/main')['object']['sha'] == head, 'MAIN_MOVED_KEEP_EXISTING')
    if pre_cas_guard is not None:
        pre_cas_guard()
    require(tuple(sorted(files.items())) == sealed_files, 'PUBLICATION_PAYLOAD_CHANGED')
    require(clock() < deadline, 'DEADLINE_PASSED_BEFORE_CAS')
    moved = github_client.call('PATCH', '/git/refs/heads/main', {'sha': new_head, 'force': False})
    require(moved['object']['sha'] == new_head, 'UNCERTAIN_CAS_REQUIRES_READ_ONLY_RECONCILIATION')
    require(tuple(sorted(files.items())) == sealed_files, 'POST_CAS_PAYLOAD_CHANGED_REQUIRES_READ_ONLY_RECONCILIATION')
    if pre_cas_guard is not None:
        pre_cas_guard()
    acknowledged = clock()
    return {'schema_version': 'dc20_natural_candidate_git_publication_ack_v1',
        'status': 'GIT_PUBLICATION_ACKNOWLEDGED' if acknowledged < deadline else 'LATE_PUBLICATION_NOT_ADMITTED',
        'signal_date': next(iter(dates)), 'commit_sha': new_head, 'parent_sha': head, 'tree_sha': new_tree,
        'files': [{'path': p, 'sha256': digest(raw), 'git_blob_sha1': git_blob(raw), 'bytes': len(raw)} for p, raw in sealed_files],
        'acknowledged_at_host_utc': acknowledged.isoformat(), 'timestamp_basis': 'HOST_CLOCK_AFTER_GITHUB_NONFORCE_REF_ACK',
        'independent_job_timing_check_still_required': True, 'natural_forward_admission_issued': False,
        'production_activation_allowed': False, 'actual_execution_claimed': False, 'existing_files_modified': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--p0-run-id', required=True)
    parser.add_argument('--work-parent', type=Path, required=True)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args(argv)
    runner, importer = dependencies()
    context = execution_context()
    importer.exact_id(args.p0_run_id)
    parent = runner._path(args.work_parent)
    require(parent.is_dir() and parent != ROOT and ROOT not in parent.parents, 'ISOLATED_EXISTING_WORK_PARENT_REQUIRED')
    token = os.environ.get(importer.TOKEN_ENV, '')
    client = importer.GitHubReadClient(token)
    work = Path(tempfile.mkdtemp(prefix='dc20-candidate-natural-', dir=parent))
    sources = work / 'sources'
    sources.mkdir()
    imported = importer.import_p0_sources(sources, expected_run_id=args.p0_run_id, github_client=client)
    local = runner.freeze_natural_day(sources, work / 'candidate_natural_forward', MODEL_PATH,
        signal_date=imported['signal_date'], expected_p0_sha256=imported['expected_p0_sha256'])
    snapshot_raw = runner._read(Path(local['snapshot_path']))[0]
    files, cutoff = prepare_publication(snapshot_raw, local, imported, context, now=datetime.now(timezone.utc))
    require(dependencies() == (runner, importer), 'WORKFLOW_DEPENDENCIES_CHANGED')
    def retain(name, data):
        body = encoded(data)
        require(token.encode() not in body, 'CREDENTIAL_IN_OUTPUT_REJECTED')
        with (work / name).open('xb') as handle:
            handle.write(body)
    # Keep original observed provenance even when a later Git CAS is ambiguous
    # or rejected. These are local source/freeze receipts, never publication proof.
    retain('p0_source_receipt.json', imported)
    retain('local_freeze_receipt.json', local)
    # Only the actual same-process source import reaches a repository write.
    result = (publish_exact_new_day(files, github_client=GitHubWriter(token), deadline=cutoff, pre_cas_guard=publication_guard)
        if args.publish else {'status': 'DRY_RUN_LOCAL_RESEARCH_ONLY', 'remote_writes': 0,
            'natural_forward_admission_issued': False, 'production_activation_allowed': False})
    retain('publication.json', result)
    print(json.dumps({'status': result['status'], 'signal_date': imported['signal_date'], 'work_root': str(work),
        'commit_sha': result.get('commit_sha'), 'production_activation_allowed': False}, ensure_ascii=False))
    return 1 if result['status'] == 'LATE_PUBLICATION_NOT_ADMITTED' else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError):
        # No tokens, signed locations, provider messages or traceback leakage.
        print('CANDIDATE_NATURAL_WORKFLOW_BLOCKED; retained local evidence is not admitted')
        raise SystemExit(1)
