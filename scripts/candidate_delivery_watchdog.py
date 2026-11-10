"""Bounded delivery recovery, not a scorer, publisher, or source authority.

Each tick dispatches at most one existing main workflow. Existing import,
publication, timestamp and immutable-ledger validators remain authoritative.
No model, frozen record, source file, trade, or Git reference is written here.
"""
from __future__ import annotations
import argparse
import base64
import csv
from datetime import datetime, time, timedelta, timezone
import hashlib
import io
import json
import os
import re
import urllib.error
import urllib.request

if __package__:
    from .dc20_writer_queue import inspect_queue, GROUP
else:
    from dc20_writer_queue import inspect_queue, GROUP

REPO = 'njedu2023-prog/DC20'
WORKFLOW = '.github/workflows/candidate_delivery_watchdog.yml'
TZ = timezone(timedelta(hours=8))
NATURAL = 'research_candidate_natural_forward.yml'
OBSERVER = 'research_candidate_natural_observer.yml'
PUBLISHER = 'publish_candidate_profit.yml'
P0 = 'run_primary_d_daily.yml'
HISTORY = 'prepare_primary_history.yml'
P0_NAME = 'DC2.0 · Publish Primary D List (P0)'
IDS = {P0: 343703608, NATURAL: 357010624, OBSERVER: 357027830}
ROOT = 'work/profit_1000_upgrade/'
PUBLIC = 'outputs/decision/candidate_profit_v1/'
UPSTREAMS = ('njedu2023-prog/a-top10', 'njedu2023-prog/a-share-top3-data')
P0_JOBS = ('Build immutable promotion-only D candidate', 'CAS publish one primary D commit',
           'Deploy exact primary D revision / deploy')
P0_GENERATION_STEPS = ('Resolve immutable upstream commits', 'Snapshot protected secondary state',
    'Sync exact-D independent inputs', 'Publish primary promotion list in the isolated scope',
    'Build exact P0 candidate patch', 'Upload immutable P0 candidate')


def require(value, reason):
    if not value:
        raise ValueError(reason)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    # Same contract as candidate_forward_predict.canonical_sha. The public
    # index binds file bytes; the ledger binds the parsed projection content.
    return digest(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())


def document(raw):
    require(isinstance(raw, bytes), 'MISSING_REQUIRED_FILE')
    value = json.loads(raw)
    require(isinstance(value, dict), 'OBJECT_REQUIRED')
    return value


class GitHub:
    def __init__(self, token):
        require(bool(token), 'TOKEN_REQUIRED')
        self.token, self.calls = token, 0

    def request(self, path, body=None, *, repository=REPO):
        self.calls += 1
        require(self.calls <= 200 and path.startswith('/'), 'REQUEST_BUDGET_EXCEEDED')
        require(repository in (REPO, *UPSTREAMS), 'REPOSITORY_REJECTED')
        if body is not None:
            require(repository == REPO and path in {f'/actions/workflows/{w}/dispatches' for w in (P0, NATURAL, OBSERVER, PUBLISHER, HISTORY)}, 'DISPATCH_TARGET_REJECTED')
            require(body.get('ref') == 'main', 'MAIN_ONLY')
        req = urllib.request.Request('https://api.github.com/repos/'+repository+path,
            data=None if body is None else json.dumps(body).encode(), headers={
                'Authorization': 'Bearer '+self.token, 'Accept': 'application/vnd.github+json',
                'X-GitHub-Api-Version': ('2026-03-10' if '/concurrency_groups' in path else '2022-11-28')})
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read(8*1024*1024+1)
            require(len(raw) <= 8*1024*1024, 'RESPONSE_TOO_LARGE')
            return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            if body is None and exc.code == 404 and path == '/actions/concurrency_groups/' + GROUP:
                return None  # Caller first verifies the supported list endpoint.
            if body is None and exc.code == 404 and path.startswith('/contents/'):
                return None
            raise ValueError('GITHUB_HTTP_'+str(exc.code)) from None
        except (TimeoutError, urllib.error.URLError):
            raise ValueError('DISPATCH_OUTCOME_UNKNOWN_NO_BLIND_RETRY' if body is not None else 'GITHUB_READ_UNAVAILABLE') from None

    def file(self, path, head, *, repository=REPO):
        require(re.fullmatch('[0-9a-f]{40}', head), 'EXACT_HEAD_REQUIRED')
        value = self.request('/contents/'+path+'?ref='+head, repository=repository)
        if value is None:
            return None
        require(value.get('type') == 'file' and value.get('encoding') == 'base64', 'REGULAR_SMALL_FILE_REQUIRED')
        raw = base64.b64decode(value['content'])
        require(hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() == value['sha'], 'GIT_BLOB_MISMATCH')
        return raw

    def candidate_code_approval(self, head):
        """Read-only dispatch preflight; never forgive an earlier failed freeze.

        A candidate run is not useful while the exact static code still awaits
        its required push CI. Reuse the production approval validator, including
        its run, jobs and actually executed regression-step checks. API errors or
        malformed approval evidence remain errors, not an empty approval list.
        """
        if __package__:
            from . import candidate_runtime_preflight as gate
        else:
            import candidate_runtime_preflight as gate
        current = gate.remote_surface(self, head)
        require(all(path in current for path in (gate.WORKFLOW,
                'scripts/candidate_runtime_preflight.py',
                'models/decision_model_freeze.json', 'requirements-dev.lock')),
                'INCOMPLETE_STATIC_APPROVAL_SURFACE')
        try:
            return gate.approve(self, current, head)
        except ValueError as exc:
            if str(exc) not in {'NO_SUCCESSFUL_PUSH_REGRESSION_FOR_CURRENT_STATIC_SURFACE',
                               'NO_MATCHING_APPROVAL_WITHIN_BOUNDED_HISTORY'}:
                raise
            value = self.request(f'/actions/workflows/{gate.WORKFLOW_ID}/runs?event=push&branch=main&per_page=100')
            runs = value.get('workflow_runs')
            require(isinstance(runs, list) and type(value.get('total_count')) is int
                    and value['total_count'] >= len(runs) and len(runs) <= 100,
                    'APPROVAL_RUN_LIST_INVALID')
            matching, checked = [], set()
            for candidate in runs:
                if not gate.approval_origin(candidate):
                    continue
                candidate_head = gate.exact_sha(candidate.get('head_sha'))
                checked.add(candidate_head)
                require(len(checked) <= 30, 'APPROVAL_STATE_HISTORY_BOUND_EXCEEDED')
                if gate.remote_surface(self, candidate_head) == current:
                    matching.append(candidate)
            run, pending = None, None
            if matching:
                latest = max(matching, key=lambda row: (stamp(row.get('created_at')), row['id']))
                run = self.request(f'/actions/runs/{latest["id"]}')
                require(gate.approval_origin(run) and all(run.get(key) == latest.get(key)
                        for key in ('id', 'head_sha', 'created_at')),
                        'APPROVAL_LATEST_RUN_IDENTITY_CHANGED')
                require(gate.remote_surface(self, gate.exact_sha(run.get('head_sha'))) == current,
                        'APPROVAL_LATEST_SURFACE_CHANGED')
                if run.get('status') == 'completed':
                    # A failed/cancelled CI needs repair, not another freeze and
                    # not a green watchdog whose only explanation is "waiting".
                    require(run.get('conclusion') == 'success', 'CODE_CI_FAILED_REQUIRES_REPAIR')
                else:
                    require(run.get('status') in {'queued', 'in_progress', 'waiting', 'pending', 'requested'},
                            'APPROVAL_LATEST_STATE_INVALID')
                    pending = run['id']
                # It may complete between the list and this read. A successful
                # badge alone cannot approve code; next tick verifies full jobs.
            return {'status': 'WAITING_FOR_CODE_CI_APPROVAL', 'repository': REPO,
                    'current_head_sha': head, 'surface_sha256': gate.digest(current),
                    'pending_approval_run_id': pending,
                    'latest_matching_approval_run_id': run['id'] if run else None,
                    'reason': ('MATCHING_PUSH_CI_FOUND_RECHECK_REQUIRED' if pending is not None else
                               'SUCCESSFUL_PUSH_CI_RECHECK_REQUIRED' if run else 'NO_MATCHING_SUCCESSFUL_PUSH_CI'),
                    'daily_source_evidence_admitted': False, 'production_freeze_issued': False}

    def p0_sources(self, day, now):
        """Preflight only. The original P0 importer remains the source authority.

        Pin both heads and read only exact-D paths. Never fall back to latest,
        overwrite an incomplete bundle, or dispatch on absent upstream data.
        """
        commits, produced = {}, []
        for repository in UPSTREAMS:
            commit = self.request('/commits/main', repository=repository)
            sha = commit.get('sha', '')
            require(re.fullmatch('[0-9a-f]{40}', sha), 'UPSTREAM_COMMIT_INVALID')
            stamp = datetime.fromisoformat(commit['commit']['committer']['date'].replace('Z', '+00:00'))
            require(stamp.tzinfo is not None and stamp <= now, 'UPSTREAM_COMMIT_FROM_FUTURE')
            commits[repository] = sha
            produced.append(stamp)
        pred_repo, market_repo = UPSTREAMS
        pred = self.file(f'outputs/decisio/pred_decisio_{day}.csv', commits[pred_repo], repository=pred_repo)
        require(pred is not None, 'P0_UPSTREAM_PRED_NOT_READY')
        rows = list(csv.DictReader(io.StringIO(pred.decode('utf-8-sig'))))
        require(rows and all(r.get('trade_date') == day for r in rows), 'P0_UPSTREAM_PRED_DATE_MISMATCH')
        for row in rows:
            source_time(row.get('generated_at_utc'), day, now)
        folder = f'data/raw/{day[:4]}/{day}/'
        files = self.request('/contents/'+folder+'?ref='+commits[market_repo], repository=market_repo)
        require(isinstance(files, list), 'P0_UPSTREAM_MARKET_NOT_READY')
        names = {r['name'] for r in files if r.get('type') == 'file' and r.get('size', 0) > 0}
        require({'daily.csv', 'daily_basic.csv', 'stk_limit.csv', 'stock_basic.csv', 'limit_list_d.csv', '_meta.json'} <= names,
                'P0_UPSTREAM_MARKET_FILES_MISSING')
        meta = document(self.file(folder+'_meta.json', commits[market_repo], repository=market_repo))
        require(meta.get('resolved_trade_date') == day, 'P0_UPSTREAM_MARKET_DATE_MISMATCH')
        source_time(meta.get('generated_at_bj'), day, now, beijing=True)
        return {'pred_commit': commits[pred_repo], 'market_commit': commits[market_repo],
                'source_ready_at': max(produced).isoformat()}

    def public_core_status(self, day, p0_raw, source_index_raw, public_raw):
        """Verify the published primary/day bytes despite a disclosed auxiliary gap."""
        try:
            def fetch(path):
                with urllib.request.urlopen('https://njedu2023-prog.github.io/DC20/'+path, timeout=20) as response:
                    body = response.read(8*1024*1024+1)
                require(len(body) <= 8*1024*1024, 'PUBLIC_RESPONSE_TOO_LARGE')
                return body
            revision = document(fetch('revision.json'))
            marker = revision.get('candidate_profit_delivery', {})
            if marker.get('schema_version') != 'dc20_candidate_delivery_isolation_v1':
                return None
            require(revision.get('signal_date') == day and revision.get('primary_d_status') == 'READY'
                and marker.get('signal_date') == day and marker.get('status') == 'PARTIAL'
                and marker.get('current_ranking_status') == 'READY' and marker.get('summary_status') == 'UNAVAILABLE'
                and marker.get('new_freeze_created') is False and marker.get('historical_results_rewritten') is False
                and marker.get('source_index_sha256') == digest(source_index_raw), 'PUBLIC_ISOLATION_IDENTITY_CHANGED')
            require(revision.get('candidate_profit_index_url') == PUBLIC+'index.json', 'PUBLIC_INDEX_PATH_CHANGED')
            index_raw = fetch(PUBLIC+'index.json')
            require(digest(index_raw) == revision.get('candidate_profit_index_sha256'), 'PUBLIC_INDEX_BINDING_CHANGED')
            index = document(index_raw)
            entries = [r for r in index['days'] if r.get('signal_date') == day]
            current = document(public_raw)
            require(len(entries) == 1 and entries[0].get('path') == PUBLIC+f'day_{day}.json'
                and entries[0].get('sha256') == digest(public_raw)
                and entries[0].get('p0_file_sha256') == digest(p0_raw)
                and entries[0].get('snapshot_file_sha256') == current.get('snapshot_file_sha256')
                and fetch(PUBLIC+f'day_{day}.json') == public_raw
                and fetch(f'outputs/decision/three_rank_top10_{day}.json') == p0_raw,
                'PUBLIC_CORE_DAY_BINDING_CHANGED')
            return {'status': 'CORE_DELIVERED_AUX_PENDING', 'summary_status': 'UNAVAILABLE',
                    'current_ranking_status': 'READY', 'reason': marker.get('reason'),
                    'excluded_days': marker.get('excluded_days', [])}
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def public_ready(self, day, day_sha, summary_sha, p0_raw):
        try:
            with urllib.request.urlopen('https://njedu2023-prog.github.io/DC20/revision.json', timeout=20) as response:
                revision = document(response.read(8*1024*1024))
            if revision.get('signal_date') != day or revision.get('primary_d_status') != 'READY':
                return False
            with urllib.request.urlopen('https://njedu2023-prog.github.io/DC20/outputs/decision/three_rank_top10_'+day+'.json', timeout=20) as response:
                if response.read(8*1024*1024) != p0_raw:
                    return False
            bodies = []
            for path in ('index.json', f'day_{day}.json', 'summary.json'):
                with urllib.request.urlopen('https://njedu2023-prog.github.io/DC20/'+PUBLIC+path, timeout=20) as response:
                    bodies.append(response.read(8*1024*1024))
            entry = next((r for r in document(bodies[0])['days'] if r['signal_date'] == day), None)
            return entry is not None and entry['sha256'] == day_sha and digest(bodies[1]) == day_sha and digest(bodies[2]) == summary_sha
        except (OSError, ValueError, KeyError):
            return False


def window(calendar_raw, now):
    require(now.tzinfo is not None, 'AWARE_CLOCK_REQUIRED')
    rows = list(csv.DictReader(io.StringIO(calendar_raw.decode('utf-8-sig'))))
    sessions = sorted(r['cal_date'] for r in rows if r['exchange'] == 'SSE' and r['is_open'] == '1')
    require(sessions and len(sessions) == len(set(sessions)), 'CALENDAR_INVALID')
    local = now.astimezone(TZ)
    closed = [d for d in sessions if d < local.strftime('%Y%m%d') or
              (d == local.strftime('%Y%m%d') and local.time() >= time(15))]
    require(closed, 'NO_CLOSED_SESSION')
    day = closed[-1]
    pos = sessions.index(day)
    require(pos+2 < len(sessions), 'CALENDAR_FUTURE_MISSING')
    trade, exit_day = sessions[pos+1:pos+3]
    cutoff = datetime.strptime(trade+'0920', '%Y%m%d%H%M').replace(tzinfo=TZ)
    return day, trade, exit_day, cutoff


def valid_run(run, workflow, *, success=True):
    require(isinstance(run.get('id'), int) and run['id'] > 0 and run.get('run_attempt') == 1,
            'ORIGINAL_FIRST_ATTEMPT_REQUIRED')
    require(run.get('workflow_id') == IDS[workflow] and run.get('path') == '.github/workflows/'+workflow,
            'REGISTERED_WORKFLOW_REQUIRED')
    require(run.get('head_branch') == 'main' and run.get('repository', {}).get('full_name') == REPO
            and run.get('head_repository', {}).get('full_name') == REPO, 'ORIGINAL_MAIN_REPOSITORY_REQUIRED')
    require(run.get('event') in {'schedule', 'workflow_dispatch', 'workflow_run'}, 'NON_PUSH_RUN_REQUIRED')
    if success:
        require(run.get('status') == 'completed' and run.get('conclusion') == 'success', 'UPSTREAM_NOT_SUCCESSFUL')


def source_time(raw, day, now, *, beijing=False):
    stamp = (datetime.strptime(str(raw), '%Y-%m-%d %H:%M:%S').replace(tzinfo=TZ) if beijing
             else datetime.fromisoformat(str(raw).replace('Z', '+00:00')))
    require(stamp.tzinfo is not None, 'P0_UPSTREAM_SOURCE_TIME_REQUIRED')
    local = stamp.astimezone(TZ)
    require(local.strftime('%Y%m%d') == day and local.time() > time(15) and stamp <= now,
            'P0_UPSTREAM_SOURCE_TIME_INVALID')


def p0_dispatch_window(day, now):
    # Match the EXISTING controlled NATURAL entry point. Do not silently extend
    # it past 23:30 or present a manual recovery as a natural scheduled run.
    local = now.astimezone(TZ)
    require(local.strftime('%Y%m%d') == day and time(15) < local.time() < time(23, 30),
            'MISSING_P0_OUTSIDE_CONTROLLED_WINDOW_SCHEDULE_REQUIRED')


def missing_p0(client, base, now, cutoff):
    require(now < cutoff, 'MISSED_PREAUCTION_PUBLICATION_DEADLINE_NO_BACKFILL')
    p0_dispatch_window(base['signal_date'], now)
    day, head = base['signal_date'], base['head']
    for path in (f'outputs/decision/three_rank_top10_{day}.json',
                 f'outputs/decision/three_rank_top10_{day}.csv',
                 f'outputs/decision/primary_d_runtime_features_{day}.csv',
                 ROOT+f'candidate_natural_forward/day_{day}.json',
                 ROOT+f'candidate_natural_forward/workflow_{day}.json',
                 ROOT+f'candidate_natural_evidence/{day}/context.json',
                 PUBLIC+f'day_{day}.json'):
        require(client.file(path, head) is None, 'INCOMPLETE_P0_REQUIRES_REPAIR_NO_OVERWRITE')
    sources = client.p0_sources(day, now)
    if __package__:
        from .prepare_primary_history import inspect_history
    else:
        from prepare_primary_history import inspect_history
    history = inspect_history(client, head, day)
    if history['needs_repair']:
        return {**base, 'status': 'NEEDS_HISTORY_PREPARATION', 'workflow': HISTORY,
                'missing_paths': history['missing_paths'], 'source_ready_at': sources['source_ready_at'],
                'inputs': {'signal_date': day, 'market_commit': sources['market_commit'],
                           'expected_head': head, 'dry_run': False}}
    return {**base, 'status': 'NEEDS_P0', 'workflow': P0,
            'recovery_kind': 'CONTROLLED_DAILY_NOT_SCHEDULE_PROOF',
            'source_ready_at': sources['source_ready_at'],
            'inputs': {'trade_date': day, 'generation_mode': 'NATURAL', 'dry_run': False,
                       'confirm_daily_generation': True, 'confirm_recovery': False,
                       'pred_commit': sources['pred_commit'], 'market_commit': sources['market_commit']}}


def stamp(value):
    require(isinstance(value, str), 'P0_HANDOFF_TIMESTAMP_REQUIRED')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(result.tzinfo is not None, 'P0_HANDOFF_AWARE_TIMESTAMP_REQUIRED')
    return result


def handoff_jobs(client, run, *, reuse=False):
    """Discovery preflight only; the unchanged importer revalidates all evidence.

    A green duplicate run did not generate anything. A failed generation is
    usable by the existing continuation only when compute and CAS succeeded and
    the canonical Pages job alone failed. Inspect the original attempt's jobs,
    not the aggregate run badge or a later retry's steps.
    """
    value = client.request(f'/actions/runs/{run["id"]}/attempts/1/jobs?per_page=100')
    jobs = value.get('jobs')
    require(isinstance(jobs, list) and type(value.get('total_count')) is int
            and value['total_count'] == len(jobs) <= 100, 'P0_HANDOFF_COMPLETE_JOBS_REQUIRED')
    require(all(isinstance(j, dict) and type(j.get('id')) is int and j['id'] > 0
                and j.get('run_id') == run['id'] and isinstance(j.get('name'), str) for j in jobs)
            and len({j['id'] for j in jobs}) == len(jobs)
            and len({j['name'] for j in jobs}) == len(jobs), 'P0_HANDOFF_JOB_IDENTITY_CHANGED')
    by_name = {j['name']: j for j in jobs}
    require(set(P0_JOBS) <= set(by_name), 'P0_HANDOFF_CRITICAL_JOB_MISSING')
    compute, cas, deploy = [by_name[name] for name in P0_JOBS]
    wanted = ('success', 'skipped', 'success') if reuse else ('success', 'success', run['conclusion'])
    require(all(j.get('status') == 'completed' and j.get('conclusion') == conclusion
                for j, conclusion in zip((compute, cas, deploy), wanted))
            and all(j.get('status') == 'completed' and j.get('conclusion') in {'success', 'skipped'}
                    for j in jobs if j['name'] != P0_JOBS[2]), 'P0_HANDOFF_JOB_OUTCOMES_INVALID')
    ordered = [compute, deploy] if reuse else [compute, cas, deploy]
    require(all(stamp(run['run_started_at']) <= stamp(j.get('started_at')) <= stamp(j.get('completed_at'))
                <= stamp(run['updated_at']) for j in ordered)
            and all(stamp(a['completed_at']) <= stamp(b['started_at']) for a, b in zip(ordered, ordered[1:])),
            'P0_HANDOFF_JOB_TIME_INVALID')
    steps = compute.get('steps')
    require(isinstance(steps, list) and all(isinstance(s, dict) and type(s.get('number')) is int
                and s['number'] > 0 and isinstance(s.get('name'), str) and s.get('status') == 'completed' for s in steps)
            and len({s['number'] for s in steps}) == len(steps)
            and len({s['name'] for s in steps}) == len(steps), 'P0_HANDOFF_STEP_IDENTITY_CHANGED')
    states = {s['name']: s.get('conclusion') for s in steps}
    require(states.get('Resolve exact D and reject a completed duplicate') == 'success'
            and all(states.get(name) == ('skipped' if reuse else 'success') for name in P0_GENERATION_STEPS),
            'P0_HANDOFF_GENERATION_OR_REUSE_STEPS_INVALID')
    return cas


def controlled_run(run, day):
    title = 'DC20 controlled daily NATURAL | D='+day
    require(run.get('event') == 'workflow_dispatch' and run.get('name') == run.get('display_title') == title,
            'P0_HANDOFF_CONTROLLED_NATURAL_REQUIRED')
    local = stamp(run['created_at']).astimezone(TZ)
    require(local.strftime('%Y%m%d') == day and time(15) <= local.time() < time(23, 30),
            'P0_HANDOFF_ORIGINAL_CONTROLLED_WINDOW_REQUIRED')


def generating_run(run, day):
    if run.get('event') == 'schedule':
        require(run.get('name') == run.get('display_title') == P0_NAME,
                'P0_HANDOFF_REGISTERED_SCHEDULE_REQUIRED')
        local = stamp(run['created_at']).astimezone(TZ)
        require(local.strftime('%Y%m%d') == day and time(15) <= local.time() < time(23, 30),
                'P0_HANDOFF_ORIGINAL_SCHEDULE_WINDOW_REQUIRED')
    else:
        controlled_run(run, day)


def original_artifact(client, run, cas_job):
    value = client.request(f'/actions/runs/{run["id"]}/artifacts?per_page=100')
    items = value.get('artifacts')
    require(isinstance(items, list) and type(value.get('total_count')) is int
            and value['total_count'] == len(items) <= 100 and all(isinstance(a, dict) for a in items),
            'P0_HANDOFF_COMPLETE_ARTIFACT_LIST_REQUIRED')
    selected = [a for a in items if a.get('name') == f'primary-d-candidate-{run["id"]}-1']
    require(len(selected) == 1, 'P0_HANDOFF_ORIGINAL_ARTIFACT_REQUIRED')
    artifact = selected[0]
    origin = artifact.get('workflow_run', {})
    require(type(artifact.get('id')) is int and artifact['id'] > 0 and artifact.get('expired') is False
            and type(artifact.get('size_in_bytes')) is int and 0 < artifact['size_in_bytes'] <= 128*1024*1024
            and origin.get('id') == run['id'] and origin.get('head_branch') == 'main'
            and origin.get('head_sha') == run['head_sha']
            and re.fullmatch('sha256:[0-9a-f]{64}', artifact.get('digest', ''))
            and stamp(run['run_started_at']) <= stamp(artifact.get('created_at'))
                <= stamp(artifact.get('updated_at')) <= stamp(cas_job['started_at']),
            'P0_HANDOFF_ARTIFACT_ORIGIN_DIGEST_OR_TIME_INVALID')


def original_cas(client, base, receipt, receipt_raw, run, cas_job):
    """Locate, never create, the original CAS in main's dated-file history."""
    day, head = base['signal_date'], base['head']
    receipt_path = f'outputs/decision/primary_d_receipt_{day}.json'
    commits = client.request(f'/commits?sha={head}&path={receipt_path}&per_page=30')
    require(isinstance(commits, list) and len(commits) < 30, 'P0_HANDOFF_COMMIT_HISTORY_LIMIT')
    matches = [c for c in commits if len(c.get('parents', [])) == 1
               and c['parents'][0].get('sha') == run['head_sha']]
    require(len(matches) == 1, 'P0_HANDOFF_UNIQUE_ORIGINAL_CAS_REQUIRED')
    sha = matches[0].get('sha', '')
    require(re.fullmatch('[0-9a-f]{40}', sha), 'P0_HANDOFF_CAS_SHA_INVALID')
    commit = client.request('/git/commits/'+sha)
    require(commit.get('sha') == sha and len(commit.get('parents', [])) == 1
            and commit['parents'][0].get('sha') == run['head_sha']
            and stamp(cas_job['started_at']) <= stamp(commit.get('committer', {}).get('date'))
                <= stamp(cas_job['completed_at']), 'P0_HANDOFF_CAS_TIME_OR_PARENT_INVALID')
    bound = receipt['outputs']
    expected = {receipt_path: digest(receipt_raw),
                f'outputs/decision/three_rank_top10_{day}.json': bound['json_sha256'],
                f'outputs/decision/three_rank_top10_{day}.csv': bound['csv_sha256'],
                f'outputs/decision/primary_d_runtime_features_{day}.csv': bound['runtime_features_sha256']}
    for path, sha256 in expected.items():
        before, current = client.file(path, sha), client.file(path, head)
        require(isinstance(before, bytes) and before == current and digest(before) == sha256,
                'P0_HANDOFF_ORIGINAL_BYTES_CHANGED')
    return sha, expected


def missing_freeze(client, base, receipt, receipt_raw, now, cutoff):
    day, head = base['signal_date'], base['head']
    original_head = receipt.get('inputs', {}).get('git_head', '')
    require(re.fullmatch('[0-9a-f]{40}', original_head), 'P0_HANDOFF_ORIGINAL_HEAD_REQUIRED')
    start = datetime.strptime(day+'1500', '%Y%m%d%H%M').replace(tzinfo=TZ)
    created_filter = start.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    value = client.request('/actions/workflows/'+P0+'/runs?branch=main&status=completed'
                           +'&created=%3E'+created_filter+'&per_page=100')
    runs = value.get('workflow_runs')
    require(isinstance(runs, list) and type(value.get('total_count')) is int
            and value['total_count'] == len(runs) <= 100, 'P0_HANDOFF_COMPLETE_RUN_LIST_REQUIRED')
    trusted = []
    for run in runs:
        try:
            valid_run(run, P0, success=False)
            require(run.get('status') == 'completed' and run.get('conclusion') in {'success', 'failure'}
                    and re.fullmatch('[0-9a-f]{40}', run.get('head_sha', ''))
                    and start < stamp(run['created_at']) <= stamp(run['run_started_at'])
                        <= stamp(run['updated_at']) <= now and stamp(run['created_at']) < cutoff,
                    'P0_HANDOFF_RUN_TIME_OR_STATE_INVALID')
        except (ValueError, KeyError, TypeError):
            continue
        trusted.append(run)
    originals = [r for r in trusted if r['head_sha'] == original_head]
    # Multiple attempts from the same parent are not guessed. A failed pre-CAS
    # attempt can be ignored only when its generation/CAS jobs prove it is not
    # the receipt writer. Malformed/API-incomplete evidence fails closed.
    generated = []
    for run in originals:
        jobs = client.request(f'/actions/runs/{run["id"]}/attempts/1/jobs?per_page=100')
        require(isinstance(jobs.get('jobs'), list) and type(jobs.get('total_count')) is int
                and jobs['total_count'] == len(jobs['jobs']) <= 100, 'P0_HANDOFF_COMPLETE_JOBS_REQUIRED')
        if any(j.get('name') == P0_JOBS[1] and j.get('conclusion') == 'success' for j in jobs['jobs']):
            generated.append(run)
    if not generated:
        # The receipt becomes visible after CAS, before the original Pages job
        # completes. A completed-only search must not mislabel that live handoff
        # as missing provenance. API errors remain failures, never an empty lock.
        queue = inspect_queue(client, P0, day, now)
        if queue['same_day_target_run_ids']:
            return {**base, 'status': 'UPSTREAM_OR_RECOVERY_ALREADY_RUNNING',
                    'pending_stage': 'ORIGINAL_P0_COMPLETION', 'queue': queue}
    require(len(generated) == 1, 'ORIGINAL_GENERATING_P0_RUN_MISSING_OR_AMBIGUOUS')
    original = generated[0]
    cas_job = handoff_jobs(client, original)
    # Bind retry accounting to the receipt's original generator, never a later
    # no-op deployment or current code HEAD. Earlier-day recovery can run on D
    # morning, but cannot consume the budget of a P0 not yet created on D.
    retry_origin = {'retry_origin_run_id': str(original['id']),
                    'retry_not_before': original['created_at']}
    if original['conclusion'] == 'success':
        return {**base, **retry_origin, 'status': 'NEEDS_FREEZE', 'workflow': NATURAL,
                'inputs': {'p0_run_id': str(original['id']), 'dry_run': False}}

    # Reuse the existing, hash-pinned continuation; this discovery grants no
    # authority and does not loosen its artifact, ancestry, source or time gates.
    generating_run(original, day)
    require(stamp(cas_job['completed_at']) < datetime.strptime(day+'2330', '%Y%m%d%H%M').replace(tzinfo=TZ),
            'P0_HANDOFF_ORIGINAL_CAS_AFTER_WINDOW')
    deadline = datetime.strptime(base['exec_date']+'0915', '%Y%m%d%H%M').replace(tzinfo=TZ)
    require(now < deadline, 'CONTINUATION_NEW_FREEZE_T0915_DEADLINE')
    original_artifact(client, original, cas_job)
    cas, expected = original_cas(client, base, receipt, receipt_raw, original, cas_job)
    evidence = {'original_generation_run_id': str(original['id']), 'original_cas_commit': cas,
                'handoff_authority': 'EXISTING_PINNED_CONTINUATION_IMPORTER',
                'freeze_deadline': deadline.isoformat()}
    for reuse in sorted(trusted, key=lambda r: r['created_at'], reverse=True):
        if reuse['conclusion'] != 'success' or reuse['id'] == original['id'] or \
                stamp(reuse['created_at']) <= stamp(cas_job['completed_at']):
            continue
        if reuse['event'] == 'workflow_dispatch':
            try:
                controlled_run(reuse, day)
            except ValueError:
                continue
        # Do not match a successful run for another D or for changed P0 bytes.
        if any((body := client.file(path, reuse['head_sha'])) is None or digest(body) != sha
               for path, sha in expected.items()):
            continue
        handoff_jobs(client, reuse, reuse=True)
        return {**base, **evidence, **retry_origin, 'status': 'NEEDS_FREEZE_CONTINUATION', 'workflow': NATURAL,
                'inputs': {'p0_run_id': str(reuse['id']), 'original_generation_run_id': str(original['id']),
                           'original_cas_commit': cas, 'dry_run': False}}
    local = now.astimezone(TZ)
    if local.strftime('%Y%m%d') != day or not time(15) < local.time() < time(23, 30):
        return {**base, **evidence, 'status': 'P0_REDEPLOY_REQUIRES_EXISTING_NATURAL_SCHEDULE',
                'reason': 'NO_SUCCESSFUL_REUSE_AND_CONTROLLED_WINDOW_CLOSED'}
    sources = receipt['inputs']
    pred, market = sources.get('candidate', {}), sources.get('market', {})
    require(pred.get('source_repository') == UPSTREAMS[0] and market.get('source_repository') == UPSTREAMS[1]
            and all(re.fullmatch('[0-9a-f]{40}', s.get('resolved_commit', '')) for s in (pred, market)),
            'P0_HANDOFF_ORIGINAL_UPSTREAM_COMMITS_REQUIRED')
    return {**base, **evidence, 'status': 'NEEDS_P0_REDEPLOY', 'workflow': P0,
            'source_ready_at': cas_job['completed_at'], 'recovery_kind': 'EXISTING_P0_REUSE_ONLY',
            'inputs': {'trade_date': day, 'generation_mode': 'NATURAL', 'dry_run': False,
                       'confirm_daily_generation': True, 'confirm_recovery': False,
                       'pred_commit': pred['resolved_commit'], 'market_commit': market['resolved_commit']}}


def completion_wakeup(client, payload, now):
    """Authenticate a P0 completion wake-up; all recovery proof stays in inspect/act."""
    require(isinstance(payload, dict) and payload.get('action') == 'completed',
            'P0_COMPLETION_EVENT_REQUIRED')
    event_run = payload.get('workflow_run')
    require(isinstance(event_run, dict), 'P0_COMPLETION_RUN_REQUIRED')
    valid_run(event_run, P0, success=False)
    require(type(event_run['id']) is int, 'P0_COMPLETION_RUN_ID_REQUIRED')
    api_run = client.request(f'/actions/runs/{event_run["id"]}')
    require(isinstance(api_run, dict), 'P0_COMPLETION_API_RUN_REQUIRED')
    valid_run(api_run, P0, success=False)
    identity = ('id', 'workflow_id', 'path', 'head_branch', 'head_sha', 'run_attempt',
                'event', 'status', 'conclusion', 'name', 'display_title',
                'created_at', 'run_started_at', 'updated_at')
    require(all(event_run.get(key) == api_run.get(key) for key in identity),
            'P0_COMPLETION_API_IDENTITY_CHANGED')
    require(api_run.get('status') == 'completed' and api_run.get('conclusion') in {'success', 'failure'}
            and api_run.get('event') == 'workflow_dispatch'
            and re.fullmatch('[0-9a-f]{40}', api_run.get('head_sha', '')),
            'P0_COMPLETION_SOURCE_INVALID')
    created = stamp(api_run.get('created_at'))
    require(created <= stamp(api_run.get('run_started_at')) <= stamp(api_run.get('updated_at')) <= now,
            'P0_COMPLETION_TIME_INVALID')
    day = created.astimezone(TZ).strftime('%Y%m%d')
    # A schedule run's created_at is not its original cron D. Scheduled recovery
    # remains with the unchanged watchdog ticks and existing continuation proof.
    controlled_run(api_run, day)
    return day


def inspect(client, now, *, wakeup_day=None):
    head = client.request('/git/ref/heads/main')['object']['sha']
    activation = document(client.file('models/decision_candidate_profit_activation_v1.json', head))
    require(type(activation.get('enabled')) is bool, 'ACTIVATION_BOOLEAN_REQUIRED')
    if not activation['enabled']:
        return {'head': head, 'status': 'ACTIVATION_DISABLED_NO_RECOVERY'}
    calendar = client.file('data/market/trade_cal_sse.csv', head)
    day, trade, exit_day, cutoff = window(calendar, now)
    base = {'signal_date': day, 'exec_date': trade, 'exit_date': exit_day, 'head': head}
    if wakeup_day is not None and wakeup_day != day:
        return {**base, 'status': 'STALE_P0_COMPLETION_IGNORED', 'wakeup_signal_date': wakeup_day,
                'reason': 'COMPLETION_D_DIFFERS_FROM_CURRENT_STRICT_CALENDAR_D'}
    receipt_raw = client.file(f'outputs/decision/primary_d_receipt_{day}.json', head)
    if receipt_raw is None:
        return missing_p0(client, base, now, cutoff)
    receipt = document(receipt_raw)
    require(receipt.get('signal_date') == day and receipt.get('exec_date') == trade and receipt.get('exit_date') == exit_day
            and receipt.get('primary_status') == 'READY' and receipt.get('generation_mode') == 'NATURAL', 'CURRENT_NATURAL_P0_REQUIRED')
    require(receipt.get('inputs', {}).get('calendar', {}).get('sha256') == digest(calendar), 'P0_CALENDAR_BINDING_CHANGED')
    p0_raw = client.file(f'outputs/decision/three_rank_top10_{day}.json', head)
    require(digest(p0_raw) == receipt['outputs']['json_sha256'], 'P0_BYTES_CHANGED')
    p0 = document(p0_raw)
    rows = p0.get('rows')
    require(type(rows) is list and len(rows) <= 10
            and all(type(r) is dict and type(r.get('promotion_rank')) is int for r in rows)
            and [r['promotion_rank'] for r in rows] == list(range(1, len(rows) + 1)),
            'PROMOTION_RANK_SEQUENCE_INVALID')
    public_raw = client.file(PUBLIC+f'day_{day}.json', head)
    index_raw = client.file(PUBLIC+'index.json', head)
    if public_raw is not None and index_raw is not None:
        public, index = document(public_raw), document(index_raw)
        entry = next((r for r in index['days'] if r['signal_date'] == day), None)
        if entry is not None:
            require(entry['sha256'] == digest(public_raw) and public.get('p0_file_sha256') == digest(p0_raw)
                    and public.get('signal_date') == day and public.get('exec_date') == trade
                    and public.get('exit_date') == exit_day, 'FORMAL_DAY_BINDING_CHANGED')
            top2 = public['rows'][:2]
            require([r['candidate_rank'] for r in top2] == list(range(1, len(top2) + 1))
                    and len({r['ts_code'] for r in top2}) == len(top2), 'PROFIT_RANK_SEQUENCE_INVALID')
            if len(top2) < 2:
                slots = public.get('candidate_slots')
                require(type(slots) is list and len(slots) == 2, 'PROFIT_ABSENT_SLOT_PROOF_REQUIRED')
                for number in range(len(top2), 2):
                    slot = slots[number]
                    require(slot.get('slot') == number + 1 and slot.get('status') == 'MISSING_CANDIDATE'
                            and all(slot.get(k) is None for k in ('ts_code', 'candidate_rank', 'candidate_score',
                                'promotion_rank', 'board_stage', 'proxy_fill', 'net_return', 'slot_net_return')),
                            'PROFIT_ABSENT_SLOT_MUST_NOT_CLAIM_CANDIDATE')
            core = client.public_core_status(day, p0_raw, index_raw, public_raw)
            if core is not None:
                return {**base, **core, 'top2': [r['ts_code'] for r in top2]}
            summary_raw = client.file(PUBLIC+'summary.json', head)
            summary = document(summary_raw)
            for number, group in enumerate(('candidate_top1', 'candidate_top2')):
                row = next((r for r in summary['groups'][group]['daily_sequence'] if r['signal_date'] == day), None)
                expected_code = top2[number]['ts_code'] if number < len(top2) else None
                require(row is not None and row.get('ts_code') == expected_code
                        and row.get('formal_projection_sha256') == canonical(public), 'FORMAL_LEDGER_NOT_BOUND')
                if expected_code is None:
                    require(row.get('status') == 'MISSING_CANDIDATE'
                            and all(row.get(k) is None for k in ('candidate_rank', 'candidate_score', 'promotion_rank',
                                'proxy_fill', 'slot_net_return')), 'FORMAL_MISSING_SLOT_CANNOT_CLAIM_ECONOMICS')
            if not client.public_ready(day, digest(public_raw), digest(summary_raw), p0_raw):
                return {**base, 'status': 'NEEDS_DEPLOYMENT', 'existing_formal': True,
                        'workflow': PUBLISHER, 'inputs': {'dry_run': False}}
            return {**base, 'status': 'FORMAL_TOP2_LEDGER_AND_PAGE_PRESENT' if len(top2) == 2 else 'FORMAL_AVAILABLE_SLOTS_LEDGER_AND_PAGE_PRESENT',
                    'top2': [r['ts_code'] for r in top2], 'missing_profit_slots': 2 - len(top2),
                    'available_promotion_slots': min(3, len(rows))}
    require(now < cutoff, 'MISSED_PREAUCTION_PUBLICATION_DEADLINE_NO_BACKFILL')
    snapshot_raw = client.file(ROOT+f'candidate_natural_forward/day_{day}.json', head)
    workflow_raw = client.file(ROOT+f'candidate_natural_forward/workflow_{day}.json', head)
    if snapshot_raw is None:
        require(workflow_raw is None, 'INCOMPLETE_FREEZE_REQUIRES_REPAIR')
        for kind in ('p0_sources', 'local_freeze'):
            require(client.file(ROOT+f'candidate_natural_forward/{kind}_{day}.json', head) is None,
                    'INCOMPLETE_FREEZE_REQUIRES_REPAIR')
        return missing_freeze(client, base, receipt, receipt_raw, now, cutoff)
    require(workflow_raw is not None and all(client.file(ROOT+f'candidate_natural_forward/{kind}_{day}.json', head) is not None
            for kind in ('p0_sources', 'local_freeze')),
            'INCOMPLETE_FREEZE_REQUIRES_REPAIR_ORIGINAL_ARTIFACT_AND_UNIQUE_CAS_NO_OVERWRITE')
    snapshot, frozen = document(snapshot_raw), document(workflow_raw)
    require(frozen.get('signal_date') == day and frozen.get('snapshot_file_sha256') == digest(snapshot_raw)
            and snapshot.get('signal_date') == day and snapshot.get('exec_date') == trade
            and snapshot.get('exit_date') == exit_day, 'FROZEN_DAY_BINDING_CHANGED')
    context_raw = client.file(ROOT+f'candidate_natural_evidence/{day}/context.json', head)
    if context_raw is None:
        run = client.request(f'/actions/runs/{frozen["run_id"]}')
        valid_run(run, NATURAL, success=False)
        if run.get('status') in {'queued', 'in_progress', 'pending', 'waiting', 'requested'}:
            return {**base, 'status': 'FREEZE_STILL_RUNNING'}
        valid_run(run, NATURAL)
        return {**base, 'status': 'NEEDS_OBSERVER', 'workflow': OBSERVER,
                'inputs': {'freeze_run_id': str(run['id']), 'dry_run': False}}
    context = document(context_raw)
    require(context.get('signal_date') == day and context.get('freeze_run_id') == frozen['run_id']
            and context.get('snapshot_file_sha256') == digest(snapshot_raw), 'OBSERVER_CONTEXT_MISMATCH')
    observer_run = client.request(f'/actions/runs/{context["observer_run_id"]}')
    valid_run(observer_run, OBSERVER, success=False)
    if observer_run.get('status') in {'queued', 'in_progress', 'pending', 'waiting', 'requested'}:
        return {**base, 'status': 'OBSERVER_STILL_RUNNING'}
    if observer_run.get('conclusion') == 'failure':
        # Read-only preflight. Publisher independently verifies the complete
        # original ACK archive, capsule, CAS and real publication timestamps.
        from work.profit_1000_upgrade import candidate_natural_evidence_publication as evidence
        evidence._run(observer_run, client.request('/actions/workflows/'+OBSERVER), observer_run['id'])
        evidence._jobs(client.request(f'/actions/runs/{observer_run["id"]}/attempts/1/jobs?per_page=100'), observer_run)
        evidence._artifact(client.request(f'/actions/runs/{observer_run["id"]}/artifacts?per_page=100'), observer_run)
    else:
        valid_run(observer_run, OBSERVER)
    return {**base, 'status': 'NEEDS_PUBLICATION', 'workflow': PUBLISHER, 'inputs': {'dry_run': False}}


def act(client, plan, now, *, execute=False, clock=None):
    if 'workflow' not in plan:
        return plan
    workflow = plan['workflow']
    queue = inspect_queue(client, workflow, plan['signal_date'], now)
    plan = {**plan, 'queue': queue}
    if queue['status'] != 'CLEAR':
        return {**plan, 'status': 'UPSTREAM_OR_RECOVERY_ALREADY_RUNNING'}
    runs = client.request('/actions/workflows/'+workflow+'/runs?per_page=50')['workflow_runs']
    relevant = [r for r in runs if r.get('head_branch') == 'main' and r.get('event') != 'push']
    failures = [r for r in relevant if r.get('conclusion') in {'failure', 'timed_out'} and
        datetime.fromisoformat(r['created_at'].replace('Z', '+00:00')).astimezone(TZ).strftime('%Y%m%d') >= plan['signal_date']]
    if workflow == NATURAL:
        origin = plan['inputs'].get('original_generation_run_id') or plan['inputs']['p0_run_id']
        require(plan.get('retry_origin_run_id') == origin, 'RETRY_ORIGINAL_P0_BINDING_REQUIRED')
        earliest = stamp(plan.get('retry_not_before'))
        start = datetime.strptime(plan['signal_date']+'1500', '%Y%m%d%H%M').replace(tzinfo=TZ)
        require(start < earliest <= now, 'RETRY_ORIGINAL_P0_TIME_INVALID')
        # Conservatively count EVERY failure since the original creation,
        # including failed preflight and failures before a successful reuse.
        # No reset on midnight, code changes, or successor deployment.
        failures = [r for r in failures if stamp(r['created_at']) >= earliest]
    if workflow in (P0, HISTORY):
        p0_dispatch_window(plan['signal_date'], now)
        # Failed scheduled attempts before data arrived must not consume the
        # retry budget for a newly ready, pinned source generation.
        ready = datetime.fromisoformat(plan['source_ready_at'])
        failures = [r for r in failures if workflow == HISTORY or (r.get('event') == 'workflow_dispatch'
                    and r.get('display_title') == 'DC20 controlled daily NATURAL | D='+plan['signal_date']
                    and datetime.fromisoformat(r['created_at'].replace('Z', '+00:00')) >= ready)]
    require(len(failures) < 3, 'RETRY_BUDGET_EXHAUSTED_REQUIRES_REPAIR')
    cutoff = datetime.strptime(plan['exec_date']+'0920', '%Y%m%d%H%M').replace(tzinfo=TZ)
    if plan.get('freeze_deadline'):
        cutoff = min(cutoff, stamp(plan['freeze_deadline']))
    require(plan.get('existing_formal') is True or now < cutoff, 'DISPATCH_DEADLINE_EXPIRED')
    if workflow == NATURAL:
        approval = client.candidate_code_approval(plan['head'])
        require(approval.get('repository') == REPO and approval.get('current_head_sha') == plan['head']
                and approval.get('daily_source_evidence_admitted') is False
                and approval.get('production_freeze_issued') is False,
                'DISPATCH_CODE_APPROVAL_BINDING_CHANGED')
        plan = {**plan, 'code_approval': approval, 'prior_failed_attempts': len(failures)}
        if approval.get('status') == 'WAITING_FOR_CODE_CI_APPROVAL':
            # Preserve the original retry budget. Waiting here creates no run,
            # does not reset past failures, and cannot extend either deadline.
            return {**plan, 'status': 'WAITING_FOR_CODE_CI_APPROVAL'}
        require(approval.get('status') == 'APPROVED_IDENTICAL_STATIC_SURFACE',
                'DISPATCH_CODE_APPROVAL_NOT_VERIFIED')
    if not execute:
        return {**plan, 'status': 'DRY_RUN_WOULD_DISPATCH'}
    require(client.request('/git/ref/heads/main')['object']['sha'] == plan['head'], 'MAIN_MOVED_RECHECK_NEXT_TICK')
    queue = inspect_queue(client, workflow, plan['signal_date'], now)
    plan = {**plan, 'queue': queue}
    if queue['status'] != 'CLEAR':
        return {**plan, 'status': 'UPSTREAM_OR_RECOVERY_ALREADY_RUNNING'}
    require(client.request('/git/ref/heads/main')['object']['sha'] == plan['head'], 'MAIN_MOVED_RECHECK_NEXT_TICK')
    # Real queue/API reads may cross a cutoff. The executable entry injects a
    # live clock; deterministic callers may provide their own advancing clock.
    dispatch_now = clock() if clock is not None else now
    require(dispatch_now.tzinfo is not None and dispatch_now >= now, 'DISPATCH_CLOCK_INVALID')
    if workflow in (P0, HISTORY):
        p0_dispatch_window(plan['signal_date'], dispatch_now)
    require(plan.get('existing_formal') is True or dispatch_now < cutoff, 'DISPATCH_DEADLINE_EXPIRED')
    client.request('/actions/workflows/'+workflow+'/dispatches', {'ref': 'main', 'inputs': plan['inputs']})
    return {**plan, 'status': 'DISPATCHED_NOT_COMPLETED'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if args.execute:
        require(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('GITHUB_REPOSITORY') == REPO
            and os.environ.get('GITHUB_REF') == 'refs/heads/main' and os.environ.get('GITHUB_RUN_ATTEMPT') == '1'
            and os.environ.get('GITHUB_WORKFLOW_REF') == REPO+'/'+WORKFLOW+'@refs/heads/main'
            and os.environ.get('GITHUB_EVENT_NAME') in {'schedule', 'workflow_dispatch', 'workflow_run'}, 'REGISTERED_WATCHDOG_ONLY')
    client = GitHub(os.environ.get('GH_TOKEN'))
    try:
        now = datetime.now(timezone.utc)
        wakeup_day = None
        if os.environ.get('GITHUB_EVENT_NAME') == 'workflow_run':
            with open(os.environ['GITHUB_EVENT_PATH'], encoding='utf-8') as event_file:
                wakeup_day = completion_wakeup(client, json.load(event_file), now)
        plan = inspect(client, now, wakeup_day=wakeup_day)
        result = act(client, plan, datetime.now(timezone.utc), execute=args.execute,
                     clock=lambda: datetime.now(timezone.utc))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except (ValueError, KeyError, TypeError) as exc:
        # Only explicit machine reason codes, never a token, raw payload or URL.
        reason = str(exc) if re.fullmatch('[A-Z0-9_]+', str(exc)) else 'UNEXPECTED_VALIDATION_FAILURE'
        print('::error title=DC20 daily Top2 delivery::'+reason)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())



