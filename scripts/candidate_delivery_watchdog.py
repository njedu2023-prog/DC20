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

REPO = 'njedu2023-prog/DC20'
WORKFLOW = '.github/workflows/candidate_delivery_watchdog.yml'
TZ = timezone(timedelta(hours=8))
NATURAL = 'research_candidate_natural_forward.yml'
OBSERVER = 'research_candidate_natural_observer.yml'
PUBLISHER = 'publish_candidate_profit.yml'
P0 = 'run_primary_d_daily.yml'
IDS = {P0: 343703608, NATURAL: 357010624, OBSERVER: 357027830}
ROOT = 'work/profit_1000_upgrade/'
PUBLIC = 'outputs/decision/candidate_profit_v1/'


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

    def request(self, path, body=None):
        self.calls += 1
        require(self.calls <= 60 and path.startswith('/'), 'REQUEST_BUDGET_EXCEEDED')
        if body is not None:
            require(path in {f'/actions/workflows/{w}/dispatches' for w in (NATURAL, OBSERVER, PUBLISHER)}, 'DISPATCH_TARGET_REJECTED')
            require(body.get('ref') == 'main', 'MAIN_ONLY')
        req = urllib.request.Request('https://api.github.com/repos/'+REPO+path,
            data=None if body is None else json.dumps(body).encode(), headers={
                'Authorization': 'Bearer '+self.token, 'Accept': 'application/vnd.github+json',
                'X-GitHub-Api-Version': '2022-11-28'})
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read(8*1024*1024+1)
            require(len(raw) <= 8*1024*1024, 'RESPONSE_TOO_LARGE')
            return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            if body is None and exc.code == 404 and path.startswith('/contents/'):
                return None
            raise ValueError('GITHUB_HTTP_'+str(exc.code)) from None
        except (TimeoutError, urllib.error.URLError):
            raise ValueError('DISPATCH_OUTCOME_UNKNOWN_NO_BLIND_RETRY' if body is not None else 'GITHUB_READ_UNAVAILABLE') from None

    def file(self, path, head):
        require(re.fullmatch('[0-9a-f]{40}', head), 'EXACT_HEAD_REQUIRED')
        value = self.request('/contents/'+path+'?ref='+head)
        if value is None:
            return None
        require(value.get('type') == 'file' and value.get('encoding') == 'base64', 'REGULAR_SMALL_FILE_REQUIRED')
        raw = base64.b64decode(value['content'])
        require(hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() == value['sha'], 'GIT_BLOB_MISMATCH')
        return raw

    def public_ready(self, day, day_sha, summary_sha):
        try:
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


def inspect(client, now):
    head = client.request('/git/ref/heads/main')['object']['sha']
    activation = document(client.file('models/decision_candidate_profit_activation_v1.json', head))
    require(type(activation.get('enabled')) is bool, 'ACTIVATION_BOOLEAN_REQUIRED')
    if not activation['enabled']:
        return {'head': head, 'status': 'ACTIVATION_DISABLED_NO_RECOVERY'}
    calendar = client.file('data/market/trade_cal_sse.csv', head)
    day, trade, exit_day, cutoff = window(calendar, now)
    base = {'signal_date': day, 'exec_date': trade, 'exit_date': exit_day, 'head': head}
    receipt = document(client.file(f'outputs/decision/primary_d_receipt_{day}.json', head))
    require(receipt.get('signal_date') == day and receipt.get('exec_date') == trade and receipt.get('exit_date') == exit_day
            and receipt.get('primary_status') == 'READY' and receipt.get('generation_mode') == 'NATURAL', 'CURRENT_NATURAL_P0_REQUIRED')
    require(receipt.get('inputs', {}).get('calendar', {}).get('sha256') == digest(calendar), 'P0_CALENDAR_BINDING_CHANGED')
    p0_raw = client.file(f'outputs/decision/three_rank_top10_{day}.json', head)
    require(digest(p0_raw) == receipt['outputs']['json_sha256'], 'P0_BYTES_CHANGED')
    p0 = document(p0_raw)
    require(len(p0['rows']) >= 3 and [r['promotion_rank'] for r in p0['rows'][:3]] == [1, 2, 3], 'PROMOTION_TOP3_MISSING')
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
            require(len(top2) == 2 and [r['candidate_rank'] for r in top2] == [1, 2]
                    and len({r['ts_code'] for r in top2}) == 2, 'PROFIT_TOP2_MISSING')
            summary_raw = client.file(PUBLIC+'summary.json', head)
            summary = document(summary_raw)
            for number, group in enumerate(('candidate_top1', 'candidate_top2')):
                row = next((r for r in summary['groups'][group]['daily_sequence'] if r['signal_date'] == day), None)
                require(row is not None and row.get('ts_code') == top2[number]['ts_code']
                        and row.get('formal_projection_sha256') == canonical(public), 'FORMAL_LEDGER_NOT_BOUND')
            if not client.public_ready(day, digest(public_raw), digest(summary_raw)):
                return {**base, 'status': 'NEEDS_DEPLOYMENT', 'existing_formal': True,
                        'workflow': PUBLISHER, 'inputs': {'dry_run': False}}
            return {**base, 'status': 'FORMAL_TOP2_LEDGER_AND_PAGE_PRESENT', 'top2': [r['ts_code'] for r in top2]}
    require(now < cutoff, 'MISSED_PREAUCTION_PUBLICATION_DEADLINE_NO_BACKFILL')
    snapshot_raw = client.file(ROOT+f'candidate_natural_forward/day_{day}.json', head)
    workflow_raw = client.file(ROOT+f'candidate_natural_forward/workflow_{day}.json', head)
    if snapshot_raw is None:
        require(workflow_raw is None, 'INCOMPLETE_FREEZE_REQUIRES_REPAIR')
        runs = client.request('/actions/workflows/'+P0+'/runs?status=success&per_page=30')['workflow_runs']
        candidates = []
        for run in runs:
            created = datetime.fromisoformat(run['created_at'].replace('Z', '+00:00'))
            if created.astimezone(TZ).strftime('%Y%m%d') != day:
                continue
            try:
                valid_run(run, P0)
            except ValueError:
                continue
            # A successful redeploy is not a generating P0. Require the immutable
            # artifact-upload step before passing the run to the original importer.
            jobs = client.request(f'/actions/runs/{run["id"]}/jobs?per_page=100')['jobs']
            if any(s.get('name') == 'Upload immutable P0 candidate' and s.get('conclusion') == 'success'
                   for j in jobs for s in j.get('steps', [])):
                candidates.append(run['id'])
            if len(candidates) >= 1:
                break
        require(candidates, 'ORIGINAL_GENERATING_P0_RUN_MISSING')
        return {**base, 'status': 'NEEDS_FREEZE', 'workflow': NATURAL,
                'inputs': {'p0_run_id': str(candidates[0]), 'dry_run': False}}
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
    valid_run(observer_run, OBSERVER)
    return {**base, 'status': 'NEEDS_PUBLICATION', 'workflow': PUBLISHER, 'inputs': {'dry_run': False}}


def act(client, plan, now, *, execute=False):
    if 'workflow' not in plan:
        return plan
    workflow = plan['workflow']
    runs = client.request('/actions/workflows/'+workflow+'/runs?per_page=50')['workflow_runs']
    relevant = [r for r in runs if r.get('head_branch') == 'main' and r.get('event') != 'push']
    if any(r.get('status') != 'completed' for r in relevant):
        return {**plan, 'status': 'UPSTREAM_OR_RECOVERY_ALREADY_RUNNING'}
    failures = [r for r in relevant if r.get('conclusion') in {'failure', 'timed_out'} and
        datetime.fromisoformat(r['created_at'].replace('Z', '+00:00')).astimezone(TZ).strftime('%Y%m%d') >= plan['signal_date']]
    require(len(failures) < 3, 'RETRY_BUDGET_EXHAUSTED_REQUIRES_REPAIR')
    cutoff = datetime.strptime(plan['exec_date']+'0920', '%Y%m%d%H%M').replace(tzinfo=TZ)
    require(plan.get('existing_formal') is True or now < cutoff, 'DISPATCH_DEADLINE_EXPIRED')
    if not execute:
        return {**plan, 'status': 'DRY_RUN_WOULD_DISPATCH'}
    require(client.request('/git/ref/heads/main')['object']['sha'] == plan['head'], 'MAIN_MOVED_RECHECK_NEXT_TICK')
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
            and os.environ.get('GITHUB_EVENT_NAME') in {'schedule', 'workflow_dispatch'}, 'REGISTERED_WATCHDOG_ONLY')
    client = GitHub(os.environ.get('GH_TOKEN'))
    try:
        plan = inspect(client, datetime.now(timezone.utc))
        result = act(client, plan, datetime.now(timezone.utc), execute=args.execute)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except (ValueError, KeyError, TypeError) as exc:
        # Only explicit machine reason codes, never a token, raw payload or URL.
        reason = str(exc) if re.fullmatch('[A-Z0-9_]+', str(exc)) else 'UNEXPECTED_VALIDATION_FAILURE'
        print('::error title=DC20 daily Top2 delivery::'+reason)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
