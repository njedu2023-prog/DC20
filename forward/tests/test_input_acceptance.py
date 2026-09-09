"""Synthetic Actions/input-acceptance boundaries; no real network or models."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from forward import input_acceptance as acceptance
from forward.storage import encoded


SOURCE_SHA = "a" * 40
PRED_SHA = "b" * 40
MARKET_SHA = "c" * 40
CRON = "15 13 * * 1"
CREATED = "2026-09-07T13:16:00Z"
NOW = "2026-09-07T13:17:00Z"
OPEN = ["20260904", "20260907", "20260908", "20260909", "20260910",
        "20260911", "20260914", "20260915", "20260930", "20261009",
        "20261012", "20261013"]


def identities():
    env = {
        "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": acceptance.REPOSITORY,
        "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "schedule",
        "GITHUB_WORKFLOW_REF": f"{acceptance.REPOSITORY}/{acceptance.WORKFLOW}@refs/heads/main",
        "GITHUB_SHA": SOURCE_SHA, "GITHUB_WORKFLOW_SHA": SOURCE_SHA,
        "GITHUB_RUN_ID": "101", "GITHUB_RUN_ATTEMPT": "1", "FORWARD_SCHEDULE": CRON,
    }
    event = {"schedule": CRON}
    run = {
        "id": 101, "run_attempt": 1, "event": "schedule", "head_sha": SOURCE_SHA,
        "head_branch": "main", "repository": {"full_name": acceptance.REPOSITORY},
        "head_repository": {"full_name": acceptance.REPOSITORY}, "path": acceptance.WORKFLOW,
        "status": "in_progress", "workflow_id": 202, "created_at": CREATED,
    }
    workflow = {"id": 202, "path": acceptance.WORKFLOW, "state": "active"}
    return env, event, run, workflow


def test_validate_run_first_natural_identity():
    env, event, run, workflow = identities()
    result = acceptance.validate_run(env, event, run, workflow, SOURCE_SHA)
    assert result == {
        "run_id": 101, "run_attempt": 1, "workflow_id": 202,
        "workflow_path": acceptance.WORKFLOW, "source_revision": SOURCE_SHA,
        "event_name": "schedule", "schedule": CRON, "run_created_at_utc": CREATED,
        "url": f"https://github.com/{acceptance.REPOSITORY}/actions/runs/101",
    }


@pytest.mark.parametrize("key,value", [
    ("GITHUB_ACTIONS", "false"), ("GITHUB_ACTIONS", None),
    ("GITHUB_REPOSITORY", "someone/DC20"), ("GITHUB_REF", "refs/heads/review"),
    ("GITHUB_EVENT_NAME", "workflow_dispatch"), ("GITHUB_EVENT_NAME", "workflow_run"),
    ("GITHUB_WORKFLOW_REF", f"{acceptance.REPOSITORY}/other.yml@refs/heads/main"),
    ("GITHUB_SHA", "d" * 40), ("GITHUB_WORKFLOW_SHA", "d" * 40),
    ("GITHUB_RUN_ID", "0"), ("GITHUB_RUN_ID", "0101"), ("GITHUB_RUN_ID", "101/attempts/1"),
    ("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_RUN_ATTEMPT", 1),
    ("FORWARD_SCHEDULE", "15 14 * * 1"),
])
def test_validate_run_rejects_env_mismatch(key, value):
    env, event, run, workflow = identities()
    env[key] = value
    with pytest.raises(ValueError):
        acceptance.validate_run(env, event, run, workflow, SOURCE_SHA)


@pytest.mark.parametrize("key,value", [
    ("id", 102), ("id", "101"), ("id", 101.0),
    ("run_attempt", 2), ("run_attempt", True), ("run_attempt", 1.0), ("run_attempt", "1"),
    ("event", "workflow_dispatch"), ("head_sha", "d" * 40), ("head_branch", "review"),
    ("repository", {"full_name": "someone/DC20"}),
    ("head_repository", {"full_name": "someone/DC20"}),
    ("path", ".github/workflows/other.yml"), ("status", "completed"),
    ("workflow_id", 203), ("workflow_id", 202.0),
])
def test_validate_run_rejects_api_run_mismatch(key, value):
    env, event, run, workflow = identities()
    run[key] = value
    with pytest.raises(ValueError):
        acceptance.validate_run(env, event, run, workflow, SOURCE_SHA)


@pytest.mark.parametrize("key,value", [
    ("id", "202"), ("id", 202.0), ("id", True), ("id", 203),
    ("path", ".github/workflows/other.yml"), ("state", "disabled_manually"),
])
def test_validate_run_rejects_workflow_mismatch(key, value):
    env, event, run, workflow = identities()
    workflow[key] = value
    with pytest.raises(ValueError):
        acceptance.validate_run(env, event, run, workflow, SOURCE_SHA)


@pytest.mark.parametrize("event", [{}, [], None, {"schedule": "15 14 * * 1"}])
def test_validate_run_rejects_event_mismatch(event):
    env, _, run, workflow = identities()
    with pytest.raises(ValueError):
        acceptance.validate_run(env, event, run, workflow, SOURCE_SHA)


@pytest.mark.parametrize("sha", ["", "a" * 39, "A" * 40, "../main", "a" * 41])
def test_validate_run_rejects_nonimmutable_checkout(sha):
    env, event, run, workflow = identities()
    with pytest.raises(ValueError):
        acceptance.validate_run(env, event, run, workflow, sha)


@pytest.fixture
def context(tmp_path):
    root = tmp_path / "repo"
    (root / "forward").mkdir(parents=True)
    calendar = ("exchange,cal_date,is_open\n" + "".join(f"SSE,{date},1\n" for date in OPEN)).encode()
    (root / "calendar.csv").write_bytes(calendar)
    config = {
        "production_enabled": False, "activated_at_utc": None, "start_signal_date": None,
        "phase": "MIGRATION_ACCEPTANCE", "formal_trade_actions_allowed": False,
        "legacy_statistics_import_allowed": False, "calendar_path": "calendar.csv",
        "calendar_sha256": hashlib.sha256(calendar).hexdigest(),
    }
    (root / "forward/config.json").write_bytes(encoded(config))
    env, event, run, workflow = identities()
    event_path = tmp_path / "event.json"
    event_path.write_bytes(encoded(event))
    env["GITHUB_EVENT_PATH"] = str(event_path)
    env["GITHUB_TOKEN"] = "must-not-appear-in-evidence"
    c = SimpleNamespace(root=root, output=tmp_path / "accepted", config=config,
                        env=env, event=event, run=run, workflow=workflow,
                        event_path=event_path, calls=[], collections=[], checkouts=[], now=NOW)

    def get(url):
        c.calls.append(url)
        if url == f"https://api.github.com/repos/{acceptance.REPOSITORY}/actions/runs/101":
            return encoded(c.run)
        if url == f"https://api.github.com/repos/{acceptance.REPOSITORY}/actions/workflows/202":
            return encoded(c.workflow)
        for repo, sha in zip(acceptance.UPSTREAMS, (PRED_SHA, MARKET_SHA)):
            if url == f"https://api.github.com/repos/{repo}/git/ref/heads/main":
                return encoded({"ref": "refs/heads/main", "object": {"type": "commit", "sha": sha}})
        raise AssertionError(f"unexpected remote access: {url}")

    def collect(root, output, date, pred, market, *, fetch, now_utc):
        assert not (c.output / "receipt.json").exists()
        assert root == c.root and output == c.output / "bundle"
        c.collections.append((date, pred, market, now_utc))
        assert fetch is c.get
        output.mkdir()
        manifest = {"signal_date": date, "pred_source_sha": pred, "market_source_sha": market}
        # Deliberately noncanonical: the receipt must hash bytes actually written.
        (output / "manifest.json").write_bytes((json.dumps(manifest, indent=2) + "\n").encode())
        return manifest

    def checkout(root):
        assert root == c.root
        c.checkouts.append(root)
        return SOURCE_SHA

    c.get, c.collector, c.checkout = get, collect, checkout
    c.clock = lambda: c.now
    return c


def accept(c, **overrides):
    args = dict(env=c.env, get=c.get, clock=c.clock, collector=c.collector, checkout=c.checkout)
    args.update(overrides)
    return acceptance.accept_inputs(c.root, c.output, **args)


def set_slot(c, *, cron, created, now):
    c.env["FORWARD_SCHEDULE"] = cron
    c.event["schedule"] = cron
    c.event_path.write_bytes(encoded(c.event))
    c.run["created_at"] = created
    c.now = now


def test_accept_inputs_exact_d_resolves_each_upstream_once_and_receipt_last(context, monkeypatch):
    c = context
    original_open = Path.open
    receipt_opens = []

    def observe_open(path, mode="r", *args, **kwargs):
        if path == c.output / "receipt.json" and "x" in mode:
            assert len(c.collections) == 1 and len(c.checkouts) == 2
            assert (c.output / "bundle/manifest.json").is_file()
            assert not path.exists()
            receipt_opens.append(mode)
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", observe_open)
    result = accept(c)
    assert c.collections == [("20260907", PRED_SHA, MARKET_SHA, NOW)]
    assert c.calls == [
        f"https://api.github.com/repos/{acceptance.REPOSITORY}/actions/runs/101",
        f"https://api.github.com/repos/{acceptance.REPOSITORY}/actions/workflows/202",
        *[f"https://api.github.com/repos/{repo}/git/ref/heads/main" for repo in acceptance.UPSTREAMS],
    ]
    assert result["status"] == "INPUTS_VALIDATED_NOT_PRODUCTION"
    assert result["sources"] == dict(zip(acceptance.UPSTREAMS, (PRED_SHA, MARKET_SHA)))
    assert result["gate"]["signal_date"] == "20260907"
    assert result["gate"]["exec_date"] == "20260908"
    assert result["gate"]["exit_date"] == "20260909"
    assert result["bundle_sha256"] == hashlib.sha256((c.output / "bundle/manifest.json").read_bytes()).hexdigest()
    assert receipt_opens == ["xb"]
    raw = (c.output / "receipt.json").read_bytes()
    assert json.loads(raw) == result
    assert b"must-not-appear" not in raw
    assert result["read_only"] is True
    assert all(result[key] is False for key in
               ("production_activated", "inference_performed", "ledger_written", "published_list"))


def test_closed_sse_session_never_resolves_upstreams_or_collects(context):
    c = context
    set_slot(c, cron="15 13 * * 4", created="2026-10-01T13:16:00Z", now="2026-10-01T13:17:00Z")

    def forbidden(*args, **kwargs):
        raise AssertionError("closed SSE date must not invoke the collector")

    result = accept(c, collector=forbidden)
    assert len(c.calls) == 2 and all("/actions/" in url for url in c.calls)
    assert not c.collections and not (c.output / "bundle").exists()
    assert result["status"] == "CLOSED" and result["sources"] == {}
    assert result["bundle_sha256"] is None
    assert result["gate"]["signal_date"] == "20261001"
    assert result["gate"]["upstream_reads_allowed"] is False
    assert json.loads((c.output / "receipt.json").read_bytes()) == result


def test_delayed_friday_uses_original_d_not_saturday(context):
    c = context
    set_slot(c, cron="15 13 * * 5", created="2026-09-05T00:30:00Z", now="2026-09-05T00:31:00Z")
    result = accept(c)
    assert c.collections[0][0] == "20260904"
    assert (result["gate"]["signal_date"], result["gate"]["exec_date"], result["gate"]["exit_date"]) == (
        "20260904", "20260907", "20260908")
    assert result["gate"]["late"] is True


def test_wrong_source_date_collector_failure_never_writes_receipt(context):
    c = context

    def wrong_date(*args, **kwargs):
        (c.output / "bundle").mkdir()
        (c.output / "bundle/partial.csv").write_text("trade_date\n20260904\n")
        raise ValueError("candidate trade_date differs from exact D")

    with pytest.raises(ValueError, match="exact D"):
        accept(c, collector=wrong_date)
    assert (c.output / "bundle/partial.csv").exists()
    assert not (c.output / "receipt.json").exists()


@pytest.mark.parametrize("kind", ["rerun", "dispatch", "wrong_head", "inactive_workflow", "unknown_cron"])
def test_accept_identity_failure_never_resolves_upstreams(context, kind):
    c = context
    if kind == "rerun":
        c.env["GITHUB_RUN_ATTEMPT"] = "2"
        c.run["run_attempt"] = 2
    elif kind == "dispatch":
        c.env["GITHUB_EVENT_NAME"] = "workflow_dispatch"
        c.run["event"] = "workflow_dispatch"
    elif kind == "wrong_head":
        c.run["head_sha"] = "d" * 40
    elif kind == "inactive_workflow":
        c.workflow["state"] = "disabled_manually"
    else:
        set_slot(c, cron="15 13 * * 1-5", created=CREATED, now=NOW)
    with pytest.raises(ValueError):
        accept(c)
    assert len(c.calls) == 2 and all("/actions/" in url for url in c.calls)
    assert not c.collections and not c.output.exists()


def test_invalid_source_ref_never_collects_or_writes_receipt(context):
    c = context
    original_get = c.get

    def get(url):
        if "/a-top10/git/ref/" in url:
            c.calls.append(url)
            return encoded({"ref": "refs/heads/main", "object": {"type": "commit", "sha": "main"}})
        return original_get(url)

    c.get = get
    with pytest.raises(ValueError, match="immutable commit"):
        accept(c)
    assert len(c.calls) == 3 and not c.collections and not c.output.exists()


@pytest.mark.parametrize("phase", ["before_lookup", "after_refs", "after_collect", "after_checkout"])
def test_crossed_admission_deadline_never_issues_complete_receipt(context, phase):
    c = context
    original_get, original_collect, original_checkout = c.get, c.collector, c.checkout
    expired = "2026-09-08T01:15:00Z"
    if phase == "before_lookup":
        c.now = expired

    def get(url):
        result = original_get(url)
        if phase == "after_refs" and "/a-share-top3-data/git/ref/" in url:
            c.now = expired
        return result

    def collect(*args, **kwargs):
        result = original_collect(*args, **kwargs)
        if phase == "after_collect":
            c.now = expired
        return result

    def checkout(root):
        result = original_checkout(root)
        if phase == "after_checkout" and len(c.checkouts) == 2:
            c.now = expired
        return result

    c.get = get
    with pytest.raises(ValueError, match="twelve-hour|09:20"):
        accept(c, collector=collect, checkout=checkout)
    assert not (c.output / "receipt.json").exists()
    if phase == "before_lookup":
        assert len(c.calls) == 2
    if phase in {"before_lookup", "after_refs"}:
        assert not c.collections


def test_checkout_change_after_collection_never_issues_receipt(context):
    c = context
    revisions = iter([SOURCE_SHA, "d" * 40])
    with pytest.raises(ValueError, match="checkout changed"):
        accept(c, checkout=lambda root: next(revisions))
    assert (c.output / "bundle/manifest.json").exists()
    assert not (c.output / "receipt.json").exists()


@pytest.mark.parametrize("mode", ["missing", "mismatch", "symlink"])
def test_missing_or_changed_completion_manifest_never_issues_receipt(context, mode):
    c = context

    def collect(*args, **kwargs):
        output = args[1]
        output.mkdir()
        returned = {"signal_date": "20260907"}
        path = output / "manifest.json"
        if mode == "mismatch":
            path.write_bytes(encoded({"signal_date": "20260904"}))
        elif mode == "symlink":
            target = c.output / "other.json"
            target.write_bytes(encoded(returned))
            path.symlink_to(target)
        return returned

    with pytest.raises(ValueError, match="manifest"):
        accept(c, collector=collect)
    assert not (c.output / "receipt.json").exists()


@pytest.mark.parametrize("key,value", [
    ("production_enabled", True), ("activated_at_utc", NOW), ("start_signal_date", "20260907"),
    ("phase", "PRODUCTION"), ("formal_trade_actions_allowed", True),
    ("legacy_statistics_import_allowed", True),
])
def test_active_or_trading_config_rejected_before_remote_reads(context, key, value):
    c = context
    c.config[key] = value
    (c.root / "forward/config.json").write_bytes(encoded(c.config))
    with pytest.raises(ValueError, match="inactive migration"):
        accept(c)
    assert not c.calls and not c.output.exists()


def test_calendar_hash_failure_precedes_remote_reads(context):
    c = context
    (c.root / "calendar.csv").write_text("exchange,cal_date,is_open\nSSE,20260907,1\n")
    with pytest.raises(ValueError, match="calendar hash"):
        accept(c)
    assert not c.calls and not c.output.exists()


@pytest.mark.parametrize("kind", ["relative", "inside_repo", "existing", "symlink"])
def test_output_must_be_new_physical_external_directory(context, kind):
    c = context
    if kind == "relative":
        c.output = Path("accepted")
    elif kind == "inside_repo":
        c.output = c.root / "accepted"
    elif kind == "existing":
        c.output.mkdir()
    else:
        parent = c.root.parent / "alias"
        parent.symlink_to(c.root.parent, target_is_directory=True)
        c.output = parent / "accepted"
    with pytest.raises(ValueError, match="outside|physical|overwrite"):
        accept(c)
    assert not c.calls


@pytest.mark.parametrize("mutation", [
    {"ref": "refs/heads/other"}, {"object": {"type": "tag", "sha": PRED_SHA}},
    {"object": {"type": "commit", "sha": "main"}},
    {"object": {"type": "commit", "sha": "A" * 40}},
])
def test_resolve_source_rejects_nonimmutable_ref(mutation):
    value = {"ref": "refs/heads/main", "object": {"type": "commit", "sha": PRED_SHA}}
    value.update(deepcopy(mutation))
    with pytest.raises(ValueError, match="immutable commit"):
        acceptance.resolve_source(acceptance.UPSTREAMS[0], lambda url: encoded(value))


def test_resolve_source_rejects_unapproved_repo_without_get():
    with pytest.raises(ValueError, match="unapproved"):
        acceptance.resolve_source("njedu2023-prog/top10-decision", lambda url: pytest.fail("network called"))


API_URL = f"https://api.github.com/repos/{acceptance.REPOSITORY}/actions/runs/101"
RAW_URL = f"https://raw.githubusercontent.com/{acceptance.UPSTREAMS[0]}/{PRED_SHA}/outputs/decisio/pred_decisio_20260907.csv"


class Response:
    def __init__(self, body=b"evidence"):
        self.body = body
        self.read_limits = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        self.read_limits.append(limit)
        return self.body


def fake_network(monkeypatch, outcomes):
    state = SimpleNamespace(requests=[], handlers=[], sleeps=[])

    class Opener:
        def open(self, request, timeout):
            state.requests.append((request, timeout))
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    def build(*handlers):
        state.handlers.extend(handlers)
        return Opener()

    monkeypatch.setattr(acceptance, "build_opener", build)
    monkeypatch.setattr(acceptance.time, "sleep", state.sleeps.append)
    return state


@pytest.mark.parametrize("url,authorized", [(API_URL, True), (RAW_URL, False)])
def test_fetch_get_token_only_on_api_and_redirect_handler_installed(monkeypatch, url, authorized):
    response = Response()
    state = fake_network(monkeypatch, [response])
    assert acceptance.fetch_bytes(url, token="synthetic-secret") == b"evidence"
    request, timeout = state.requests[0]
    headers = {key.lower(): value for key, value in request.header_items()}
    assert request.get_method() == "GET" and request.data is None and timeout == 40
    assert request.full_url == url
    assert headers.get("authorization") == ("Bearer synthetic-secret" if authorized else None)
    assert response.read_limits == [64 * 1024 * 1024 + 1]
    assert len(state.handlers) == 1 and isinstance(state.handlers[0], acceptance.NoRedirect)
    with pytest.raises(ValueError, match="redirect refused"):
        state.handlers[0].redirect_request(request, None, 302, "Found", {}, "https://evil.example/")
    assert not state.sleeps


@pytest.mark.parametrize("url", [
    "http://api.github.com/repos/njedu2023-prog/DC20/actions/runs/101",
    API_URL + "?token=secret", API_URL + "#fragment", API_URL.replace("api.github.com", "api.github.com.evil.example"),
    API_URL.replace("api.github.com", "api.github.com:443"), API_URL.replace("api.github.com", "user@api.github.com"),
    "https://api.github.com/repos/njedu2023-prog/DC20/actions/runs/101/rerun",
    "https://api.github.com/repos/njedu2023-prog/DC20/contents/forward/config.json",
    "https://api.github.com/repos/njedu2023-prog/top10-decision/git/ref/heads/main",
    RAW_URL.replace(PRED_SHA, "main"), RAW_URL.replace("a-top10/", "top10-decision/"),
    RAW_URL.replace("/outputs/", "/../outputs/"), RAW_URL.replace("/outputs/", "/./outputs/"),
    RAW_URL.replace("/outputs/", "/%2e%2e/outputs/"), RAW_URL + "?download=1",
])
def test_fetch_disallowed_url_never_constructs_network_opener(monkeypatch, url):
    monkeypatch.setattr(acceptance, "build_opener", lambda *args: pytest.fail("network constructed for disallowed URL"))
    with pytest.raises(ValueError):
        acceptance.fetch_bytes(url, token="synthetic-secret")


def test_fetch_retries_only_bounded_transient_failures(monkeypatch):
    state = fake_network(monkeypatch, [
        HTTPError(API_URL, 503, "temporary", {}, None), URLError("temporary"), Response(b"ok"),
    ])
    assert acceptance.fetch_bytes(API_URL) == b"ok"
    assert len(state.requests) == 3 and state.sleeps == [1, 2]


@pytest.mark.parametrize("status", [301, 302, 401, 403, 404, 422])
def test_fetch_nonretry_http_status_does_not_retry_or_leak_secret(monkeypatch, status):
    state = fake_network(monkeypatch, [HTTPError(API_URL, status, "synthetic-secret", {}, None)])
    with pytest.raises(ValueError, match=f"HTTP {status}") as error:
        acceptance.fetch_bytes(API_URL, token="synthetic-secret")
    assert "synthetic-secret" not in str(error.value)
    # The collector can classify optional HTTP 404 without guessing from strings.
    assert isinstance(error.value, acceptance.FetchError) and error.value.status == status
    assert len(state.requests) == 1 and not state.sleeps


def test_fetch_persistent_transient_http_failure_preserves_status_after_three_tries(monkeypatch):
    failure = HTTPError(API_URL, 503, "synthetic-secret", {}, None)
    state = fake_network(monkeypatch, [failure, failure, failure])
    with pytest.raises(acceptance.FetchError) as raised:
        acceptance.fetch_bytes(API_URL, token="synthetic-secret")
    assert raised.value.status == 503 and "synthetic-secret" not in str(raised.value)
    assert len(state.requests) == 3 and state.sleeps == [1, 2]


@pytest.mark.parametrize("error", [URLError("synthetic-secret"), TimeoutError("synthetic-secret")])
def test_fetch_network_retries_stop_after_three(monkeypatch, error):
    state = fake_network(monkeypatch, [error, error, error])
    with pytest.raises(ValueError, match="bounded retries") as raised:
        acceptance.fetch_bytes(API_URL, token="synthetic-secret")
    assert "synthetic-secret" not in str(raised.value)
    assert len(state.requests) == 3 and state.sleeps == [1, 2]


def test_fetch_oversize_response_is_blocked_without_retry(monkeypatch):
    class Oversize:
        def __len__(self):
            return 64 * 1024 * 1024 + 1

    state = fake_network(monkeypatch, [Response(Oversize())])
    with pytest.raises(ValueError, match="size limit"):
        acceptance.fetch_bytes(RAW_URL)
    assert len(state.requests) == 1 and not state.sleeps
