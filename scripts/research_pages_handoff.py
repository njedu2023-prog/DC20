"""Hand research Pages to a real new main run, without changing frozen data.

Only the registered child workflow may be dispatched. Pages backend checks are
read-only; this module never creates/cancels deployments or writes Git objects.
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from scripts import profit_research_delivery as delivery

REPO = 'njedu2023-prog/DC20'
CHILD = 'profit_research_pages.yml'
CHILD_PATH = '.github/workflows/' + CHILD
CHILD_NAME = 'DC20 · Publish exact research Pages'
PARENTS = {
    '.github/workflows/profit_research.yml': 'DC20 · Independent profit research data',
    '.github/workflows/publish_candidate_profit.yml': 'DC20 · Publish active candidate profit',
}
# Official pages-deployment-status schema and pinned deploy-pages implementation.
ACTIVE = {'deployment_in_progress', 'syncing_files', 'finished_file_sync',
          'updating_pages', 'purging_cdn', 'deployment_attempt_error'}
FAILED = {'deployment_cancelled', 'deployment_failed', 'deployment_content_failed',
          'deployment_perms_error', 'deployment_lost'}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(value):
    require(isinstance(value, str) and re.fullmatch('[0-9a-f]{40}', value), 'EXACT_SHA_REQUIRED')
    return value


class Client:
    def __init__(self, token):
        require(bool(token), 'GITHUB_TOKEN_REQUIRED')
        self.token = token

    def request(self, path, body=None):
        require(path.startswith('/'), 'API_PATH_INVALID')
        if body is not None:
            require(path == '/actions/workflows/' + CHILD + '/dispatches'
                    and body.get('ref') == 'main', 'DISPATCH_TARGET_REJECTED')
        request = urllib.request.Request('https://api.github.com/repos/' + REPO + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization': 'Bearer ' + self.token,
                     'Accept': ('application/vnd.github.object+json' if path.startswith('/contents/')
                                else 'application/vnd.github+json'),
                     'X-GitHub-Api-Version': '2026-03-10',
                     'Cache-Control': 'no-cache'})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
            require(len(raw) <= 8 * 1024 * 1024, 'API_RESPONSE_TOO_LARGE')
            return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raise ValueError('GITHUB_HTTP_' + str(exc.code)) from None
        except (TimeoutError, urllib.error.URLError):
            raise ValueError('DISPATCH_OUTCOME_UNKNOWN' if body is not None else
                             'GITHUB_READ_UNAVAILABLE') from None


def all_items(client, path, key):
    items, total = [], None
    for page in range(1, 11):
        value = client.request(path + ('&' if '?' in path else '?') +
                               'per_page=100&page=' + str(page))
        require(isinstance(value, dict) and isinstance(value.get(key), list)
                and isinstance(value.get('total_count'), int), 'API_PAGINATION_INCOMPLETE')
        if total is None:
            total = value['total_count']
        require(total == value['total_count'] and 0 <= total <= 1000, 'API_PAGINATION_CHANGED')
        items.extend(value[key])
        require(all(isinstance(item, dict) and isinstance(item.get('id'), int) for item in items),
                'API_ITEM_INVALID')
        require(len({item['id'] for item in items}) == len(items), 'API_PAGINATION_DUPLICATE')
        if len(value[key]) < 100 or len(items) == total:
            require(len(items) == total, 'API_PAGINATION_INCOMPLETE')
            return items
    raise ValueError('API_PAGINATION_INCOMPLETE')


def validate_run(run, *, path, name, run_id=None, head=None, event=None):
    require(isinstance(run, dict), 'RUN_IDENTITY_INVALID')
    names = (name,) if isinstance(name, str) else tuple(name)
    require(run.get('path') == path and run.get('name') in names
            and run.get('head_branch') == 'main' and run.get('run_attempt') == 1
            and run.get('repository', {}).get('full_name') == REPO
            and run.get('head_repository', {}).get('full_name') == REPO,
            'RUN_IDENTITY_INVALID')
    if run_id is not None:
        require(run.get('id') == run_id, 'RUN_ID_MISMATCH')
    if head is not None:
        require(run.get('head_sha') == head, 'RUN_HEAD_MISMATCH')
    if event is not None:
        require(run.get('event') == event, 'RUN_EVENT_MISMATCH')
    return run


def source_run(client, run_id):
    require(str(run_id).isdigit() and int(run_id) > 0, 'SOURCE_RUN_ID_INVALID')
    run = client.request('/actions/runs/' + str(run_id))
    require(run.get('path') in PARENTS, 'SOURCE_WORKFLOW_REJECTED')
    validate_run(run, path=run['path'], name=PARENTS[run['path']], run_id=int(run_id))
    registration = client.request('/actions/workflows/' + run['path'].rsplit('/', 1)[-1])
    require(registration.get('state') == 'active' and registration.get('path') == run['path']
            and registration.get('name') == PARENTS[run['path']]
            and registration.get('id') == run.get('workflow_id'), 'SOURCE_REGISTRATION_MISMATCH')
    require(run.get('event') in {'schedule', 'workflow_dispatch', 'workflow_run', 'push'}
            and not (run['path'].endswith('publish_candidate_profit.yml') and run.get('event') == 'push'),
            'SOURCE_EVENT_REJECTED')
    # Aggregate failure can mean only auxiliary Pages failed after a valid CAS.
    jobs = all_items(client, '/actions/runs/' + str(run_id) + '/jobs', 'jobs')
    matches = [job for job in jobs if job.get('name') == 'accumulate'
               or job.get('name', '').endswith(' / accumulate')]
    require(len(matches) == 1 and matches[0].get('run_id') == int(run_id)
            and matches[0].get('status') == 'completed' and matches[0].get('conclusion') == 'success'
            and any(step.get('name') == 'Write only research evidence through GitHub API'
                    and step.get('conclusion') == 'success'
                    for step in matches[0].get('steps', [])), 'SOURCE_RESEARCH_CAS_NOT_SUCCESSFUL')
    run['_research_job'] = matches[0]
    return run


def source_cas(client, root, head, source, run):
    """Bind the successful producer job to immutable pointer/version Git bytes.

    The head may contain later code-only commits. The historical CAS must still
    be in its ancestry, made during this actual producer job, with source parent.
    """
    sha(head); sha(source)
    job = run['_research_job']
    def instant(value):
        require(isinstance(value, str), 'RESEARCH_SOURCE_CAS_TIME_INVALID')
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        require(value.tzinfo is not None, 'RESEARCH_SOURCE_CAS_TIME_INVALID')
        return value
    started, ended = instant(job.get('started_at')), instant(job.get('completed_at'))
    require(started <= ended, 'RESEARCH_SOURCE_CAS_TIME_INVALID')
    pointer_path = delivery.PREFIX + 'latest.json'
    pointer_raw = (root/pointer_path).read_bytes()
    pointer = json.loads(pointer_raw)
    require(re.fullmatch('versions/[0-9a-f]{64}\\.json', pointer.get('path', '')),
            'UNSAFE_RESEARCH_VERSION_PATH')
    version_path = delivery.PREFIX + pointer['path']
    files = {pointer_path: pointer_raw, version_path: (root/version_path).read_bytes()}
    query = urllib.parse.urlencode({'sha': head, 'path': pointer_path,
                                   'since': job['started_at'], 'until': job['completed_at']})
    commits, seen = [], set()
    for page in range(1, 11):
        rows = client.request('/commits?' + query + '&per_page=100&page=' + str(page))
        require(isinstance(rows, list) and len(rows) <= 100, 'RESEARCH_SOURCE_CAS_PAGINATION_INCOMPLETE')
        for row in rows:
            require(isinstance(row, dict), 'RESEARCH_SOURCE_CAS_RESPONSE_INVALID')
            commit_sha = sha(row.get('sha'))
            require(commit_sha not in seen, 'RESEARCH_SOURCE_CAS_PAGINATION_DUPLICATE')
            seen.add(commit_sha)
            commits.append(row)
        if len(rows) < 100:
            break
    else:
        raise ValueError('RESEARCH_SOURCE_CAS_PAGINATION_INCOMPLETE')
    matches = []
    for commit in commits:
        parents = commit.get('parents')
        stamp = instant(commit.get('commit', {}).get('committer', {}).get('date'))
        if (not isinstance(parents, list) or len(parents) != 1
                or parents[0].get('sha') != source or not started <= stamp <= ended):
            continue
        matched = True
        for path, raw in files.items():
            value = client.request('/contents/' + path + '?ref=' + commit['sha'])
            blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
            require(isinstance(value, dict), 'RESEARCH_SOURCE_CAS_BLOB_RESPONSE_INVALID')
            if (value.get('type') != 'file' or value.get('path') != path
                    or value.get('size') != len(raw) or value.get('sha') != blob):
                matched = False
        if matched:
            matches.append(commit['sha'])
    require(len(matches) == 1, 'RESEARCH_SOURCE_CAS_NOT_UNIQUE')
    return matches[0]


def capsule(root, head, day, source):
    sha(head); sha(source)
    require(re.fullmatch('20[0-9]{6}', day), 'SIGNAL_DATE_INVALID')
    actual = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    require(actual == head, 'CHECKOUT_HEAD_MISMATCH')
    require(delivery.signal_date(root) == day, 'RESEARCH_DAY_MISMATCH')
    pointer = json.loads((root / delivery.PREFIX / 'latest.json').read_bytes())
    require(re.fullmatch('versions/[0-9a-f]{64}\\.json', pointer.get('path', '')),
            'UNSAFE_RESEARCH_VERSION_PATH')
    raw = (root / delivery.PREFIX / pointer['path']).read_bytes()
    delivery.validate_dataset(pointer, raw, day, source)
    return pointer


def public_head():
    # Deliberately use the ordinary public URL as the customer sees it.
    value = json.loads(delivery.get('https://njedu2023-prog.github.io/DC20/revision.json'))
    require(isinstance(value, dict), 'PUBLIC_REVISION_INVALID')
    return sha(value.get('head_sha'))


def wait_backend(client, deployment, expected_public, *, timeout=1200,
                 monotonic=time.monotonic, sleep=time.sleep, read_public=public_head):
    if not deployment:
        require(not expected_public, 'WAIT_ID_REQUIRED')
        return {'status': 'NO_PRIOR_DEPLOYMENT'}
    sha(deployment); sha(expected_public)
    deadline = monotonic() + timeout
    while True:
        value = client.request('/pages/deployments/' + deployment)
        require(isinstance(value, dict), 'PAGES_STATUS_INCOMPLETE')
        status = value.get('status')
        require(status in ACTIVE | FAILED | {'succeed'}, 'PAGES_STATUS_UNKNOWN')
        if status in FAILED:
            # Explicit backend terminal failure is distinct from an HTTP/API error.
            print(json.dumps({'prior_deployment': deployment, 'status': status}), flush=True)
            return {'status': 'PRIOR_BACKEND_TERMINAL_FAILURE', 'backend_status': status}
        observed = read_public() if status == 'succeed' else None
        if status == 'succeed' and observed == expected_public:
            return {'status': 'PRIOR_BACKEND_AND_PUBLIC_CONFIRMED', 'head': observed}
        print(json.dumps({'prior_deployment': deployment, 'status': status,
                          'public_head': observed, 'expected_public_head': expected_public}), flush=True)
        require(monotonic() < deadline, 'PAGES_BACKEND_WAIT_TIMEOUT')
        sleep(min(60, max(0, deadline - monotonic())))


def current_head(client):
    value = client.request('/git/ref/heads/main')
    require(value.get('ref') == 'refs/heads/main', 'MAIN_REF_INVALID')
    return sha(value.get('object', {}).get('sha'))


def marker(head, source_id):
    return 'Research Pages | head=' + head + ' | source=' + str(source_id)


def matching_child(client, head, source_id, registration):
    runs = all_items(client, '/actions/workflows/' + CHILD + '/runs?head_sha=' + head, 'workflow_runs')
    matches = [run for run in runs if run.get('display_title') == marker(head, source_id)]
    require(len(matches) <= 1, 'AMBIGUOUS_RESEARCH_PAGES_RUNS')
    if not matches:
        return None
    run = client.request('/actions/runs/' + str(matches[0]['id']))
    validate_run(run, path=CHILD_PATH, name=(CHILD_NAME, marker(head, source_id)), head=head, event='workflow_dispatch')
    require(run.get('workflow_id') == registration['id']
            and run.get('display_title') == marker(head, source_id), 'CHILD_REGISTRATION_MISMATCH')
    return run


def dispatch_and_wait(client, head, day, source, source_id, prior_deployment='', prior_public='', *, timeout=2700,
                      monotonic=time.monotonic, sleep=time.sleep):
    sha(head); sha(source)
    require(bool(prior_deployment) == bool(prior_public), 'PRIOR_DEPLOYMENT_PAIR_REQUIRED')
    if prior_deployment:
        sha(prior_deployment); sha(prior_public)
    source_run(client, source_id)
    registration = client.request('/actions/workflows/' + CHILD)
    require(registration.get('state') == 'active' and registration.get('path') == CHILD_PATH
            and registration.get('name') == CHILD_NAME and isinstance(registration.get('id'), int),
            'CHILD_WORKFLOW_NOT_ACTIVE')
    run = matching_child(client, head, source_id, registration)
    if run is None:
        require(current_head(client) == head, 'MAIN_MOVED_BEFORE_DISPATCH')
        inputs = {'expected_head': head, 'signal_date': day, 'source_revision': source,
                  'source_run_id': str(source_id), 'wait_for_deployment': prior_deployment,
                  'wait_expected_head': prior_public}
        try:
            client.request('/actions/workflows/' + CHILD + '/dispatches',
                           {'ref': 'main', 'inputs': inputs})
        except ValueError as exc:
            if str(exc) != 'DISPATCH_OUTCOME_UNKNOWN':
                raise
            # One POST only. Continue discovery; uncertainty never authorizes a resend.
            print('Dispatch outcome unknown; querying the real child run without resending.', flush=True)
    deadline = monotonic() + timeout
    while True:
        run = matching_child(client, head, source_id, registration)
        if run:
            print(json.dumps({'child_run_id': run['id'], 'status': run['status'],
                              'conclusion': run.get('conclusion'),
                              'url': run.get('html_url')}), flush=True)
            if run.get('status') == 'completed':
                require(run.get('conclusion') == 'success',
                        'RESEARCH_PAGES_CHILD_' + str(run.get('conclusion')).upper())
                return run
            require(run.get('status') in {'queued', 'in_progress', 'waiting', 'pending', 'requested'},
                    'CHILD_STATUS_UNKNOWN')
        require(monotonic() < deadline, 'RESEARCH_PAGES_CHILD_WAIT_TIMEOUT')
        sleep(min(60, max(0, deadline - monotonic())))


def guard(client, env, root):
    require(env.get('GITHUB_ACTIONS') == 'true' and env.get('GITHUB_REPOSITORY') == REPO
            and env.get('GITHUB_REF') == 'refs/heads/main' and env.get('GITHUB_RUN_ATTEMPT') == '1'
            and env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
            and env.get('GITHUB_WORKFLOW_REF') == REPO + '/' + CHILD_PATH + '@refs/heads/main',
            'CHILD_ENVIRONMENT_REJECTED')
    head, source, day = env['EXPECTED_RESEARCH_HEAD'], env['EXPECTED_RESEARCH_SOURCE'], env['EXPECTED_RESEARCH_D']
    require(env.get('GITHUB_SHA') == head, 'REAL_CHILD_HEAD_MISMATCH')
    registration = client.request('/actions/workflows/' + CHILD)
    require(registration.get('state') == 'active' and registration.get('path') == CHILD_PATH
            and registration.get('name') == CHILD_NAME, 'CHILD_WORKFLOW_NOT_ACTIVE')
    run = validate_run(client.request('/actions/runs/' + env['GITHUB_RUN_ID']),
        path=CHILD_PATH, name=(CHILD_NAME, marker(head, env['SOURCE_RUN_ID'])), run_id=int(env['GITHUB_RUN_ID']),
        head=head, event='workflow_dispatch')
    require(run.get('workflow_id') == registration.get('id') and run.get('status') == 'in_progress',
            'CHILD_REGISTRATION_MISMATCH')
    require(run.get('display_title') == marker(head, env['SOURCE_RUN_ID']), 'CHILD_TITLE_MISMATCH')
    require(current_head(client) == head, 'MAIN_MOVED_BEFORE_DEPLOY')
    capsule(root, head, day, source)
    producer = source_run(client, env['SOURCE_RUN_ID'])
    bound_cas = source_cas(client, root, head, source, producer)
    print(json.dumps({'source_run_id': env['SOURCE_RUN_ID'], 'research_cas': bound_cas}), flush=True)
    result = wait_backend(client, env.get('WAIT_FOR_DEPLOYMENT', ''), env.get('WAIT_EXPECTED_HEAD', ''))
    # Waiting cannot silently authorize a stale source or another main revision.
    require(current_head(client) == head, 'MAIN_MOVED_DURING_BACKEND_WAIT')
    capsule(root, head, day, source)
    return result


def already_public(root, head):
    base = 'https://njedu2023-prog.github.io/DC20/'
    if public_head() != head:
        return False
    pointer_raw = (root / delivery.PREFIX / 'latest.json').read_bytes()
    pointer = json.loads(pointer_raw)
    require(re.fullmatch('versions/[0-9a-f]{64}\\.json', pointer.get('path', '')),
            'UNSAFE_RESEARCH_VERSION_PATH')
    version_raw = (root / delivery.PREFIX / pointer['path']).read_bytes()
    return (delivery.get(base + delivery.PREFIX + 'latest.json') == pointer_raw
            and delivery.get(base + delivery.PREFIX + pointer['path']) == version_raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('dispatch', 'guard'))
    args = parser.parse_args()
    client, root = Client(os.environ.get('GH_TOKEN')), Path('.')
    env = os.environ
    if args.command == 'guard':
        print(json.dumps(guard(client, env, root)))
    else:
        head, day, source = env['EXPECTED_RESEARCH_HEAD'], env['EXPECTED_RESEARCH_D'], env['EXPECTED_RESEARCH_SOURCE']
        require(env.get('GITHUB_ACTIONS') == 'true' and env.get('GITHUB_REPOSITORY') == REPO
                and env.get('GITHUB_REF') == 'refs/heads/main' and env.get('GITHUB_RUN_ATTEMPT') == '1',
                'PARENT_ENVIRONMENT_REJECTED')
        parent = source_run(client, env['GITHUB_RUN_ID'])
        require(parent.get('head_sha') == env['GITHUB_SHA'], 'PARENT_HEAD_MISMATCH')
        capsule(root, head, day, source)
        require(current_head(client) == head, 'MAIN_MOVED_BEFORE_DISPATCH')
        if already_public(root, head):
            print('RESEARCH_ALREADY_EXACTLY_PUBLIC_NO_DISPATCH')
            return
        source_cas(client, root, head, source, parent)
        run = dispatch_and_wait(client, head, day, source, env['GITHUB_RUN_ID'],
                                env.get('WAIT_FOR_DEPLOYMENT', ''), env.get('WAIT_EXPECTED_HEAD', ''))
        if env.get('GITHUB_STEP_SUMMARY'):
            with open(env['GITHUB_STEP_SUMMARY'], 'a') as stream:
                stream.write('Independent research Pages: ' + run['html_url'] + '\n')


if __name__ == '__main__':
    main()
