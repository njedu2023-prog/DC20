"""Queue fault injection: no HTTP, credentials, cancellation, or dispatch."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import dc20_writer_queue as q


REPO = "njedu2023-prog/DC20"
P0 = "run_primary_d_daily.yml"
P1 = "run_primary_profit_rankings.yml"
WRITER = "decision-auction-main-writer"
DAY = "20261103"
NOW = datetime.fromisoformat("2026-11-03T19:30:00+08:00")
OLD = "2026-10-08T11:30:00Z"
STATUSES = ("queued", "requested", "waiting", "pending", "in_progress")
RECENT = ("/actions/workflows/" + P0 + "/runs?branch=main&"
          "created=%3E%3D2026-11-01T16%3A00%3A00Z&per_page=100&page=")


def run(run_id=101, workflow=P0, status="queued", created_at=OLD, **changes):
    value = {
        "id": run_id,
        "workflow_id": 343703608 if workflow == P0 else 343703609,
        "path": ".github/workflows/" + workflow,
        "run_attempt": 1,
        "head_branch": "main",
        "head_sha": "a" * 40,
        "repository": {"full_name": REPO},
        "head_repository": {"full_name": REPO},
        "event": "workflow_dispatch",
        "status": status,
        "conclusion": None,
        "created_at": created_at,
        "updated_at": created_at,
        "display_title": "DC20 controlled daily NATURAL | D=" + created_at[:10].replace("-", ""),
    }
    value.update(changes)
    return value


def group(run_id=101, status="pending", name=WRITER):
    return {
        "group_name": name,
        "group_url": f"https://api.github.com/repos/{REPO}/actions/concurrency_groups/{name}",
        "total_count": 1,
        "group_members": [{"run_id": run_id, "status": status}],
    }


class Queue:
    """Strict fake: an unimplemented API read is a test failure."""

    def __init__(self, *runs):
        self.runs = list(deepcopy(runs))
        self.reads = []
        self.group_list = {"total_count": 0, "concurrency_groups": []}
        self.writer = None
        self.details = {}
        self.detail_counts = {}
        self.jobs = {}
        self.run_groups = {}
        self.responses = {}
        self.response_sequences = {}
        self.response_counts = {}

    def activate_writer(self, run_id=101, status="pending"):
        self.writer = group(run_id, status)
        self.group_list = {
            "total_count": 1,
            "concurrency_groups": [{"group_name": WRITER, "group_url": self.writer["group_url"]}],
        }

    def request(self, path):
        self.reads.append(path)
        if path in self.response_sequences:
            sequence = self.response_sequences[path]
            number = self.response_counts.get(path, 0)
            self.response_counts[path] = number + 1
            value = sequence[min(number, len(sequence)-1)]
            if isinstance(value, Exception):
                raise value
            return deepcopy(value)
        if path in self.responses:
            value = self.responses[path]
            if isinstance(value, Exception):
                raise value
            return deepcopy(value)
        if path == "/actions/concurrency_groups?per_page=100":
            return deepcopy(self.group_list)
        if path == "/actions/concurrency_groups/" + WRITER:
            return deepcopy(self.writer)
        if path.startswith("/actions/workflows/") and "/runs?" in path:
            params = parse_qs(urlsplit(path).query)
            assert set(params) == {"branch", "created", "per_page", "page"}
            assert params["branch"] == ["main"] and params["per_page"] == ["100"]
            assert params["created"] == [">=2026-11-01T16:00:00Z"]
            workflow = path.split("/actions/workflows/", 1)[1].split("/runs?", 1)[0]
            page = int(params["page"][0])
            assert page >= 1
            floor = datetime.fromisoformat("2026-11-01T16:00:00+00:00")
            rows = [r for r in self.runs if r["path"] == ".github/workflows/" + workflow
                    and datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) >= floor]
            return {"total_count": len(rows), "workflow_runs": deepcopy(rows[(page-1)*100:page*100])}
        if path.startswith("/actions/runs?"):
            params = parse_qs(urlsplit(path).query)
            assert set(params) == {"branch", "status", "per_page", "page"}
            assert params["branch"] == ["main"] and params["per_page"] == ["100"]
            status = params["status"][0]
            assert status in STATUSES
            page = int(params["page"][0])
            assert page >= 1
            rows = [r for r in self.runs if r["status"] == status]
            return {"total_count": len(rows), "workflow_runs": deepcopy(rows[(page-1)*100:page*100])}
        if path.startswith("/actions/runs/"):
            tail = path[len("/actions/runs/"):]
            run_id = int(tail.split("/", 1)[0])
            if path.endswith("/jobs?per_page=100"):
                return deepcopy(self.jobs.get(run_id, {"total_count": 0, "jobs": []}))
            if path.endswith("/concurrency_groups?per_page=100"):
                # Configured groups may legitimately have no active members.
                return deepcopy(self.run_groups.get(run_id, {
                    "total_count": 1,
                    "concurrency_groups": [{"group_name": WRITER, "group_members": []}],
                }))
            assert tail.isdigit(), path
            number = self.detail_counts.get(run_id, 0)
            self.detail_counts[run_id] = number + 1
            sequence = self.details.get(run_id)
            if sequence:
                return deepcopy(sequence[min(number, len(sequence)-1)])
            return deepcopy(next(r for r in self.runs if r["id"] == run_id))
        raise AssertionError("Unexpected API read: " + path)


def inspect(client):
    return q.inspect_queue(client, P0, DAY, NOW)


def assert_blocked(client, run_id=None):
    result = inspect(client)
    assert result["status"] == "WAIT"
    if run_id is not None:
        assert run_id in result["blocking_run_ids"]
    return result


def test_empty_queue_is_clear_and_all_active_statuses_are_checked():
    client = Queue()
    result = inspect(client)
    assert result["status"] == "CLEAR"
    assert result["writer_group_active"] is False
    assert result["orphan_run_ids"] == []
    assert result["blocking_run_ids"] == []
    assert result["same_day_target_run_ids"] == []
    assert datetime.fromisoformat(result["checked_at"].replace("Z", "+00:00")) == NOW
    for status in STATUSES:
        assert any(f"status={status}&" in path for path in client.reads)
    active_reads = [parse_qs(urlsplit(path).query)["status"][0]
                    for path in client.reads if path.startswith("/actions/runs?")]
    assert active_reads == list(STATUSES)
    assert RECENT + "1" in client.reads
    assert client.reads.count("/actions/concurrency_groups?per_page=100") == 2
    assert client.reads.count("/actions/concurrency_groups/" + WRITER) == 2


def test_old_queued_without_jobs_or_active_group_is_quarantined_read_only():
    client = Queue(run())
    result = inspect(client)
    assert result["status"] == "CLEAR"
    assert result["orphan_run_ids"] == [101]
    assert result["blocking_run_ids"] == []
    assert client.detail_counts[101] >= 2
    assert "/actions/runs/101/jobs?per_page=100" in client.reads
    assert "/actions/runs/101/concurrency_groups?per_page=100" in client.reads
    assert all(not path.endswith(("/cancel", "/rerun", "/dispatches")) for path in client.reads)


def test_same_day_target_queued_is_followed_even_without_jobs():
    client = Queue(run(created_at="2026-11-03T11:00:00Z"))
    result = assert_blocked(client, 101)
    assert result["same_day_target_run_ids"] == [101]
    assert result["orphan_run_ids"] == []


def test_same_day_other_writer_is_not_mistaken_for_same_target():
    client = Queue(run(workflow=P1, created_at="2026-11-03T11:00:00Z"))
    result = assert_blocked(client, 101)
    assert result["same_day_target_run_ids"] == []


def test_old_creation_with_explicit_current_target_tag_still_blocks():
    client = Queue(run(display_title="DC20 controlled daily NATURAL | D=" + DAY))
    result = assert_blocked(client, 101)
    assert result["same_day_target_run_ids"] == [101]
    assert result["orphan_run_ids"] == []


@pytest.mark.parametrize("status", ["in_progress", "requested", "waiting", "pending"])
def test_old_nonqueued_writer_never_isolated_by_age(status):
    result = assert_blocked(Queue(run(status=status)), 101)
    assert result["orphan_run_ids"] == []


def test_previous_date_but_under_twenty_four_hours_still_blocks():
    stamp = (NOW - timedelta(hours=23)).astimezone(timezone.utc).isoformat()
    assert_blocked(Queue(run(created_at=stamp)), 101)


def test_exact_twenty_four_hour_boundary_still_blocked_by_recent_target_window():
    stamp = (NOW - timedelta(hours=24)).astimezone(timezone.utc).isoformat()
    assert q.old_queued(run(created_at=stamp), DAY, NOW) is True
    assert_blocked(Queue(run(created_at=stamp)), 101)


@pytest.mark.parametrize("job_status", ["queued", "in_progress", "completed"])
def test_any_existing_job_prevents_orphan_isolation(job_status):
    client = Queue(run())
    client.jobs[101] = {"total_count": 1, "jobs": [{"id": 99, "status": job_status}]}
    result = assert_blocked(client, 101)
    assert result["orphan_run_ids"] == []


def test_shared_writer_group_is_authoritative_even_with_empty_run_list():
    client = Queue()
    client.activate_writer(999)
    result = assert_blocked(client)
    assert result["writer_group_active"] is True
    assert 999 in result["blocking_run_ids"]


def test_old_jobless_run_in_shared_group_cannot_be_isolated():
    client = Queue(run())
    client.activate_writer()
    result = assert_blocked(client, 101)
    assert result["orphan_run_ids"] == []


def test_run_level_active_membership_blocks_even_if_repo_group_list_raced():
    client = Queue(run())
    client.run_groups[101] = {"total_count": 1, "concurrency_groups": [group()]}
    result = assert_blocked(client, 101)
    assert result["orphan_run_ids"] == []


def test_other_ci_and_watchdog_do_not_block_without_shared_writer_membership():
    client = Queue(run(workflow="lint.yml", status="in_progress"),
                   run(102, "candidate_delivery_watchdog.yml", "in_progress"))
    result = inspect(client)
    assert result["status"] == "CLEAR" and result["blocking_run_ids"] == []


def test_observation_verifier_is_a_shared_writer():
    assert_blocked(Queue(run(workflow="verify_decision_observations.yml", status="in_progress")), 101)


@pytest.mark.parametrize("refresh_number", [0, 1])
def test_queued_to_running_transition_is_never_isolated(refresh_number):
    client = Queue(run())
    client.details[101] = ([run()] if refresh_number else []) + [run(status="in_progress")]
    result = assert_blocked(client, 101)
    assert result["orphan_run_ids"] == []


@pytest.mark.parametrize("refresh_number", [0, 1])
def test_refreshed_current_day_tag_cannot_turn_into_an_old_orphan(refresh_number):
    client = Queue(run())
    current = run(display_title="DC20 controlled daily NATURAL | D=" + DAY)
    client.details[101] = ([run()] if refresh_number else []) + [current]
    result = assert_blocked(client, 101)
    assert result["orphan_run_ids"] == []


def test_duplicate_across_status_pages_preserves_running_evidence():
    client = Queue(run())
    path = "/actions/runs?branch=main&status=in_progress&per_page=100&page=1"
    client.responses[path] = {"total_count": 1, "workflow_runs": [run(status="in_progress")]}
    result = assert_blocked(client, 101)
    assert result["orphan_run_ids"] == []


def test_running_on_second_active_page_is_not_missed():
    client = Queue(*(run(i, "lint.yml", "in_progress") for i in range(1, 101)),
                   run(101, P0, "in_progress"))
    result = assert_blocked(client, 101)
    assert any("status=in_progress&per_page=100&page=2" in path for path in client.reads)
    assert result["orphan_run_ids"] == []


@pytest.mark.parametrize("bad", [
    {"total_count": 1, "concurrency_groups": []},
    {"total_count": 0},
    {"total_count": "0", "concurrency_groups": []},
])
def test_incomplete_or_malformed_group_inventory_fails_closed(bad):
    client = Queue(run())
    client.group_list = bad
    with pytest.raises(ValueError, match="QUEUE_"):
        inspect(client)


@pytest.mark.parametrize("bad", [
    {"total_count": 0, "jobs": [{"id": 1}]},
    {"total_count": 1, "jobs": []},
    {"total_count": "0", "jobs": []},
    {"total_count": 0},
])
def test_inconsistent_job_evidence_fails_closed(bad):
    client = Queue(run())
    client.jobs[101] = bad
    with pytest.raises(ValueError, match="QUEUE_"):
        inspect(client)


@pytest.mark.parametrize("path", [
    "/actions/concurrency_groups?per_page=100",
    "/actions/concurrency_groups/" + WRITER,
    "/actions/runs/101",
    "/actions/runs/101/jobs?per_page=100",
    "/actions/runs/101/concurrency_groups?per_page=100",
])
def test_unavailable_queue_evidence_never_becomes_clear(path):
    client = Queue(run())
    client.responses[path] = ValueError("QUEUE_API_UNAVAILABLE")
    with pytest.raises(ValueError, match="QUEUE_"):
        inspect(client)


def test_active_status_pagination_cannot_silently_truncate():
    client = Queue()
    path = "/actions/runs?branch=main&status=queued&per_page=100&page=1"
    client.responses[path] = {"total_count": 1, "workflow_runs": []}
    with pytest.raises(ValueError, match="QUEUE_"):
        inspect(client)


def test_active_status_pagination_budget_fails_closed():
    client = Queue(*(run(i, "lint.yml", "queued") for i in range(1, 1002)))
    with pytest.raises(ValueError, match="QUEUE_"):
        inspect(client)
    assert not any("page=11" in path for path in client.reads)


@pytest.mark.parametrize("field,value", [
    ("id", True),
    ("run_attempt", 2),
    ("created_at", "2026-11-03T20:00:00+08:00"),
    ("created_at", "2026-10-08T11:30:00"),
    ("repository", {"full_name": "untrusted/DC20"}),
    ("head_repository", {"full_name": "untrusted/DC20"}),
])
def test_untrusted_or_unprovable_old_queue_is_never_isolated(field, value):
    client = Queue(run(**{field: value}))
    try:
        result = inspect(client)
    except ValueError as error:
        assert str(error).startswith("QUEUE_")
    else:
        assert result["status"] == "WAIT" and result["orphan_run_ids"] == []


def test_run_group_configuration_count_cannot_hide_missing_groups():
    client = Queue(run())
    client.run_groups[101] = {"total_count": 2, "concurrency_groups": [{"group_name": WRITER, "group_members": []}]}
    with pytest.raises(ValueError, match="QUEUE_"):
        inspect(client)


def test_run_missing_from_status_slices_is_caught_by_recent_unfiltered_view():
    client = Queue()
    current = run(status="in_progress", created_at="2026-11-03T11:00:00Z")
    client.responses[RECENT + "1"] = {"total_count": 1, "workflow_runs": [current]}
    result = assert_blocked(client, 101)
    assert result["same_day_target_run_ids"] == [101]
    assert result["orphan_run_ids"] == []


def test_recent_unfiltered_second_page_running_is_not_hidden_by_completed_runs():
    client = Queue()
    completed = [run(i, status="completed", created_at="2026-11-03T10:00:00Z", conclusion="success")
                 for i in range(1, 101)]
    current = run(101, status="in_progress", created_at="2026-11-03T11:00:00Z")
    client.responses[RECENT + "1"] = {"total_count": 101, "workflow_runs": completed}
    client.responses[RECENT + "2"] = {"total_count": 101, "workflow_runs": [current]}
    assert_blocked(client, 101)
    assert RECENT + "2" in client.reads


def test_recent_unfiltered_incomplete_page_fails_closed():
    client = Queue()
    client.responses[RECENT + "1"] = {"total_count": 1, "workflow_runs": []}
    with pytest.raises(ValueError, match="QUEUE_"):
        inspect(client)


def test_final_writer_group_appearance_blocks_previously_clear_scan():
    client = Queue()
    live = group(999, "in_progress")
    client.response_sequences["/actions/concurrency_groups?per_page=100"] = [
        {"total_count": 0, "concurrency_groups": []},
        {"total_count": 1, "concurrency_groups": [{"group_name": WRITER, "group_url": live["group_url"]}]},
    ]
    client.response_sequences["/actions/concurrency_groups/" + WRITER] = [None, live]
    result = assert_blocked(client, 999)
    assert result["writer_group_active"] is True


def test_final_writer_lookup_failure_does_not_reuse_initial_clear_snapshot():
    client = Queue()
    client.response_sequences["/actions/concurrency_groups/" + WRITER] = [None, ValueError("QUEUE_API_UNAVAILABLE")]
    with pytest.raises(ValueError, match="QUEUE_"):
        inspect(client)


def test_transient_count_without_rows_requires_complete_rescan():
    client = Queue()
    path = '/actions/runs?branch=main&status=queued&per_page=100&page=1'
    client.response_sequences[path] = [
        {'total_count': 1, 'workflow_runs': []},
        {'total_count': 0, 'workflow_runs': []},
    ]
    assert inspect(client)['status'] == 'CLEAR'
    assert client.response_counts[path] == 2
    for status in STATUSES:
        assert any(f'status={status}&' in p for p in client.reads)


def test_persistent_incomplete_pagination_exhausts_bounded_rescans(capsys):
    client = Queue()
    path = '/actions/runs?branch=main&status=queued&per_page=100&page=1'
    client.response_sequences[path] = [{'total_count': 1, 'workflow_runs': []}]
    with pytest.raises(ValueError, match='^QUEUE_RUN_LIST_INCOMPLETE$'):
        inspect(client)
    assert client.response_counts[path] == q.MAX_ACTIVE_SCAN_ATTEMPTS
    import json
    diagnostics = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert diagnostics[-1]['pagination'] == {'status': 'queued', 'page': 1, 'total': 1, 'rows': 0, 'seen': 0}


def test_cli_preserves_machine_code_after_retry_exhaustion(monkeypatch, capsys):
    from scripts import candidate_delivery_watchdog as watchdog
    client = Queue()
    path = '/actions/runs?branch=main&status=queued&per_page=100&page=1'
    client.responses[path] = {'total_count': 1, 'workflow_runs': []}
    monkeypatch.setattr(watchdog, 'GitHub', lambda token: client)
    monkeypatch.setattr(q.sys, 'argv', ['queue', '--signal-date', DAY])
    assert q.main() == 1
    import json
    assert json.loads(capsys.readouterr().out) == {'status': 'BLOCKED', 'reason': 'QUEUE_RUN_LIST_INCOMPLETE'}


def test_running_evidence_before_retry_cannot_disappear_into_clear():
    client = Queue()
    current = run(status='in_progress', created_at='2026-11-03T11:00:00Z')
    path = '/actions/runs?branch=main&status=in_progress&per_page=100&page=1'
    client.response_sequences[path] = [
        {'total_count': 2, 'workflow_runs': [current]},
        {'total_count': 0, 'workflow_runs': []},
    ]
    assert_blocked(client, 101)
    assert client.response_counts[path] == 2


def test_changed_pagination_total_retries_even_if_final_count_matches():
    client = Queue()
    page1 = '/actions/runs?branch=main&status=queued&per_page=100&page=1'
    page2 = '/actions/runs?branch=main&status=queued&per_page=100&page=2'
    client.response_sequences[page1] = [
        {'total_count': 2, 'workflow_runs': [run(workflow='lint.yml')]},
        {'total_count': 0, 'workflow_runs': []},
    ]
    client.responses[page2] = {'total_count': 1, 'workflow_runs': []}
    assert inspect(client)['status'] == 'CLEAR'
    assert client.response_counts[page1] == 2


@pytest.mark.parametrize('error', [PermissionError('403'), TimeoutError('network'), ValueError('QUEUE_API_UNAVAILABLE')])
def test_active_api_failure_is_not_retried_or_treated_as_empty(error):
    client = Queue()
    path = '/actions/runs?branch=main&status=queued&per_page=100&page=1'
    client.response_sequences[path] = [error, {'total_count': 0, 'workflow_runs': []}]
    with pytest.raises(type(error)):
        inspect(client)
    assert client.response_counts[path] == 1

