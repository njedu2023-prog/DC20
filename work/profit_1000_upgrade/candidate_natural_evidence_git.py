"""Append one immutable research evidence capsule via bounded GitHub Git CAS.

No model, ledger, frontend or workflow writes. A Git acknowledgement alone is
not a verified prospective publication, market source, or executable fill.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import PurePosixPath
import re
import time
import urllib.error
import urllib.request

REPO = 'njedu2023-prog/DC20'
PREFIX = 'work/profit_1000_upgrade/candidate_natural_evidence/'
MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES = 65, 8 * 1024**2, 64 * 1024**2 + 65536


def require(ok, message):
    if not ok:
        raise ValueError(message)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def git_blob(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def sha40(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{40}', value), 'EXACT_GIT_SHA_REQUIRED')
    return value


class EvidenceGitWriter:
    """Own request budget; fixed repository and Git endpoints; no redirect/retry."""
    def __init__(self, token):
        require(type(token) is str and token and not any(c.isspace() for c in token), 'TOKEN_REQUIRED')
        self.token, self.calls, self.started = token, 0, time.monotonic()
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        self.opener = urllib.request.build_opener(NoRedirect())

    def call(self, method, suffix, payload=None):
        allowed = ((method == 'GET' and (suffix == '/git/ref/heads/main' or
            re.fullmatch(r'/git/(commits|trees)/[0-9a-f]{40}(\?recursive=1)?', suffix))) or
            (method == 'POST' and suffix in ('/git/blobs', '/git/trees', '/git/commits')) or
            (method == 'PATCH' and suffix == '/git/refs/heads/main'))
        require(allowed, 'NON_GIT_ROUTE_REJECTED')
        self.calls += 1
        require(self.calls <= 128 and time.monotonic() - self.started < 300, 'GIT_BUDGET_EXCEEDED')
        body = None if payload is None else encoded(payload)
        forbidden = (self.token.encode(), json.dumps(self.token)[1:-1].encode())
        require(body is None or not any(x in body for x in forbidden), 'CREDENTIAL_PAYLOAD_REJECTED')
        request = urllib.request.Request('https://api.github.com/repos/' + REPO + suffix,
            data=body, method=method, headers={'Authorization': 'Bearer ' + self.token,
                'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json',
                'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'DC20-research-evidence-CAS'})
        try:
            with self.opener.open(request, timeout=min(20, max(1, 300-(time.monotonic()-self.started)))) as response:
                require(response.status in (200, 201), 'GIT_STATUS_REJECTED')
                raw = response.read(8_000_001)
                require(len(raw) <= 8_000_000 and not any(x in raw for x in forbidden), 'GIT_RESPONSE_REJECTED')
                require(time.monotonic() - self.started < 300, 'GIT_BUDGET_EXCEEDED')
                return json.loads(raw)
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ValueError('GIT_API_FAILED_NO_FORCE_OR_RETRY') from None


def validate_files(files):
    require(type(files) is dict and 3 <= len(files) <= MAX_FILES, 'BOUNDED_CAPSULE_FILES_REQUIRED')
    days, names, total = set(), set(), 0
    for path, raw in files.items():
        match = re.fullmatch(re.escape(PREFIX) + r'([0-9]{8})/(manifest\.json|context\.json|bodies/([0-9a-f]{64})\.bin)', path)
        require(match and type(raw) is bytes and 0 < len(raw) <= MAX_FILE_BYTES, 'EVIDENCE_PATH_OR_BODY_REJECTED')
        day, name, body_sha = match.groups()
        datetime.strptime(day, '%Y%m%d')
        require(day >= '20260914', 'FORWARD_D_REQUIRED')
        require(not body_sha or digest(raw) == body_sha, 'CONTENT_ADDRESSED_BODY_CHANGED')
        require(name != 'context.json' or len(raw) <= 65536, 'CONTEXT_SIZE_EXCEEDED')
        days.add(day)
        names.add(name)
        total += len(raw)
    require(len(days) == 1 and {'manifest.json', 'context.json'} <= names and total <= MAX_TOTAL_BYTES,
        'ONE_COMPLETE_BOUNDED_CAPSULE_REQUIRED')
    return next(iter(days))


def publish_new_evidence(files, *, github_client, deadline, clock=lambda: datetime.now(timezone.utc), pre_cas_guard=None):
    day = validate_files(files)
    sealed = tuple(sorted(files.items()))
    require(deadline.tzinfo is not None and clock() < deadline, 'EVIDENCE_PUBLICATION_DEADLINE_PASSED')
    head = sha40(github_client.call('GET', '/git/ref/heads/main')['object']['sha'])
    parent = github_client.call('GET', '/git/commits/' + head)
    require(parent['sha'] == head, 'EXACT_PARENT_REQUIRED')
    tree_sha = sha40(parent['tree']['sha'])
    tree = github_client.call('GET', '/git/trees/' + tree_sha + '?recursive=1')
    require(tree['sha'] == tree_sha and tree['truncated'] is False, 'COMPLETE_PARENT_TREE_REQUIRED')
    old = {x['path']: x for x in tree['tree']}
    require(len(old) == len(tree['tree']) and not any(p.startswith(PREFIX+day+'/') for p in old),
        'EXISTING_OR_PARTIAL_DAY_KEEP_ORIGINAL')
    additions = []
    for path, raw in sealed:
        result = github_client.call('POST', '/git/blobs', {'encoding': 'base64', 'content': base64.b64encode(raw).decode()})
        require(result['sha'] == git_blob(raw), 'CREATED_BLOB_CHANGED')
        additions.append({'path': path, 'type': 'blob', 'mode': '100644', 'sha': result['sha']})
    made = github_client.call('POST', '/git/trees', {'base_tree': tree_sha, 'tree': additions})
    new_tree = sha40(made['sha'])
    verify = github_client.call('GET', '/git/trees/' + new_tree + '?recursive=1')
    require(verify['sha'] == new_tree and verify['truncated'] is False, 'COMPLETE_NEW_TREE_REQUIRED')
    new = {x['path']: x for x in verify['tree']}
    parents = {str(p) for path in files for p in PurePosixPath(path).parents if str(p) != '.'}
    require(len(new) == len(verify['tree']) and set(new) == set(old) | set(files) | parents,
        'UNEXPECTED_TREE_MEMBERSHIP')
    require(all(new[p]['type'] == 'tree' and new[p]['mode'] == '040000' for p in parents), 'PARENT_NOT_DIRECTORY')
    require(all(new[p] == item for p, item in old.items() if item['type'] == 'tree' and p not in parents), 'UNRELATED_TREE_CHANGED')
    wanted = {p: (x['mode'], x['type'], x['sha']) for p, x in old.items() if x['type'] != 'tree'}
    wanted.update({x['path']: (x['mode'], x['type'], x['sha']) for x in additions})
    require({p: (x['mode'], x['type'], x['sha']) for p,x in new.items() if x['type'] != 'tree'} == wanted,
        'EXISTING_FILE_CHANGED_OR_UNEXPECTED_ADDITION')
    made = github_client.call('POST', '/git/commits', {'message': 'research: preserve original candidate publication ' + day,
        'tree': new_tree, 'parents': [head]})
    new_head = sha40(made['sha'])
    require(made['tree']['sha'] == new_tree and [x['sha'] for x in made['parents']] == [head], 'COMMIT_BINDING_CHANGED')
    require(github_client.call('GET', '/git/ref/heads/main')['object']['sha'] == head, 'MAIN_MOVED_NO_RETRY')
    if pre_cas_guard is not None:
        pre_cas_guard()
    require(tuple(sorted(files.items())) == sealed, 'PAYLOAD_CHANGED')
    require(clock() < deadline, 'EVIDENCE_PUBLICATION_DEADLINE_PASSED')
    moved = github_client.call('PATCH', '/git/refs/heads/main', {'sha': new_head, 'force': False})
    require(moved['object']['sha'] == new_head, 'UNCERTAIN_CAS_READ_ONLY_RECONCILIATION_REQUIRED')
    require(tuple(sorted(files.items())) == sealed, 'POST_CAS_CHANGE_READ_ONLY_RECONCILIATION_REQUIRED')
    if pre_cas_guard is not None:
        pre_cas_guard()
    require(tuple(sorted(files.items())) == sealed, 'POST_GUARD_CHANGE_READ_ONLY_RECONCILIATION_REQUIRED')
    acknowledged = clock()
    return {'schema_version': 'dc20_candidate_natural_evidence_git_ack_v1',
        'status': 'EVIDENCE_GIT_ACKNOWLEDGED' if acknowledged < deadline else 'LATE_EVIDENCE_NOT_ADMITTED',
        'signal_date': day, 'commit_sha': new_head, 'parent_sha': head, 'tree_sha': new_tree,
        'files': [{'path': p, 'sha256': digest(raw), 'git_blob_sha1': git_blob(raw), 'bytes': len(raw)} for p,raw in sealed],
        'acknowledged_at_host_utc': acknowledged.isoformat(), 'independent_observer_job_check_required': True,
        'natural_forward_admission_issued': False, 'production_activation_allowed': False,
        'actual_execution_claimed': False, 'existing_files_modified': False}
