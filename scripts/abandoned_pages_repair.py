"""Manual-only, evidence-bound repair of one abandoned Pages backend.

No workflow cancellation, deployment creation, data generation, or Git writes.
This draft supports only the reviewed 2026-11-09 duplicate-ID incident.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from scripts import research_pages_handoff as handoff
from scripts import dc20_writer_queue as queue

REPO = 'njedu2023-prog/DC20'
WORKFLOW = 'repair_abandoned_pages.yml'
WORKFLOW_PATH = '.github/workflows/' + WORKFLOW
WORKFLOW_NAME = 'DC20 · Repair reviewed abandoned Pages backend'
DEPLOYMENT = '408f72835f715c5470b3f9f3132e63ca7cd04136'
SOURCE = '75db1ba45df45d4d5dc8940ea84ae1a572374f45'
TARGET = '5262085565389a4948642b41bd7c24618a723dfa'
PRODUCER = 42626848568
BUSY_RUN = 42629243751
DAY = '20261109'
CONFIRM = 'CANCEL_REVIEWED_ABANDONED_BACKEND_' + DEPLOYMENT
REASON = 'ABANDONED_DUPLICATE_ID_WITH_BUSY_PROOF'
EVIDENCE_PATH = 'docs/abandoned_pages_20261109.json'
INCIDENT_EVIDENCE_SHA256 = '79959a745e87f0cd867993f7639e090126ff1a843387f0aef882baea2a21db42'
MAX_BYTES = 128 * 1024 * 1024
PUBLIC = 'https://njedu2023-prog.github.io/DC20/'
PINNED_DEPLOY_SHA256 = '36a983c3ebebceb20943ae0074e10f60b46cdd7d0c27126ee3fd86d1e06da64a'


def require(value, reason):
    if not value:
        raise ValueError(reason)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class Client(handoff.Client):
    """GET only, except one hard-coded original backend cancel endpoint."""
    def __init__(self, token):
        super().__init__(token)
        self.cancel_attempted = False

    def request(self, path, body=None):
        require(body is None, 'NON_CANCEL_MUTATION_REJECTED')
        return super().request(path)

    def raw(self, path):
        require(path.startswith('/') and not path.startswith('//'), 'API_PATH_INVALID')
        url = 'https://api.github.com/repos/' + REPO + path
        headers = {'Authorization': 'Bearer ' + self.token,
                   'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2026-03-10'}
        opener = urllib.request.build_opener(NoRedirect)
        for _ in range(4):
            try:
                with opener.open(urllib.request.Request(url, headers=headers), timeout=60) as response:
                    data = response.read(MAX_BYTES + 1)
                require(len(data) <= MAX_BYTES, 'DOWNLOAD_TOO_LARGE')
                return data
            except urllib.error.HTTPError as exc:
                if exc.code not in (301, 302, 303, 307, 308):
                    raise ValueError('GITHUB_HTTP_' + str(exc.code)) from None
                url = exc.headers.get('Location', '')
                parsed = urllib.parse.urlsplit(url)
                require(parsed.scheme == 'https' and parsed.hostname and not parsed.username
                        and (parsed.hostname.endswith('.blob.core.windows.net')
                             or parsed.hostname.endswith('.actions.githubusercontent.com')
                             or parsed.hostname in {'github.com', 'api.github.com'}),
                        'UNTRUSTED_DOWNLOAD_REDIRECT')
                # Never forward an API bearer token to a signed download URL.
                headers = {}
            except (TimeoutError, urllib.error.URLError):
                raise ValueError('GITHUB_READ_UNAVAILABLE') from None
        raise ValueError('TOO_MANY_DOWNLOAD_REDIRECTS')

    def cancel_once(self):
        require(not self.cancel_attempted, 'CANCEL_ALREADY_ATTEMPTED')
        self.cancel_attempted = True  # Set before the request, including timeouts.
        request = urllib.request.Request(
            'https://api.github.com/repos/' + REPO + '/pages/deployments/' + DEPLOYMENT + '/cancel',
            data=b'', method='POST', headers={'Authorization': 'Bearer ' + self.token,
                'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2026-03-10'})
        try:
            # No redirects, retry adapters or repeat POSTs, even for 404/5xx.
            with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
                code, raw = response.status, response.read(1)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            raise ValueError('CANCEL_OUTCOME_NOT_CONFIRMED_DO_NOT_RETRY') from None
        require(code == 204 and raw == b'', 'CANCEL_OUTCOME_NOT_CONFIRMED_DO_NOT_RETRY')
        return {'status': 'CANCEL_REQUEST_ACCEPTED_204', 'deployment_id': DEPLOYMENT,
                'publication_complete': False, 'do_not_repeat_cancel': True}


def artifact_files(raw):
    """Read ZIP containing artifact.tar without extracting paths to disk."""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        require(archive.namelist() == ['artifact.tar'], 'ARTIFACT_ZIP_SHAPE_INVALID')
        info = archive.getinfo('artifact.tar')
        require(info.file_size <= MAX_BYTES, 'ARTIFACT_TOO_LARGE')
        tar_raw = archive.read(info)
    files = {}
    with tarfile.open(fileobj=io.BytesIO(tar_raw), mode='r:*') as archive:
        size = 0
        for member in archive:
            name = member.name.removeprefix('./')
            require(not name.startswith('/') and '..' not in name.split('/'), 'UNSAFE_ARTIFACT_PATH')
            if member.isdir():
                continue
            require(member.isfile() and name not in files, 'UNSAFE_ARTIFACT_MEMBER')
            size += member.size
            require(size <= MAX_BYTES, 'ARTIFACT_TOO_LARGE')
            files[name] = archive.extractfile(member).read()
    return files


def checked_artifact(client, spec):
    metadata = client.request('/actions/artifacts/' + str(spec['id']))
    require(metadata.get('id') == spec['id'] and metadata.get('name') == spec['name']
            and metadata.get('expired') is False
            and metadata.get('workflow_run', {}).get('id') == PRODUCER
            and metadata.get('workflow_run', {}).get('head_sha') == DEPLOYMENT
            and metadata.get('digest') == 'sha256:' + spec['zip_sha256'], 'ARTIFACT_IDENTITY_MISMATCH')
    raw = client.raw('/actions/artifacts/' + str(spec['id']) + '/zip')
    require(digest(raw) == spec['zip_sha256'], 'ARTIFACT_BYTES_MISMATCH')
    files = artifact_files(raw)
    revision = json.loads(files['revision.json'])
    require(revision.get('head_sha') == spec['head'] and revision.get('run_id') == PRODUCER
            and revision.get('run_attempt') == 1 and revision.get('signal_date') == DAY
            and revision.get('primary_d_signal_date') == DAY
            and revision.get('primary_d_exec_date') == '20261110'
            and revision.get('primary_d_exit_date') == '20261111'
            and revision.get('primary_d_status') == 'READY', 'ARTIFACT_REVISION_MISMATCH')
    return files


def terminal_jobs(client, run_id):
    jobs = handoff.all_items(client, '/actions/runs/' + str(run_id) + '/jobs', 'jobs')
    require(jobs and all(j.get('run_id') == run_id and j.get('status') == 'completed'
                        and j.get('completed_at') for j in jobs), 'ORIGINAL_JOB_STILL_ACTIVE')
    return {job['id']: job for job in jobs}


def checked_logs(client, jobs, specs):
    for spec in specs:
        job = jobs.get(spec['job_id'])
        require(job and job.get('name') == spec['name']
                and job.get('conclusion') == spec['conclusion'], 'ORIGINAL_JOB_IDENTITY_MISMATCH')
        raw = client.raw('/actions/jobs/' + str(spec['job_id']) + '/logs')
        require(digest(raw) == spec['sha256'], 'ORIGINAL_LOG_BYTES_MISMATCH')
        text = raw.decode('utf-8')
        require(all(marker in text for marker in spec['required_literals']), 'ORIGINAL_LOG_PROOF_MISSING')


def no_other_work(client, self_id, expected_head):
    # Holding the same dc20-pages lock prevents a new normal deployment passing us.
    groups, total = queue.collection(client.request('/actions/concurrency_groups?per_page=100'),
                                     'concurrency_groups')
    require(len(groups) == total and len({x.get('group_name') for x in groups}) == total,
            'CONCURRENCY_LIST_INCOMPLETE')
    names = {x.get('group_name') for x in groups}
    require('dc20-pages' in names, 'REPAIR_MUST_HOLD_EXISTING_PAGES_LOCK')
    group = client.request('/actions/concurrency_groups/dc20-pages')
    members, count = queue.collection(group, 'group_members')
    require(group.get('group_name') == 'dc20-pages' and len(members) == count and count > 0
            and all(m.get('run_id') == self_id and m.get('status') == 'in_progress' for m in members),
            'PAGES_QUEUE_NOT_EXCLUSIVE')
    ignored = []
    live = {}
    for status in queue.ACTIVE:
        for row in handoff.all_items(client, '/actions/runs?status=' + status, 'workflow_runs'):
            live[row['id']] = row
    for observed in live.values():
        run_id = observed['id']
        if run_id == self_id:
            continue
        current = client.request('/actions/runs/' + str(run_id))
        workflow = queue.identity(current, run_id)
        jobs = handoff.all_items(client, '/actions/runs/' + str(run_id) + '/jobs', 'jobs')
        if current.get('status') == 'completed' and all(j.get('status') == 'completed' for j in jobs):
            continue
        # Only the previously understood old non-Pages observer orphan may be ignored.
        if (workflow == 'verify_decision_observations.yml' and not jobs
                and queue.old_queued(current, DAY, datetime.now(timezone.utc))
                and queue.no_run_lease(client, run_id)):
            again = client.request('/actions/runs/' + str(run_id))
            require(queue.unchanged_run(current, again) and again.get('status') == 'queued',
                    'ORPHAN_CHANGED_RECHECK')
            ignored.append(run_id)
            continue
        # Deliberately do NOT exempt a current guard: it may schedule a deploy later.
        raise ValueError('OTHER_LIVE_RUN_OR_JOB_' + str(run_id))
    require(handoff.current_head(client) == expected_head, 'MAIN_MOVED_DURING_QUEUE_CHECK')
    return ignored


def original_identity(client, root, expected_head, evidence):
    producer = handoff.source_run(client, PRODUCER)
    require(producer.get('head_sha') == DEPLOYMENT and producer.get('event') == 'workflow_run'
            and producer.get('status') == 'completed' and producer.get('conclusion') == 'failure',
            'PRODUCER_NOT_TERMINAL_FAILED')
    producer_jobs = terminal_jobs(client, PRODUCER)
    require(set(producer_jobs) == set(evidence['producer_job_ids']), 'PRODUCER_JOB_SET_CHANGED')
    require({j['id'] for j in producer_jobs.values() if j.get('conclusion') == 'failure'}
            == {evidence['public_verify_job_id']}, 'PRODUCER_FAILURE_NOT_ONLY_PUBLIC_VERIFY')
    require(producer['_research_job']['id'] == evidence['source_cas_job_id'], 'SOURCE_JOB_MISMATCH')
    verify_job = producer_jobs[evidence['public_verify_job_id']]
    require([step.get('name') for step in verify_job.get('steps', [])
             if step.get('conclusion') == 'failure'] == ['Verify public DC2.0 Pages revision'],
            'SECOND_DEPLOY_HAS_OTHER_FAILURE')
    handoff.capsule(root, expected_head, DAY, SOURCE)
    require(handoff.source_cas(client, root, expected_head, SOURCE, producer) == TARGET,
            'ORIGINAL_SOURCE_CAS_MISMATCH')
    aliases = handoff.all_items(client, '/actions/runs?head_sha=' + DEPLOYMENT, 'workflow_runs')
    require(len(aliases) == 1 and aliases[0].get('id') == PRODUCER, 'BACKEND_HEAD_HAS_OTHER_RUNS')
    busy = client.request('/actions/runs/' + str(BUSY_RUN))
    require(busy.get('id') == BUSY_RUN and busy.get('head_sha') == TARGET
            and busy.get('head_branch') == 'main' and busy.get('run_attempt') == 1
            and busy.get('repository', {}).get('full_name') == REPO
            and busy.get('head_repository', {}).get('full_name') == REPO
            and busy.get('path') == '.github/workflows/deploy_dc20_pages.yml'
            and busy.get('status') == 'completed' and busy.get('conclusion') == 'failure',
            'BUSY_PROOF_RUN_MISMATCH')
    registration = client.request('/actions/workflows/deploy_dc20_pages.yml')
    require(registration.get('state') == 'active'
            and registration.get('path') == busy['path']
            and registration.get('id') == busy.get('workflow_id')
            and busy.get('event') == 'workflow_dispatch', 'BUSY_WORKFLOW_IDENTITY_MISMATCH')
    busy_jobs = terminal_jobs(client, BUSY_RUN)
    require(set(busy_jobs) == {129200940731}
            and [step.get('name') for step in busy_jobs[129200940731].get('steps', [])
                 if step.get('conclusion') == 'failure'] == ['Deploy DC2.0 Pages'],
            'BUSY_PROOF_HAS_OTHER_FAILURE')
    checked_logs(client, producer_jobs | busy_jobs, evidence['log_proofs'])
    primary = checked_artifact(client, evidence['artifacts'][0])
    research = checked_artifact(client, evidence['artifacts'][1])
    pointer_raw = research[handoff.delivery.PREFIX + 'latest.json']
    pointer = json.loads(pointer_raw)
    require(re.fullmatch(r'versions/[0-9a-f]{64}\.json', pointer.get('path', '')),
            'UNSAFE_RESEARCH_VERSION_PATH')
    version_raw = research[handoff.delivery.PREFIX + pointer['path']]
    handoff.delivery.validate_dataset(pointer, version_raw, DAY, SOURCE)
    require(pointer_raw == (root / handoff.delivery.PREFIX / 'latest.json').read_bytes()
            and version_raw == (root / handoff.delivery.PREFIX / pointer['path']).read_bytes(),
            'CURRENT_SOURCE_BYTES_CHANGED')
    return primary


def preserved_public(primary, read=handoff.delivery.get):
    require(read(PUBLIC + 'revision.json') == primary['revision.json'], 'PUBLIC_REVISION_CHANGED')
    revision = json.loads(primary['revision.json'])
    require(revision['head_sha'] == SOURCE, 'PRIMARY_SOURCE_MISMATCH')
    checked = ['revision.json']
    for key, fingerprint in revision.items():
        if not key.endswith('_sha256'):
            continue
        path = revision.get(key[:-7] + '_url')
        if not path:
            continue
        require(isinstance(path, str) and not path.startswith('/') and '..' not in path.split('/')
                and '://' not in path and path in primary, 'PUBLIC_FINGERPRINT_PATH_INVALID')
        require(digest(primary[path]) == fingerprint and read(PUBLIC + path) == primary[path],
                'PUBLIC_CORE_FINGERPRINT_CHANGED')
        checked.append(path)
    require(len(checked) >= 5, 'PUBLIC_CORE_PROOF_INCOMPLETE')
    receipt = revision['primary_d_receipt_url']
    require(read(PUBLIC + receipt) == primary[receipt], 'PUBLIC_P0_RECEIPT_CHANGED')
    return checked


def cancel_marker():
    return 'Repair abandoned Pages | cancel | id=' + DEPLOYMENT


def environment(client, env, expected_head, mode):
    require(env.get('GITHUB_ACTIONS') == 'true' and env.get('GITHUB_REPOSITORY') == REPO
            and env.get('GITHUB_REF') == 'refs/heads/main' and env.get('GITHUB_RUN_ATTEMPT') == '1'
            and env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
            and env.get('GITHUB_WORKFLOW_REF') == REPO + '/' + WORKFLOW_PATH + '@refs/heads/main'
            and env.get('GITHUB_SHA') == expected_head, 'REPAIR_ENVIRONMENT_REJECTED')
    self_id = int(env['GITHUB_RUN_ID'])
    registration = client.request('/actions/workflows/' + WORKFLOW)
    require(registration.get('state') == 'active' and registration.get('path') == WORKFLOW_PATH
            and registration.get('name') == WORKFLOW_NAME, 'REPAIR_REGISTRATION_INVALID')
    actual = client.request('/actions/runs/' + str(self_id))
    handoff.validate_run(actual, path=WORKFLOW_PATH, name=(WORKFLOW_NAME,
        'Repair abandoned Pages | ' + mode + ' | id=' + DEPLOYMENT),
        run_id=self_id, head=expected_head, event='workflow_dispatch')
    require(actual.get('workflow_id') == registration.get('id') and actual.get('status') == 'in_progress',
            'REPAIR_API_IDENTITY_INVALID')
    require(actual.get('display_title') == 'Repair abandoned Pages | ' + mode + ' | id=' + DEPLOYMENT,
            'REPAIR_MARKER_MISMATCH')
    if mode == 'cancel':
        prior = handoff.all_items(client, '/actions/workflows/' + WORKFLOW + '/runs', 'workflow_runs')
        matches = [r for r in prior if r.get('display_title') == cancel_marker()]
        require(len(matches) == 1 and matches[0].get('id') == self_id,
                'PRIOR_CANCEL_RUN_EXISTS_DO_NOT_RETRY')
    return self_id


def backend_state(client):
    value = client.request('/pages/deployments/' + DEPLOYMENT)
    require(isinstance(value, dict) and value.get('status') in handoff.ACTIVE | handoff.FAILED | {'succeed'},
            'PAGES_BACKEND_STATUS_UNKNOWN')
    require(value['status'] not in handoff.FAILED, 'BACKEND_ALREADY_TERMINAL_NO_CANCEL')
    return value['status']


def within_cancel_window():
    # Deliberately no time supplied by workflow inputs or environment.
    now = datetime.now(timezone(timedelta(hours=8)))
    require(now.strftime('%Y%m%d') == DAY and (15, 0) <= (now.hour, now.minute) < (23, 30),
            'INCIDENT_CANCEL_WINDOW_CLOSED')


def execute(client, env, root, evidence):
    mode, head = env.get('REPAIR_MODE'), env.get('EXPECTED_HEAD')
    require(mode in {'audit', 'cancel'}, 'REPAIR_MODE_INVALID')
    handoff.sha(head)
    deploy_bytes = (root / '.github/workflows/deploy_dc20_pages.yml').read_bytes()
    require(digest(deploy_bytes) == PINNED_DEPLOY_SHA256
            and b'  group: dc20-pages\n' in deploy_bytes, 'EXISTING_PAGES_LOCK_PIN_MISMATCH')
    proof = {key: value for key, value in evidence.items() if key != 'review_ready'}
    require(digest(json.dumps(proof, sort_keys=True, separators=(',', ':'),
                              ensure_ascii=False).encode()) == INCIDENT_EVIDENCE_SHA256,
            'REVIEWED_INCIDENT_PROOF_CHANGED')
    require(evidence.get('review_ready') is True, 'REVIEW_EVIDENCE_NOT_READY')
    require([s.get('id') for s in evidence.get('artifacts', [])] == [13678414118, 13680206038]
            and [s.get('head') for s in evidence['artifacts']] == [SOURCE, TARGET]
            and {s.get('job_id') for s in evidence.get('log_proofs', [])}
                == {129194373756, 129197369975, 129200940731, 129195600302}
            and all(s.get('required_literals') for s in evidence['log_proofs']),
            'INCIDENT_PROOF_SET_INCOMPLETE')
    require(evidence.get('deployment_id') == DEPLOYMENT and evidence.get('reason') == REASON,
            'INCIDENT_BINDING_MISMATCH')
    if mode == 'cancel':
        require(env.get('CONFIRM_ABANDONED_CANCEL') == CONFIRM, 'MANUAL_CONFIRMATION_REQUIRED')
        within_cancel_window()
    self_id = environment(client, env, head, mode)
    require(handoff.current_head(client) == head, 'MAIN_MOVED_BEFORE_REPAIR')
    primary = original_identity(client, root, head, evidence)
    ignored = no_other_work(client, self_id, head)
    before = backend_state(client)
    files = preserved_public(primary)
    # Slow immutable log/artifact reads cannot leave stale live guards behind.
    ignored = no_other_work(client, self_id, head)
    require(backend_state(client) == before, 'BACKEND_CHANGED_RECHECK')
    preserved_public(primary)
    require(handoff.current_head(client) == head, 'MAIN_MOVED_BEFORE_CANCEL')
    result = {'status': 'REVIEWED_ABANDONED_BACKEND', 'reason': REASON,
              'deployment_id': DEPLOYMENT, 'backend_status': before, 'preserved_public_head': SOURCE,
              'source_cas': TARGET, 'public_files_checked': files, 'ignored_orphans': ignored,
              'cancel_requested': False}
    if mode == 'cancel':
        within_cancel_window()
        # All live guards are complete. Only this exact backend mutation exists.
        print(json.dumps({'status': 'ONE_CANCEL_ATTEMPT_STARTING', 'deployment_id': DEPLOYMENT}), flush=True)
        result.update(client.cancel_once())
        result['cancel_requested'] = True
        # Receipt is acknowledgement, never a CLEAR/publication-success claim.
        result['publication_complete'] = False
    return result


def main():
    client, root = Client(os.environ.get('GH_TOKEN')), Path('.')
    try:
        result = execute(client, os.environ, root, json.loads((root / EVIDENCE_PATH).read_bytes()))
        print(json.dumps(result, sort_keys=True))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        reason = str(exc) if re.fullmatch('[A-Z0-9_]+', str(exc)) else 'REPAIR_EVIDENCE_INVALID'
        print(json.dumps({'status': 'BLOCKED', 'reason': reason, 'cancel_retry_allowed': False}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
