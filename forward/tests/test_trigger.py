"""Synthetic pure schedule bindings; no Actions dispatch or upstream access."""
from datetime import datetime, timedelta, timezone

import pytest

from forward.trigger import SCHEDULES, bind_schedule


OPEN = ["20260904", "20260907", "20260908", "20260909", "20260910", "20260911",
        "20260914", "20260915", "20260916", "20260917", "20260918", "20260921"]


def bind(schedule="15 13 * * 1", created="2026-09-07T13:16:00Z", now="2026-09-07T13:17:00Z", dates=OPEN, **kwargs):
    return bind_schedule("schedule", schedule, created, now, dates, **kwargs)


def test_primary_binds_exact_original_dates_and_publish_deadline():
    result = bind()
    assert result["status"] == "ELIGIBLE"
    assert (result["signal_date"], result["exec_date"], result["exit_date"]) == ("20260907", "20260908", "20260909")
    assert result["slot_utc"] == "2026-09-07T13:15:00Z"
    assert result["slot_beijing"] == "2026-09-07T21:15:00+08:00"
    assert result["slot_kind"] == "PRIMARY"
    assert result["publish_deadline_beijing"] == "2026-09-07T21:35:00+08:00"
    assert result["primary_publish_deadline_utc"] == result["publish_deadline_utc"]
    assert result["primary_publish_deadline_beijing"] == result["publish_deadline_beijing"]
    assert result["primary_deadline_missed"] == result["late"]
    assert result["admission_deadline_beijing"] == "2026-09-08T09:15:00+08:00"
    assert result["created_delay_seconds"] == 60
    assert result["execution_delay_seconds"] == 120
    assert result["late"] is False
    assert result["production_activated"] is False
    assert result["upstream_reads_allowed"] is True


@pytest.mark.parametrize("weekday", range(1, 6))
@pytest.mark.parametrize("hour,kind", [(13, "PRIMARY"), (14, "RECOVERY")])
def test_all_ten_weekday_identities(weekday, hour, kind):
    day = datetime(2026, 9, 7, hour, 15, tzinfo=timezone.utc) + timedelta(days=weekday - 1)
    stamp = (day + timedelta(minutes=1)).isoformat()
    result = bind_schedule("schedule", f"15 {hour} * * {weekday}", stamp, stamp, OPEN)
    assert result["signal_date"] == day.strftime("%Y%m%d")
    assert result["slot_kind"] == kind
    assert result["status"] == "ELIGIBLE"
    assert len(SCHEDULES) == 10


@pytest.mark.parametrize("created,now", [
    ("2026-09-04T18:16:00Z", "2026-09-04T18:17:00Z"),
    ("2026-09-05T00:30:00Z", "2026-09-05T00:31:00Z"),
])
def test_friday_delayed_into_saturday_never_changes_original_d(created, now):
    result = bind("15 13 * * 5", created, now)
    assert result["status"] == "ELIGIBLE"
    assert (result["signal_date"], result["exec_date"], result["exit_date"]) == ("20260904", "20260907", "20260908")
    assert result["slot_utc"] == "2026-09-04T13:15:00Z"
    assert result["late"] is True
    assert result["admission_deadline_beijing"] == "2026-09-05T09:15:00+08:00"


def test_recovery_cutoff_is_t0920_not_its_later_twelve_hour_deadline():
    result = bind("15 14 * * 1", "2026-09-08T01:19:58Z", "2026-09-08T01:19:59Z")
    assert result["signal_date"] == "20260907"
    assert result["admission_deadline_beijing"] == "2026-09-08T09:20:00+08:00"
    with pytest.raises(ValueError, match="09:20"):
        bind("15 14 * * 1", "2026-09-08T01:19:58Z", "2026-09-08T01:20:00Z")


def test_recovery_created_after_t0920_also_rejected():
    with pytest.raises(ValueError, match="09:20"):
        bind("15 14 * * 1", "2026-09-08T01:20:00Z", "2026-09-08T01:20:00Z")


@pytest.mark.parametrize("created,now", [
    ("2026-09-07T13:16:00Z", "2026-09-08T01:15:00Z"),
    ("2026-09-08T01:15:00Z", "2026-09-08T01:15:00Z"),
    ("2026-09-05T01:15:00Z", "2026-09-05T01:15:00Z"),
])
def test_twelve_hour_boundary_is_exclusive(created, now):
    weekday = 5 if "2026-09-05" in created else 1
    with pytest.raises(ValueError, match="twelve-hour"):
        bind(f"15 13 * * {weekday}", created, now)


def test_publish_lateness_uses_actual_execution_not_only_creation():
    on_time = bind(now="2026-09-07T13:35:00Z")
    late = bind(now="2026-09-07T13:35:01Z")
    assert on_time["late"] is False
    assert late["late"] is True
    assert on_time["primary_deadline_missed"] == on_time["late"]
    assert late["primary_deadline_missed"] == late["late"]
    assert late["created_delay_seconds"] == 60


def test_timely_recovery_does_not_hide_missed_primary_publish_deadline():
    result = bind("15 14 * * 1", "2026-09-07T14:16:00Z", "2026-09-07T14:30:00Z")
    assert result["status"] == "ELIGIBLE"
    assert result["slot_kind"] == "RECOVERY"
    assert result["late"] is False
    assert result["publish_deadline_beijing"] == "2026-09-07T22:35:00+08:00"
    assert result["primary_publish_deadline_utc"] == "2026-09-07T13:35:00Z"
    assert result["primary_publish_deadline_beijing"] == "2026-09-07T21:35:00+08:00"
    assert result["primary_deadline_missed"] is True


def test_closed_original_holiday_is_explicit_no_upstream_reads():
    holidays = ["20260928", "20260929", "20260930", "20261009", "20261012", "20261013"]
    result = bind("15 13 * * 4", "2026-10-01T13:16:00Z", "2026-10-01T13:17:00Z", holidays)
    assert result["status"] == "CLOSED"
    assert result["signal_date"] == "20261001"
    assert result["exec_date"] is None and result["exit_date"] is None
    assert result["upstream_reads_allowed"] is False
    assert result["reason"] == "ORIGINAL_D_SSE_CLOSED"


def test_closed_slot_after_midnight_remains_that_closed_d():
    holidays = ["20260928", "20260929", "20260930", "20261009", "20261012", "20261013"]
    result = bind("15 13 * * 4", "2026-10-02T00:01:00Z", "2026-10-02T00:02:00Z", holidays)
    assert result["status"] == "CLOSED"
    assert result["signal_date"] == "20261001"


@pytest.mark.parametrize("event", ["workflow_dispatch", "workflow_run", "push", "pull_request", "", None])
def test_only_natural_schedule_event_is_eligible(event):
    with pytest.raises(ValueError, match="natural schedule"):
        bind_schedule(event, "15 13 * * 1", "2026-09-07T13:16:00Z", "2026-09-07T13:17:00Z", OPEN)


@pytest.mark.parametrize("attempt", [2, 0, -1, True, False, 1.0, "1", None])
def test_reruns_and_noninteger_attempts_rejected(attempt):
    with pytest.raises(ValueError, match="first natural run attempt"):
        bind(run_attempt=attempt)


@pytest.mark.parametrize("schedule", ["15 13 * * 1-5", "15 14 * * 1,2,3,4,5", "15 21 * * 1",
                                      "15 22 * * 1", "35 13 * * 1", "15 13 * * 0",
                                      "15 13 * * 6", "15 13 * * 7", "15  13 * * 1", None, True, []])
def test_cron_must_be_exact_supported_identity(schedule):
    with pytest.raises(ValueError, match="exact independent"):
        bind(schedule=schedule)


@pytest.mark.parametrize("created,now", [
    ("2026-09-07T13:17:01Z", "2026-09-07T13:17:00Z"),
    ("2026-09-07T13:16:00", "2026-09-07T13:17:00Z"),
    ("2026-09-07T13:16:00Z", "2026-09-07T13:17:00"),
    ("invalid", "2026-09-07T13:17:00Z"),
    (None, "2026-09-07T13:17:00Z"),
])
def test_bad_or_future_creation_metadata_rejected(created, now):
    with pytest.raises(ValueError):
        bind(created=created, now=now)


def test_aware_beijing_timestamps_are_normalized_not_reinterpreted():
    result = bind(created="2026-09-07T21:16:00+08:00", now="2026-09-07T21:17:00+08:00")
    assert result["run_created_at_utc"] == "2026-09-07T13:16:00Z"
    assert result["slot_utc"] == "2026-09-07T13:15:00Z"


@pytest.mark.parametrize("created", ["2026-09-07T13:14:59Z", "2026-09-08T14:00:00Z",
                                     "2026-09-13T13:16:00Z", "2026-09-14T13:14:59Z"])
def test_wrong_weekday_or_stale_week_cannot_drift_to_today(created):
    with pytest.raises(ValueError, match="expired or ambiguous"):
        bind(created=created, now=created)


def test_unknown_calendar_coverage_is_not_claimed_closed():
    with pytest.raises(ValueError, match="does not cover"):
        bind(dates=["20260908", "20260909", "20260910"])
    with pytest.raises(ValueError, match="does not cover"):
        bind("15 13 * * 1", "2027-01-04T13:16:00Z", "2027-01-04T13:17:00Z", ["20261229", "20261230", "20261231"])
    with pytest.raises(ValueError, match="T/T1 coverage"):
        bind(dates=["20260904", "20260907", "20260908"])


@pytest.mark.parametrize("dates", [[], "20260907", None, set(OPEN),
                                   ["20260907", "20260907", "20260908"],
                                   ["20260908", "20260907", "20260909"],
                                   ["20260907", "20260230", "20260909"],
                                   ["20260907", True, "20260909"]])
def test_calendar_is_committed_strict_sequence_not_weekday_guess(dates):
    with pytest.raises(ValueError):
        bind(dates=dates)


def test_pure_binding_does_not_mutate_calendar():
    supplied = list(OPEN)
    before = list(supplied)
    bind(dates=supplied)
    assert supplied == before
