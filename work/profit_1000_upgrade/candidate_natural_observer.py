"""Immediately verify and preserve the exact natural-freeze publication evidence.

Separate research paths only. Local/captured status or a Git ACK is not a
cross-day admission certificate; the successful observer job must be checked.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).absolute().parents[2]
WORKFLOW_PATH = '.github/workflows/research_candidate_natural_observer.yml'
CAPTURE_SHA = 'd40677e35ecbb8c255c1b039e0eee03b66e97eaa2dccc973ac69b514c0de7dd3'
WRITER_SHA = 'e876d5865d72e8dddef1e920a68dfdf9d24e26d7b647b96672a7ff89c6358a76'
SELF_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
CONTEXT_SCHEMA = 'dc20_candidate_natural_observer_context_v1'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def dependencies():
    for name, expected in ((Path(__file__).name, SELF_SHA), ('candidate_natural_evidence.py', CAPTURE_SHA),
            ('candidate_natural_evidence_git.py', WRITER_SHA)):
        path = ROOT / 'work/profit_1000_upgrade' / name
        require(expected != '0'*64 and not any(p.is_symlink() for p in (path, *path.parents))
            and path.is_file() and path.stat().st_nlink == 1 and digest(path.read_bytes()) == expected,
            'REVIEWED_OBSERVER_CODE_REQUIRED')
    from work.profit_1000_upgrade import candidate_natural_evidence as capture
    from work.profit_1000_upgrade import candidate_natural_evidence_git as writer
    require(Path(capture.__file__).absolute() == ROOT / 'work/profit_1000_upgrade/candidate_natural_evidence.py'
        and Path(writer.__file__).absolute() == ROOT / 'work/profit_1000_upgrade/candidate_natural_evidence_git.py',
        'EXACT_OBSERVER_IMPORT_ORIGIN_REQUIRED')
    capture.code_guard()
    return capture, writer


def execution_context():
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('GITHUB_REPOSITORY') == 'njedu2023-prog/DC20'
        and os.environ.get('GITHUB_REF') == 'refs/heads/main' and os.environ.get('GITHUB_RUN_ATTEMPT') == '1',
        'FIRST_MAIN_OBSERVER_ACTION_REQUIRED')
    require(re.fullmatch('[1-9][0-9]*', os.environ.get('GITHUB_RUN_ID',''))
        and re.fullmatch('[0-9a-f]{40}', os.environ.get('GITHUB_SHA','')), 'EXACT_OBSERVER_RUN_AND_CODE_REQUIRED')
    return {'observer_workflow_path': WORKFLOW_PATH, 'observer_run_id': int(os.environ['GITHUB_RUN_ID']),
        'run_attempt': 1, 'code_head_sha': os.environ['GITHUB_SHA'], 'repository': 'njedu2023-prog/DC20', 'branch': 'main'}


def prepare_evidence_files(captured, context, *, now):
    capture, writer = dependencies()
    require(captured.get('status') == 'LOCAL_UNPUBLISHED_OBSERVATION' and captured.get('test_transport_injected') is False,
        'ACTUAL_SAME_PROCESS_CAPTURE_REQUIRED')
    verified = capture.verify_local_evidence(captured['output_root'], expected_manifest_sha256=captured['manifest_sha256'])
    require(all(verified[k] == captured[k] for k in ('signal_date','snapshot_file_sha256','publication_observation_sha256',
        'manifest_path','manifest_sha256','files','output_root','test_transport_injected')), 'CAPTURE_CHANGED')
    manifest = verified['manifest']
    observation_raw = capture.gh.read(Path(captured['output_root']) / manifest['native_observation']['path'], capture.MAX_FILE_BYTES)[0]
    observation = capture.gh.parse_json(observation_raw)
    require(observation['research_prospective_publication_observed'] is True, 'ORIGINAL_PUBLICATION_NOT_OBSERVED')
    auction = datetime.strptime(observation['exec_date']+'0925', '%Y%m%d%H%M').replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
    deadline = auction - timedelta(minutes=5)
    require(now.tzinfo is not None and now < deadline, 'OBSERVER_T0920_SAFETY_DEADLINE')
    require(context.get('repository') == writer.REPO and context.get('branch') == 'main'
        and context.get('observer_workflow_path') == WORKFLOW_PATH and type(context.get('observer_run_id')) is int
        and context['observer_run_id'] > 0 and type(context.get('run_attempt')) is int and context['run_attempt'] == 1,
        'EXACT_OBSERVER_CONTEXT_REQUIRED')
    writer.sha40(context['code_head_sha'])
    bound_context = {**context, 'schema_version': CONTEXT_SCHEMA, 'signal_date': captured['signal_date'],
        'freeze_run_id': int(manifest['freeze_run_id']), 'snapshot_file_sha256': captured['snapshot_file_sha256'],
        'manifest_sha256': captured['manifest_sha256'], 'publication_observation_sha256': captured['publication_observation_sha256'],
        'capture_module_sha256': CAPTURE_SHA, 'coordinator_sha256': SELF_SHA, 'writer_sha256': WRITER_SHA,
        'created_at_host_utc': now.astimezone(timezone.utc).isoformat(),
        'original_prospective_publication_observed': True, 'evidence_natural_admission_issued': False,
        'production_activation_allowed': False, 'actual_execution_claimed': False}
    prefix = writer.PREFIX + captured['signal_date'] + '/'
    files = {}
    for binding in captured['files']:
        raw = capture.gh.read(Path(captured['output_root']) / binding['path'], capture.MAX_FILE_BYTES)[0]
        require(digest(raw) == binding['sha256'] and len(raw) == binding['bytes'], 'ORIGINAL_CAPSULE_FILE_CHANGED')
        files[prefix + binding['path']] = raw
    files[prefix+'context.json'] = writer.encoded(bound_context)
    writer.validate_files(files)
    require(capture.verify_local_evidence(captured['output_root'], expected_manifest_sha256=captured['manifest_sha256']) == verified,
        'CAPTURE_CHANGED_DURING_PREPARATION')
    return files, deadline


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze-run-id', required=True)
    parser.add_argument('--work-parent', type=Path, required=True)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args(argv)
    capture, writer = dependencies()
    context = execution_context()
    capture.gh.exact_id(args.freeze_run_id)
    parent = capture.gh.path(args.work_parent, directory=True)
    require(parent != ROOT and ROOT not in parent.parents, 'ISOLATED_WORK_PARENT_REQUIRED')
    token = os.environ.get(capture.gh.TOKEN_ENV, '')
    work = Path(tempfile.mkdtemp(prefix='dc20-candidate-observer-', dir=parent))
    captured = capture.capture_evidence(work/'capsule', freeze_run_id=args.freeze_run_id, token=token)
    files, deadline = prepare_evidence_files(captured, context, now=datetime.now(timezone.utc))
    def retain(name, value):
        raw = writer.encoded(value)
        require(token.encode() not in raw and json.dumps(token)[1:-1].encode() not in raw, 'CREDENTIAL_OUTPUT_REJECTED')
        with (work/name).open('xb') as handle:
            handle.write(raw)
    # Keep context outside the integrity-checked capsule's exact inventory.
    retain('context.json', json.loads(files[writer.PREFIX+captured['signal_date']+'/context.json']))
    retain('capture.json', captured)
    def guard():
        require(dependencies() == (capture, writer), 'OBSERVER_CODE_CHANGED')
        fresh, fresh_deadline = prepare_evidence_files(captured, context,
            now=datetime.fromisoformat(json.loads(files[writer.PREFIX+captured['signal_date']+'/context.json'])['created_at_host_utc']))
        require(fresh == files and fresh_deadline == deadline, 'OBSERVER_PAYLOAD_CHANGED')
    result = writer.publish_new_evidence(files, github_client=writer.EvidenceGitWriter(token), deadline=deadline, pre_cas_guard=guard) if args.publish else {
        'status': 'DRY_RUN_LOCAL_EVIDENCE_ONLY', 'remote_writes': 0, 'natural_forward_admission_issued': False, 'production_activation_allowed': False}
    retain('publication.json', result)
    print(json.dumps({'status': result['status'], 'signal_date': captured['signal_date'], 'work_root': str(work),
        'evidence_commit': result.get('commit_sha'), 'observer_run_id': context['observer_run_id'], 'production_activation_allowed': False}))
    return 1 if result['status'] == 'LATE_EVIDENCE_NOT_ADMITTED' else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print('NATURAL_OBSERVER_BLOCKED; retained local evidence is not admitted')
        raise SystemExit(1)
