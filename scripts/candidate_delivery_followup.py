"""Follow a registered Ridge handoff through the verified public daily ranking."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from time import sleep

if __package__:
    from . import candidate_delivery_watchdog as delivery
else:
    import candidate_delivery_watchdog as delivery

TERMINAL = {'FORMAL_TOP2_LEDGER_AND_PAGE_PRESENT', 'FORMAL_AVAILABLE_SLOTS_LEDGER_AND_PAGE_PRESENT',
            'CORE_DELIVERED_AUX_PENDING'}
PENDING = {'queued', 'in_progress', 'pending', 'waiting', 'requested'}
STAGES = (delivery.NATURAL, delivery.OBSERVER, delivery.PUBLISHER)
FAILED = {'failure', 'timed_out', 'startup_failure', 'action_required', 'stale', 'cancelled'}
TRANSIENT = {'GITHUB_READ_UNAVAILABLE', 'PUBLIC_PAGES_READ_UNAVAILABLE', 'QUEUE_RUN_LIST_INCOMPLETE',
             'GROUP_CHANGED_RECHECK', 'MAIN_MOVED_RECHECK_NEXT_TICK', 'PAGES_REVISION_CHANGED_DURING_CHECK',
             'GITHUB_HTTP_403', 'GITHUB_HTTP_429', 'GITHUB_HTTP_500', 'GITHUB_HTTP_502',
             'GITHUB_HTTP_503', 'GITHUB_HTTP_504'}


def utc_now():
    return datetime.now(timezone.utc)


def stage_runs(client, workflow, day, earliest):
    return [r for r in delivery.recent_target_runs(client, workflow, day)
            if r['event'] in ('workflow_dispatch', 'workflow_run', 'schedule') and r['run_attempt'] == 1
            and delivery.stamp(r['created_at']) >= earliest]


def completed_writer(client, target, runs):
    name = {delivery.OBSERVER: 'Verify and preserve original publication evidence',
            delivery.PUBLISHER: 'publish'}[target]
    for run in runs:
        if run.get('conclusion') != 'success':
            continue
        listing = client.request(f"/actions/runs/{run['id']}/attempts/1/jobs?per_page=100")
        jobs = listing.get('jobs')
        delivery.require(isinstance(jobs, list) and len(jobs) == listing.get('total_count')
                         and all(j.get('run_id') == run['id'] for j in jobs), 'FOLLOWUP_JOBS_INCOMPLETE')
        if any(j.get('name') == name and j.get('conclusion') == 'success' for j in jobs):
            return run['id']
    return None


def after_prerequisite(client, plan, runs):
    """A terminal writer that ended before today's verified upstream cannot deliver it."""
    if not runs or plan['status'] not in ('NEEDS_OBSERVER', 'NEEDS_PUBLICATION'):
        return runs
    if plan['workflow'] == delivery.OBSERVER:
        upstream = delivery.NATURAL
        run_id = int(plan['inputs']['freeze_run_id'])
    else:
        path = delivery.ROOT + f"candidate_natural_evidence/{plan['signal_date']}/context.json"
        context = delivery.document(client.file(path, plan['head']))
        delivery.require(context.get('signal_date') == plan['signal_date'], 'OBSERVER_CONTEXT_MISMATCH')
        upstream, run_id = delivery.OBSERVER, context['observer_run_id']
    run = client.request(f'/actions/runs/{run_id}')
    delivery.valid_run(run, upstream, success=False)
    delivery.require(run['id'] == run_id and run['status'] == 'completed', 'FOLLOWUP_UPSTREAM_CHANGED')
    started = delivery.stamp(run['created_at'])
    return [r for r in runs if r['status'] != 'completed' or delivery.stamp(r['updated_at']) >= started]


def follow(initial, client_factory, clock=utc_now, pause=sleep):
    stage = initial.get('workflow')
    if stage not in STAGES or initial.get('status') not in (
            'DISPATCHED_NOT_COMPLETED', 'DISPATCH_OUTCOME_UNKNOWN_NO_BLIND_RETRY',
            'UPSTREAM_OR_RECOVERY_ALREADY_RUNNING'):
        return {'status': 'NO_RIDGE_HANDOFF_TO_FOLLOW'}
    day = initial['signal_date']
    # GitHub run timestamps are whole seconds; the queue clock carries microseconds.
    earliest = delivery.stamp(initial['queue']['checked_at']).replace(microsecond=0)
    if initial['status'] == 'UPSTREAM_OR_RECOVERY_ALREADY_RUNNING':
        same_day = initial['queue']['same_day_target_run_ids']
        floor = (delivery.stamp(initial['retry_not_before']) if stage == delivery.NATURAL else
                 datetime.strptime(day+'1500', '%Y%m%d%H%M').replace(tzinfo=delivery.TZ))
        candidates = stage_runs(client_factory(), stage, day, floor) if same_day else []
        events = ('workflow_run', 'workflow_dispatch', 'schedule') if stage == delivery.PUBLISHER else (
            'workflow_run', 'workflow_dispatch')
        matched = [r for r in candidates if r['id'] in same_day and r['event'] in events
                   and r['status'] in PENDING and r['head_sha'] == initial['head']]
        if not matched:
            return {'signal_date': day, 'status': 'NO_CONFIRMED_ACTIVE_RIDGE_RUN'}
        earliest = min(delivery.stamp(r['created_at']) for r in matched)
    cutoff = datetime.strptime(initial['exec_date']+'0920', '%Y%m%d%H%M').replace(tzinfo=delivery.TZ)
    until = min(cutoff, clock() + timedelta(minutes=40))
    sent = set(STAGES[:STAGES.index(stage)+1])
    bound_ids = {stage: initial.get('dispatched_run_id')}
    ready = None
    last = initial['status']
    run_ids = []

    def dispatch_clock():
        now = clock()
        delivery.require(now < until, 'FOLLOWUP_WINDOW_ENDED')
        return now

    while clock() < until:
        pause(max(0, min(60, (until - clock()).total_seconds())))
        if clock() >= until:
            break
        client = client_factory()
        target = None
        try:
            # The target scan also detects jobs which failed between two polls.
            # Push CI and previous-day jobs are never evidence of a daily delivery.
            observed = stage_runs(client, stage, day, earliest)
            run_ids = [r['id'] for r in observed]
            active = any(r['status'] in PENDING for r in observed)
            if stage == delivery.NATURAL and any(r['status'] in PENDING and r['id'] == bound_ids.get(stage)
                                                for r in observed):
                last = 'DISPATCHED_RIDGE_STILL_RUNNING'
                continue
            plan = delivery.inspect(client, clock(), wakeup_day=day)
            last = plan['status']
            if last in TERMINAL:
                return plan
            if last == 'STALE_P0_COMPLETION_IGNORED':
                break
            target = plan.get('workflow')
            if target == stage:
                if active:
                    last = 'WAITING_FOR_CURRENT_STAGE'
                    continue
                failed = [r for r in observed if r.get('conclusion') in FAILED]
                bound_failure = next((r for r in failed if r['id'] == bound_ids.get(stage)), None)
                if bound_failure and bound_failure['conclusion'] != 'cancelled':
                    return {'signal_date': day, 'status': 'DELIVERY_STAGE_FAILED', 'workflow': stage,
                            'run_ids': [bound_failure['id']]}
                if failed:
                    # Cancellation can have a successor. Other failures may be for a
                    # previous signal. Neither changes the current frozen evidence.
                    last = 'RUN_OUTCOME_AWAITS_SAME_DAY_EVIDENCE'
            if target not in (delivery.OBSERVER, delivery.PUBLISHER) or target in sent:
                continue
            # Give the ordinary workflow_run callback one poll interval to appear.
            # An observer failure with a complete verified ACK may still yield a
            # publisher plan, as decided by the existing inspector.
            if ready != target:
                ready = target
                continue
            others = stage_runs(client, target, day, earliest)
            if any(r['status'] in PENDING for r in others):
                continue
            others = after_prerequisite(client, plan, others)
            acknowledged = completed_writer(client, target, others)
            if acknowledged or any(r.get('conclusion') in FAILED for r in others):
                # A nearby run may concern another signal day. It grants no
                # authority to conclude success/failure, or to retry blindly.
                last = 'RUN_OUTCOME_AWAITS_SAME_DAY_EVIDENCE'
                run_ids = [r['id'] for r in others]
                continue
            outcome = delivery.act(client, plan, clock(), execute=True, clock=dispatch_clock)
            last = outcome['status']
            if last in ('DISPATCHED_NOT_COMPLETED', 'DISPATCH_OUTCOME_UNKNOWN_NO_BLIND_RETRY'):
                bound_ids[target] = outcome.get('dispatched_run_id')
                stage = target
                sent.add(target)
        except ValueError as exc:
            reason = str(exc)
            if reason == 'FOLLOWUP_WINDOW_ENDED':
                break
            if reason == 'DISPATCH_OUTCOME_UNKNOWN_NO_BLIND_RETRY' and target:
                stage = target
                sent.add(target)
            elif reason not in TRANSIENT:
                raise
            last = reason
    return {'signal_date': day, 'status': 'DELIVERY_FOLLOWUP_ENDED_WITHOUT_PUBLIC_ACCEPTANCE',
            'last_observation': last, 'workflow': stage, 'observed_run_ids': run_ids}


def main():
    delivery.require(os.environ.get('GITHUB_ACTIONS') == 'true'
                     and os.environ.get('GITHUB_REPOSITORY') == delivery.REPO
                     and os.environ.get('GITHUB_REF') == 'refs/heads/main'
                     and os.environ.get('GITHUB_RUN_ATTEMPT') == '1'
                     and os.environ.get('GITHUB_WORKFLOW_REF') == delivery.REPO+'/'+delivery.WORKFLOW+'@refs/heads/main'
                     and os.environ.get('GITHUB_EVENT_NAME') in ('schedule', 'workflow_dispatch', 'workflow_run')
                     and os.environ.get('EXECUTE') == 'true', 'REGISTERED_WATCHDOG_ONLY')
    with open(sys.argv[1], encoding='utf-8') as stream:
        initial = json.load(stream)
    result = follow(initial, lambda: delivery.GitHub(os.environ.get('GH_TOKEN')))
    delivery.write_delivery_summary(result, os.environ.get('GITHUB_STEP_SUMMARY'))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return int(result['status'] in ('DELIVERY_STAGE_FAILED', 'DELIVERY_FOLLOWUP_ENDED_WITHOUT_PUBLIC_ACCEPTANCE'))


if __name__ == '__main__':
    raise SystemExit(main())
