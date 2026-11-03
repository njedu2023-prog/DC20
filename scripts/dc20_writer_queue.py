"""Read-only, bounded queue evidence for the existing DC20 daily dispatcher.

GitHub's run status is not proof of a concurrency lease. An old queued run
can be quarantined only after live group, run and zero-job checks agree.
This module never cancels a run, changes a ref, or dispatches a workflow.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import re
from urllib.parse import quote

REPO = 'njedu2023-prog/DC20'
GROUP = 'decision-auction-main-writer'
TZ = timezone(timedelta(hours=8))
ACTIVE = ('queued', 'requested', 'waiting', 'pending', 'in_progress')
MAX_PAGES = 10
MAX_ORPHAN_CHECKS = 10
ORPHAN_AGE = timedelta(hours=24)
WRITERS = frozenset({
    'run_primary_d_daily.yml', 'run_primary_profit_rankings.yml',
    'run_primary_profit_forward_shadow.yml', 'verify_decision_observations.yml',
    'run_decision_daily.yml', 'run_auction_v3.yml',
    'backfill_decision_v11_history.yml', 'train_decision_three_engines.yml',
    'migrate_decision_runtime.yml',
})
TARGETS = WRITERS | frozenset({
    'research_candidate_natural_forward.yml',
    'research_candidate_natural_observer.yml', 'publish_candidate_profit.yml',
})


def require(value, reason):
    if not value:
        raise ValueError('QUEUE_' + reason)


def stamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        raise ValueError('QUEUE_TIMESTAMP_INVALID') from None
    require(result.tzinfo is not None, 'TIMESTAMP_INVALID')
    return result


def collection(value, key):
    require(isinstance(value, dict), 'RESPONSE_INVALID')
    rows, total = value.get(key), value.get('total_count')
    require(isinstance(rows, list) and type(total) is int and total >= 0,
            'RESPONSE_INVALID')
    require(all(isinstance(row, dict) for row in rows), 'RESPONSE_INVALID')
    return rows, total


def writer_group(client):
    # A successful list proves endpoint permission/support before interpreting
    # the documented inactive-group 404. Cursor-incomplete evidence is rejected.
    groups, total = collection(client.request('/actions/concurrency_groups?per_page=100'),
                               'concurrency_groups')
    require(len(groups) == total, 'GROUP_LIST_INCOMPLETE')
    names = [g.get('group_name') for g in groups]
    require(all(isinstance(n, str) and n for n in names) and len(set(names)) == len(names),
            'GROUP_LIST_INVALID')
    value = client.request('/actions/concurrency_groups/' + GROUP)
    if value is None:
        require(GROUP not in names, 'GROUP_CHANGED_RECHECK')
        return []
    members, count = collection(value, 'group_members')
    require(value.get('group_name') == GROUP and len(members) == count and count > 0,
            'GROUP_RESPONSE_INVALID')
    require(all(type(r.get('run_id')) is int and r['run_id'] > 0 and
                r.get('status') in ACTIVE for r in members), 'GROUP_MEMBERS_INVALID')
    return members


def active_runs(client):
    """Page each live status; never infer no active work from a recent-50 list."""
    found = {}
    for status in ACTIVE:
        seen = set()
        for page in range(1, MAX_PAGES + 1):
            path = f'/actions/runs?branch=main&status={status}&per_page=100&page={page}'
            rows, total = collection(client.request(path), 'workflow_runs')
            require(total <= 1000, 'RUN_LIST_TOO_LARGE')
            for run in rows:
                run_id = run.get('id')
                require(type(run_id) is int and run_id > 0 and run_id not in seen,
                        'RUN_LIST_INVALID')
                seen.add(run_id)
                # A run may advance between status requests. Preserve any
                # non-queued observation rather than downgrade it to an orphan.
                prior = found.get(run_id)
                priority = {'completed': 0, 'queued': 1, 'requested': 2,
                            'waiting': 3, 'pending': 4, 'in_progress': 5}
                if prior is None or priority.get(run.get('status'), 6) > priority.get(prior.get('status'), 6):
                    found[run_id] = run
            if len(seen) == total:
                break
            require(rows and len(seen) < total and page < MAX_PAGES,
                    'RUN_LIST_INCOMPLETE')
    return list(found.values())


def recent_target_runs(client, workflow, signal_date):
    """Status-independent final scan catches runs moving between status pages."""
    start = datetime.strptime(signal_date, '%Y%m%d').replace(tzinfo=TZ) - timedelta(days=1)
    created = quote('>=' + start.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), safe='')
    found = {}
    for page in range(1, MAX_PAGES + 1):
        path = (f'/actions/workflows/{workflow}/runs?branch=main&created={created}'
                f'&per_page=100&page={page}')
        rows, total = collection(client.request(path), 'workflow_runs')
        require(total <= 1000, 'TARGET_LIST_TOO_LARGE')
        for run in rows:
            run_id = run.get('id')
            require(type(run_id) is int and run_id > 0 and run_id not in found,
                    'TARGET_LIST_INVALID')
            require(identity(run, run_id) == workflow and stamp(run.get('created_at')) >= start,
                    'TARGET_LIST_INVALID')
            found[run_id] = run
        if len(found) == total:
            return list(found.values())
        require(rows and len(found) < total and page < MAX_PAGES, 'TARGET_LIST_INCOMPLETE')
    raise ValueError('QUEUE_TARGET_LIST_INCOMPLETE')


def identity(run, run_id):
    require(isinstance(run, dict) and run.get('id') == run_id and
            run.get('head_branch') == 'main' and
            run.get('repository', {}).get('full_name') == REPO and
            run.get('head_repository', {}).get('full_name') == REPO,
            'RUN_IDENTITY_INVALID')
    path = run.get('path')
    require(isinstance(path, str) and re.fullmatch(r'\.github/workflows/[A-Za-z0-9_-]+\.yml', path),
            'RUN_IDENTITY_INVALID')
    require(type(run.get('run_attempt')) is int and run['run_attempt'] >= 1,
            'RUN_IDENTITY_INVALID')
    require(run.get('status') in (*ACTIVE, 'completed'), 'RUN_STATUS_INVALID')
    return path.rsplit('/', 1)[-1]


def old_queued(run, signal_date, now):
    created = stamp(run.get('created_at'))
    return (run.get('status') == 'queued' and run.get('run_attempt') == 1 and
            now - created >= ORPHAN_AGE and
            created.astimezone(TZ).strftime('%Y%m%d') < signal_date)


def unchanged_run(before, after):
    return all(before.get(key) == after.get(key) for key in (
        'created_at', 'path', 'workflow_id', 'head_sha', 'display_title', 'event',
    ))


def no_run_lease(client, run_id):
    groups, total = collection(client.request(f'/actions/runs/{run_id}/concurrency_groups?per_page=100'),
                               'concurrency_groups')
    require(len(groups) == total, 'RUN_GROUPS_INCOMPLETE')
    for group in groups:
        require(isinstance(group.get('group_name'), str) and
                isinstance(group.get('group_members'), list), 'RUN_GROUPS_INVALID')
        for member in group['group_members']:
            require(isinstance(member, dict) and type(member.get('run_id')) is int and
                    member.get('status') in ACTIVE, 'RUN_GROUPS_INVALID')
            if member['run_id'] == run_id:
                return False
    return True


def inspect_queue(client, workflow, signal_date, now):
    require(workflow in TARGETS and re.fullmatch(r'20\d{6}', signal_date or ''),
            'TARGET_INVALID')
    require(now.tzinfo is not None, 'TIMESTAMP_INVALID')
    members = writer_group(client)
    blocking = {r['run_id'] for r in members}
    same_day, orphans = set(), set()
    checked = 0
    for run in active_runs(client):
        run_id = run['id']
        name = identity(run, run_id)
        if name not in WRITERS and name != workflow:
            continue  # Other CI is not a writer; live writer leases above win.
        if run['status'] == 'completed':
            continue  # It finished while the status pages were being read.
        created = stamp(run.get('created_at'))
        current = created.astimezone(TZ).strftime('%Y%m%d') >= signal_date
        tagged_current = ('D=' + signal_date) in str(run.get('display_title', ''))
        if name == workflow and (current or tagged_current):
            same_day.add(run_id)
        if not old_queued(run, signal_date, now) or current or tagged_current or run_id in blocking:
            blocking.add(run_id)
            continue
        checked += 1
        require(checked <= MAX_ORPHAN_CHECKS, 'ORPHAN_CHECK_BUDGET_EXCEEDED')
        fresh = client.request(f'/actions/runs/{run_id}')
        require(identity(fresh, run_id) == name, 'RUN_IDENTITY_CHANGED')
        if fresh['status'] == 'completed':
            continue
        if not old_queued(fresh, signal_date, now) or not unchanged_run(run, fresh):
            blocking.add(run_id)
            continue
        jobs, total = collection(client.request(f'/actions/runs/{run_id}/jobs?per_page=100'), 'jobs')
        require(len(jobs) == min(total, 100), 'JOBS_INCOMPLETE')
        if total != 0 or jobs or not no_run_lease(client, run_id):
            blocking.add(run_id)
            continue
        final = client.request(f'/actions/runs/{run_id}')
        require(identity(final, run_id) == name, 'RUN_IDENTITY_CHANGED')
        if final['status'] == 'completed':
            continue
        if not old_queued(final, signal_date, now) or not unchanged_run(fresh, final):
            blocking.add(run_id)
            continue
        orphans.add(run_id)
    if not blocking:
        # Independent target workflows have their own concurrency groups. An
        # unfiltered recent scan is essential even if the shared writer is idle.
        for run in recent_target_runs(client, workflow, signal_date):
            if run['status'] != 'completed':
                blocking.add(run['id'])
                if (stamp(run['created_at']).astimezone(TZ).strftime('%Y%m%d') >= signal_date
                        or ('D=' + signal_date) in str(run.get('display_title', ''))):
                    same_day.add(run['id'])
        members = writer_group(client)
        blocking.update(r['run_id'] for r in members)
        orphans.difference_update(blocking)
    return {'status': 'WAIT' if blocking else 'CLEAR',
            'writer_group_active': bool(members),
            'same_day_target_run_ids': sorted(same_day),
            'blocking_run_ids': sorted(blocking), 'orphan_run_ids': sorted(orphans),
            'checked_at': now.isoformat()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--signal-date', required=True)
    parser.add_argument('--workflow', default='run_primary_d_daily.yml', choices=sorted(TARGETS))
    args = parser.parse_args()
    # Import only the existing read client. This CLI has no execute/cancel flag.
    if __package__:
        from .candidate_delivery_watchdog import GitHub
    else:
        from candidate_delivery_watchdog import GitHub
    try:
        result = inspect_queue(GitHub(os.environ.get('GH_TOKEN')), args.workflow,
                               args.signal_date, datetime.now(timezone.utc))
        print(json.dumps(result, sort_keys=True))
    except (ValueError, KeyError, TypeError) as exc:
        reason = str(exc) if re.fullmatch('[A-Z0-9_]+', str(exc)) else 'QUEUE_VALIDATION_FAILURE'
        print(json.dumps({'status': 'BLOCKED', 'reason': reason}, sort_keys=True))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
