"""Pure original-slot binding for the read-only natural schedule rehearsal.

Caller contract: event_name, schedule, run_created_at and run_attempt must be
obtained from the independently checked GitHub Actions run/event metadata.
This pure function cannot authenticate an arbitrary caller's strings. In
particular, do not substitute local today, job start, artifact time or a supplied
dispatch date for the API's immutable run created_at. The workflow/repository/
head-SHA identity check is a separate caller responsibility.

No network requests, dispatches, writes, production activation or ledger changes
occur here. CLOSED is an explicit SSE-closed original slot; malformed metadata,
unknown calendar coverage and expired slots raise ValueError (fail closed).
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, time, timedelta, timezone

from .ledger import BEIJING, _date, _timestamp


SCHEDULES = {
    f"15 {hour} * * {weekday}": (weekday, hour, kind)
    for weekday in range(1, 6)
    for hour, kind in ((13, "PRIMARY"), (14, "RECOVERY"))
}


def _iso_utc(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _calendar(open_dates):
    if not isinstance(open_dates, Sequence) or isinstance(open_dates, (str, bytes)) or not open_dates:
        raise ValueError("a nonempty ordered committed SSE calendar is required")
    values = list(open_dates)
    for date in values:
        _date(date)
    if values != sorted(set(values)):
        raise ValueError("committed SSE sessions must be unique and increasing")
    return values


def bind_schedule(event_name, schedule, run_created_at, now, open_dates, run_attempt=1):
    """Bind one of ten independent weekday/slot cron identities to its own D.

The most recent occurrence of that exact weekday/UTC time not after the API
created_at is the only candidate. Both creation and execution must be strictly
less than twelve hours after it, and execution must precede T 09:20 Beijing.
    Thus a delayed Friday run may complete on Saturday, but it remains Friday D;
    creation/execution beyond twelve hours of this bound slot is rejected.

    Observability limit: GitHub does not expose a nominal occurrence identifier.
    The binding is the most recent matching weekday/clock before created_at, not
    independent proof of the scheduler's original occurrence. An extreme delay
    of whole weeks that happens to land inside a new matching slot's window is
    indistinguishable from that new occurrence using these metadata alone.

ELIGIBLE is permission for the next read-only input check, not proof of
production readiness, input availability, model success or a published list.
"""
    if event_name != "schedule":
        raise ValueError("only a natural schedule event is eligible; dispatch/workflow_run is not")
    if type(run_attempt) is not int or run_attempt != 1:
        raise ValueError("only the first natural run attempt is eligible; never a rerun")
    if not isinstance(schedule, str) or schedule not in SCHEDULES:
        raise ValueError("schedule must be one exact independent weekday/UTC cron identity")
    created = _timestamp(run_created_at)
    current = _timestamp(now)
    if current < created:
        raise ValueError("current time precedes immutable run created_at")
    dates = _calendar(open_dates)
    weekday, hour, kind = SCHEDULES[schedule]
    days_back = (created.isoweekday() - weekday) % 7
    slot_date = created.date() - timedelta(days=days_back)
    slot = datetime.combine(slot_date, time(hour, 15), timezone.utc)
    if slot > created:
        slot -= timedelta(days=7)
    hard_limit = slot + timedelta(hours=12)
    if not slot <= created < hard_limit or not slot <= current < hard_limit:
        raise ValueError("original schedule slot expired or ambiguous; twelve-hour limit exceeded")
    signal_date = slot.astimezone(BEIJING).strftime("%Y%m%d")
    # An open-date-only list proves closed dates only inside its known span.
    # Outside that span, absence must not be mislabeled as a holiday.
    if signal_date < dates[0] or signal_date > dates[-1]:
        raise ValueError("committed SSE calendar does not cover the original slot date")
    publish_deadline = slot + timedelta(minutes=20)
    primary_publish_deadline = datetime.combine(_date(signal_date), time(21, 35), BEIJING)
    common = {
        "event_name": event_name, "schedule": schedule, "run_attempt": run_attempt,
        "binding_basis": "immutable_run_created_at_and_distinct_cron",
        "signal_date": signal_date,
        "slot_kind": kind,
        "slot_utc": _iso_utc(slot),
        "slot_beijing": slot.astimezone(BEIJING).isoformat(),
        "run_created_at_utc": _iso_utc(created),
        "checked_at_utc": _iso_utc(current),
        "publish_deadline_utc": _iso_utc(publish_deadline),
        "publish_deadline_beijing": publish_deadline.astimezone(BEIJING).isoformat(),
        "late": current > publish_deadline,
        "primary_publish_deadline_utc": _iso_utc(primary_publish_deadline),
        "primary_publish_deadline_beijing": primary_publish_deadline.isoformat(),
        "primary_deadline_missed": current > primary_publish_deadline,
        "created_delay_seconds": (created - slot).total_seconds(),
        "execution_delay_seconds": (current - slot).total_seconds(),
        "read_only": True,
        "production_activated": False,
    }
    if signal_date not in dates:
        return {**common, "status": "CLOSED", "reason": "ORIGINAL_D_SSE_CLOSED",
                "exec_date": None, "exit_date": None,
                "admission_deadline_utc": _iso_utc(hard_limit),
                "admission_deadline_beijing": hard_limit.astimezone(BEIJING).isoformat(),
                "upstream_reads_allowed": False}
    index = dates.index(signal_date)
    if index + 2 >= len(dates):
        raise ValueError("committed SSE calendar lacks strict T/T1 coverage")
    exec_date, exit_date = dates[index + 1:index + 3]
    t_cutoff = datetime.combine(_date(exec_date), time(9, 20), BEIJING)
    deadline = min(hard_limit, t_cutoff)
    if created >= deadline or current >= deadline:
        raise ValueError("original slot crossed the strict T 09:20 admission cutoff")
    return {**common, "status": "ELIGIBLE", "reason": "NATURAL_SLOT_BOUND",
            "exec_date": exec_date, "exit_date": exit_date,
            "admission_deadline_utc": _iso_utc(deadline),
            "admission_deadline_beijing": deadline.astimezone(BEIJING).isoformat(),
            "upstream_reads_allowed": True}
