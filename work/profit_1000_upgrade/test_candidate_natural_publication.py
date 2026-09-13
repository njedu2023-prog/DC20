"""Entirely synthetic Git/ZIP/run observations; no network or future truth.

The fixed historical 779/999 model bytes are only validated. P0 fixture values
are synthetic; HOST flags below construct adversarial API fixtures, not real
source authority. Fake clients always return SYNTHETIC_PUBLICATION_CHECK_ONLY.
"""
import base64
from copy import deepcopy
from datetime import datetime, timezone
import io
from pathlib import Path
import socket
import stat
import zipfile

import pytest

from work.profit_1000_upgrade import candidate_natural_publication as m
from work.profit_1000_upgrade import test_candidate_natural_p0_github as p0

RUN_ID = "77712345678"
CODE_HEAD, PUBLISHED = "f" * 40, "e" * 40
API_RUN = "/actions/runs/" + RUN_ID
WORKFLOW_API = "/actions/workflows/research_candidate_natural_forward.yml"
STAMP = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)


def zip_bytes(files, prefix="", *, extra=()):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in [*((prefix + p, raw) for p, raw in files.items()), *extra]:
            archive.writestr(name, body)
    return stream.getvalue()


class FakeGitHub:
    def __init__(self, responses, archives):
        self.responses, self.archives, self.requests = responses, archives, []
    def get_json(self, path):
        # Exercise the actual frozen client's path policy, not a permissive mock.
        return m.gh.GitHubReadClient.get_json(self, path)
    def get_artifact_zip(self, identity):
        return m.gh.GitHubReadClient.get_artifact_zip(self, identity)
    def _open(self, url, *, authorization, limit, allow_302=False):
        if allow_302:
            assert authorization and url.startswith("https://api.github.com" + m.gh.API_PREFIX + "/actions/artifacts/")
            identity = int(url.rsplit("/", 2)[1])
            self.requests.append(("artifact", identity))
            return "https://artifact.actions.githubusercontent.com/" + str(identity)
        if not authorization:
            assert url.startswith("https://artifact.actions.githubusercontent.com/")
            return self.archives[int(url.rsplit("/", 1)[1])]
        assert url.startswith("https://api.github.com" + m.gh.API_PREFIX + "/")
        path = url.removeprefix("https://api.github.com")
        self.requests.append(path)
        return m.gh.json_bytes(self.responses[path])


def make_case(tmp_path, monkeypatch, *, size=2, prefix=""):
    source = p0.fixture(tmp_path, monkeypatch, size=size)
    imported = p0.execute(source)
    imported.update(injected_client_for_test=False, network_calls_performed=38)
    # Freeze only synthetic P0 data, with the real unchanged model and scorer.
    checker = m.gh.adapter.publisher.build_primary_d_runtime_index
    with monkeypatch.context() as temporary:
        temporary.setattr(m.gh.adapter.publisher, "build_primary_d_runtime_index",
            lambda root, **kw: m.gh._index(root, source["day"]))
        receipt = m.natural.freeze_natural_day(source["output"], tmp_path.resolve() / "candidate_natural_forward",
            m.ROOT / m.MODEL_PATH, signal_date=source["day"], expected_p0_sha256=imported["expected_p0_sha256"],
            clock=lambda: STAMP)
    assert m.gh.adapter.publisher.build_primary_d_runtime_index is checker
    snapshot = m.gh.parse_json(Path(receipt["snapshot_path"]).read_bytes())
    snapshot["clock_mode"] = receipt["clock_mode"] = "HOST_SYSTEM_UTC"
    snapshot.pop("snapshot_sha256")
    snapshot["snapshot_sha256"] = m.natural.scorer.canonical_sha(snapshot)
    snapshot_raw = m.natural.storage.encoded(snapshot)
    receipt.update(snapshot_file_sha256=m.gh.sha256(snapshot_raw), snapshot_sha256=snapshot["snapshot_sha256"])
    context = {"repository": m.gh.REPOSITORY, "run_id": int(RUN_ID), "run_attempt": 1,
        "code_head_sha": CODE_HEAD, "branch": "main", "workflow_path": m.WORKFLOW_PATH}
    files, _ = m.workflow.prepare_publication(snapshot_raw, receipt, imported, context,
        now=datetime(2026, 9, 14, 12, 1, tzinfo=timezone.utc))
    local, _ = m.code_guard()
    parent_files = {**local, **source["sources"], "protected.txt": b"unchanged formal evidence"}
    parent_tree, tree = p0.git_tree(parent_files), p0.git_tree({**parent_files, **files})
    ack = {"schema_version": "dc20_natural_candidate_git_publication_ack_v1", "status": "GIT_PUBLICATION_ACKNOWLEDGED",
        "signal_date": source["day"], "commit_sha": PUBLISHED, "parent_sha": CODE_HEAD, "tree_sha": tree["sha"],
        "files": [{"path": p, "sha256": m.gh.sha256(b), "git_blob_sha1": m.gh.git_blob(b), "bytes": len(b)} for p, b in sorted(files.items())],
        "acknowledged_at_host_utc": "2026-09-14T12:01:00+00:00", "timestamp_basis": "HOST_CLOCK_AFTER_GITHUB_NONFORCE_REF_ACK",
        "independent_job_timing_check_still_required": True, "natural_forward_admission_issued": False,
        "production_activation_allowed": False, "actual_execution_claimed": False, "existing_files_modified": False}
    archive_files = {"publication.json": m.gh.json_bytes(ack), "p0_source_receipt.json": m.gh.json_bytes(imported),
        "local_freeze_receipt.json": m.gh.json_bytes(receipt), f"candidate_natural_forward/day_{source['day']}.json": snapshot_raw,
        f"candidate_natural_forward/day_{source['day']}.json.lock": b"",
        **{"sources/" + p: raw for p, raw in source["sources"].items()}}
    archive = zip_bytes(archive_files, prefix)
    run = {"id": int(RUN_ID), "workflow_id": m.WORKFLOW_ID, "path": m.WORKFLOW_PATH, "name": m.WORKFLOW_NAME,
        "head_branch": "main", "head_sha": CODE_HEAD, "run_attempt": 1, "status": "completed", "conclusion": "success",
        "event": "workflow_run", "repository": {"full_name": m.gh.REPOSITORY}, "head_repository": {"full_name": m.gh.REPOSITORY},
        "created_at": "2026-09-14T11:20:00Z", "run_started_at": "2026-09-14T11:20:01Z", "updated_at": "2026-09-14T12:11:00Z"}
    jobs = [{"id": 201+i, "run_id": int(RUN_ID), "name": name, "status": "completed", "conclusion": "success",
        "started_at": "2026-09-14T11:21:00Z" if i == 0 else "2026-09-14T11:23:00Z",
        "completed_at": "2026-09-14T11:22:00Z" if i == 0 else "2026-09-14T12:10:00Z"} for i, name in enumerate(m.JOBS)]
    artifact = {"id": 900, "name": f"dc20-candidate-natural-{RUN_ID}-1", "expired": False,
        "size_in_bytes": len(archive), "digest": "sha256:" + m.gh.sha256(archive),
        "workflow_run": {"id": int(RUN_ID), "head_branch": "main", "head_sha": CODE_HEAD},
        "created_at": "2026-09-14T12:02:00Z", "updated_at": "2026-09-14T12:03:00Z"}
    responses = deepcopy(source["client"].responses)
    responses.update({m.gh.API_PREFIX + WORKFLOW_API: {"id": m.WORKFLOW_ID, "path": m.WORKFLOW_PATH, "name": m.WORKFLOW_NAME, "state": "active"},
        m.gh.API_PREFIX + API_RUN: run,
        m.gh.API_PREFIX + API_RUN + "/attempts/1/jobs?per_page=100": {"total_count": 2, "jobs": jobs},
        m.gh.API_PREFIX + API_RUN + "/artifacts?per_page=100": {"total_count": 1, "artifacts": [artifact]},
        m.gh.API_PREFIX + "/git/commits/" + CODE_HEAD: {"sha": CODE_HEAD, "tree": {"sha": parent_tree["sha"]}, "parents": []},
        m.gh.API_PREFIX + "/git/trees/" + parent_tree["sha"] + "?recursive=1": parent_tree,
        m.gh.API_PREFIX + "/git/commits/" + PUBLISHED: {"sha": PUBLISHED, "tree": {"sha": tree["sha"]}, "parents": [{"sha": CODE_HEAD}]},
        m.gh.API_PREFIX + "/git/trees/" + tree["sha"] + "?recursive=1": tree,
        m.gh.API_PREFIX + "/git/ref/heads/main": {"ref": "refs/heads/main", "object": {"type": "commit", "sha": PUBLISHED}}})
    for path, raw in files.items():
        sha = m.gh.git_blob(raw)
        responses[m.gh.API_PREFIX + "/git/blobs/" + sha] = {"sha": sha, "size": len(raw), "encoding": "base64", "content": base64.encodebytes(raw).decode()}
    client = FakeGitHub(responses, {900: archive, 12345: source["client"].archive})
    return {"client": client, "source": source, "files": files, "archive_files": archive_files, "archive": archive,
        "run": run, "jobs": jobs, "artifact": artifact, "ack": ack, "parent_tree": parent_tree, "tree": tree, "prefix": prefix}


def run(case):
    return m.verify_publication(expected_freeze_run_id=RUN_ID, github_client=case["client"])


@pytest.mark.parametrize("size,prefix", [(0, ""), (1, "dc20-candidate-natural-fixture/"), (2, ""), (10, "")])
def test_full_synthetic_git_zip_time_chain_no_false_authority(tmp_path, monkeypatch, size, prefix):
    case = make_case(tmp_path, monkeypatch, size=size, prefix=prefix)
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("NETWORK"))
    monkeypatch.setattr(m.natural.scorer, "predict_forward", lambda *a, **k: pytest.fail("SCORING_DURING_VERIFY"))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = run(case)
    assert result["status"] == "SYNTHETIC_PUBLICATION_CHECK_ONLY"
    assert not result["research_prospective_publication_observed"] and result["injected_client_for_test"]
    assert result["published_commit_sha"] == PUBLISHED and len(result["published_file_bindings"]) == 4
    assert len(result["original_source_bindings"]) == 28 and result["bounded_request_cost"] <= 40
    assert result["model_canonical_sha256"] == m.natural.MODEL_SHA
    assert result["current_main_four_file_bytes_preserved"] and result["git_ancestry_verified"] is False
    assert result["files_written"] == result["remote_writes_performed"] == 0
    assert all(result[k] is False for k in ("source_authority_issued", "natural_outcome_admission_issued", "production_activation_allowed",
        "formal_model_replacement_allowed", "actual_execution_claimed", "actual_capacity_verified", "provider_timestamp_semantics_confirmed",
        "future_outcomes_read", "predictions_recomputed", "model_training_performed"))
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("field,value", [("workflow_id", True), ("workflow_id", 1), ("path", ".github/workflows/other.yml"),
    ("name", "other"), ("head_branch", "other"), ("run_attempt", 2), ("run_attempt", True), ("status", "in_progress"),
    ("conclusion", "failure"), ("event", "push"), ("head_sha", "invalid"), ("repository", {"full_name": "other/repo"}),
    ("head_repository", {"full_name": "other/repo"}), ("created_at", "2026-09-15T12:00:00Z")])
def test_run_identity_rejected_before_archive(tmp_path, monkeypatch, field, value):
    case = make_case(tmp_path, monkeypatch); case["run"][field] = value
    with pytest.raises(ValueError): run(case)
    assert not any(type(p) is tuple for p in case["client"].requests)


@pytest.mark.parametrize("kind", ["missing", "extra", "duplicate", "foreign", "failure", "skipped", "reverse", "late"])
def test_exact_jobs_and_independent_deadline(tmp_path, monkeypatch, kind):
    case = make_case(tmp_path, monkeypatch)
    doc = case["client"].responses[m.gh.API_PREFIX + API_RUN + "/attempts/1/jobs?per_page=100"]
    if kind == "missing": doc["jobs"].pop()
    elif kind == "extra": doc["jobs"].append(deepcopy(doc["jobs"][0]))
    elif kind == "duplicate": doc["jobs"][1]["name"] = m.JOBS[0]
    elif kind == "foreign": doc["jobs"][1]["run_id"] += 1
    elif kind in ("failure", "skipped"): doc["jobs"][1]["conclusion"] = kind
    elif kind == "reverse": doc["jobs"][1]["started_at"] = "2026-09-14T11:21:00Z"
    else:
        doc["jobs"][1]["completed_at"] = "2026-09-15T01:25:00Z"
        case["run"]["updated_at"] = "2026-09-15T01:26:00Z"
    with pytest.raises(ValueError): run(case)


@pytest.mark.parametrize("field,value", [("name", "other"), ("expired", True), ("id", True), ("digest", "sha256:"+"0"*64),
    ("size_in_bytes", 1), ("workflow_run", {"id": 1, "head_branch": "main", "head_sha": CODE_HEAD})])
def test_same_run_artifact_identity_and_raw_digest(tmp_path, monkeypatch, field, value):
    case = make_case(tmp_path, monkeypatch); case["artifact"][field] = value
    with pytest.raises(ValueError): run(case)


@pytest.mark.parametrize("name", ["future_outcomes_20260915.json", "sources/data/market/raw/2026/20260915/daily.csv",
    "sources/outputs/decision/profit_20260914.json", "candidate_natural_forward/day_20260915.json", "../publication.json",
    "/publication.json", "a\\publication.json", "another-root/publication.json"])
def test_unknown_or_future_member_not_opened(tmp_path, monkeypatch, name):
    case = make_case(tmp_path, monkeypatch)
    raw = zip_bytes(case["archive_files"], extra=[(name, b"NEVER_READ_FUTURE")])
    original = zipfile.ZipFile.open
    def guarded(self, member, *a, **kw):
        assert (member.filename if isinstance(member, zipfile.ZipInfo) else member) != name, "UNKNOWN_MEMBER_READ"
        return original(self, member, *a, **kw)
    monkeypatch.setattr(zipfile.ZipFile, "open", guarded)
    with pytest.raises(ValueError): m.read_archive(raw)


@pytest.mark.parametrize("kind", ["duplicate", "symlink", "fifo", "parent_file", "unknown_dir", "two_roots", "nonempty_lock", "crc", "limit"])
def test_archive_adversarial(tmp_path, monkeypatch, kind):
    case = make_case(tmp_path, monkeypatch); entries = deepcopy(case["archive_files"]); extra = []
    if kind == "duplicate": extra = [("publication.json", entries["publication.json"])]
    elif kind in ("symlink", "fifo"):
        item = zipfile.ZipInfo("publication.json")
        item.external_attr = (stat.S_IFLNK if kind == "symlink" else stat.S_IFIFO) << 16
        body = entries.pop("publication.json"); extra = [(item, body)]
    elif kind == "parent_file": entries["sources"] = b"not directory"
    elif kind == "unknown_dir": extra = [("unrelated/", b"")]
    elif kind == "two_roots": extra = [("dc20-candidate-natural-other/publication.json", entries["publication.json"])]
    elif kind == "nonempty_lock": entries["candidate_natural_forward/day_20260914.json.lock"] = b"unexpected"
    raw = zip_bytes(entries, extra=extra)
    if kind == "crc": raw = raw[:70] + bytes([raw[70] ^ 255]) + raw[71:]
    elif kind == "limit": monkeypatch.setattr(m, "MAX_MEMBERS", 1)
    with pytest.raises((ValueError, zipfile.BadZipFile)): m.read_archive(raw)


@pytest.mark.parametrize("which", ["published_blob", "code_blob", "source_blob", "parent", "main", "model", "snapshot_flag"])
def test_git_binding_and_model_changed(tmp_path, monkeypatch, which):
    case = make_case(tmp_path, monkeypatch)
    responses = case["client"].responses
    if which == "published_blob":
        key = m.gh.API_PREFIX + "/git/blobs/" + case["ack"]["files"][0]["git_blob_sha1"]
        responses[key]["content"] = base64.b64encode(b"fake").decode()
    elif which in ("code_blob", "source_blob"):
        tree = case["parent_tree"] if which == "code_blob" else responses[m.gh.API_PREFIX + "/git/trees/" + case["source"]["tree"]["sha"] + "?recursive=1"]
        next(x for x in tree["tree"] if x["type"] == "blob")["sha"] = "0"*40
    elif which == "parent": responses[m.gh.API_PREFIX + "/git/commits/" + PUBLISHED]["parents"] = []
    elif which == "main": responses[m.gh.API_PREFIX + "/git/ref/heads/main"]["object"]["sha"] = CODE_HEAD
    elif which == "model": monkeypatch.setitem(m.FIXED_FILES, m.MODEL_PATH, "0"*64)
    else:
        snapshot = m.gh.parse_json(case["archive_files"]["candidate_natural_forward/day_20260914.json"])
        snapshot["clock_mode"] = "INJECTED_TEST_CLOCK_RESEARCH_ONLY"
        snapshot.pop("snapshot_sha256"); snapshot["snapshot_sha256"] = m.natural.scorer.canonical_sha(snapshot)
        with pytest.raises(ValueError): m._snapshot(m.natural.storage.encoded(snapshot), "20260914")
        return
    with pytest.raises(ValueError): run(case)


@pytest.mark.parametrize("which", ["run", "jobs", "artifact", "p0", "code"])
def test_final_guards_recheck_metadata_and_local_code(tmp_path, monkeypatch, which):
    case = make_case(tmp_path, monkeypatch); original = case["client"].get_json; counts = {}
    def changed(path):
        counts[path] = counts.get(path, 0) + 1
        if path == m.gh.API_PREFIX + API_RUN and counts[path] == 2:
            if which == "run": case["run"]["updated_at"] = "2026-09-14T12:12:00Z"
            elif which == "jobs": case["jobs"][0]["id"] += 1
            elif which == "artifact": case["artifact"]["expired"] = True
            elif which == "p0": case["client"].responses[case["source"]["api_base"]]["conclusion"] = "failure"
            else: monkeypatch.setattr(m, "SELF_SHA", "0"*64)
        return original(path)
    case["client"].get_json = changed
    with pytest.raises(ValueError): run(case)


@pytest.mark.parametrize("value", [None, 1, True, "0", "01", "latest", "1/2"])
def test_explicit_run_id_before_network(value):
    client = FakeGitHub({}, {})
    with pytest.raises(ValueError): m.verify_publication(expected_freeze_run_id=value, github_client=client)
    assert client.requests == []


def test_get_budget_never_retries(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch)
    monkeypatch.setattr(m, "MAX_CALLS", 2)
    with pytest.raises(ValueError, match="BUDGET"): run(case)
    assert len(case["client"].requests) == 2


def test_original_client_no_auth_cdn_tests_remain_delegated():
    assert m.gh.GitHubReadClient.__module__ == "work.profit_1000_upgrade.candidate_natural_p0_github"
    assert m.IMPORTER_SHA == m.gh.SELF_SHA and m.gh.MAX_API_CALLS == 50


def test_frozen_real_client_path_policy_rejects_compare_dots():
    client = FakeGitHub({}, {})
    with pytest.raises(m.gh.ImportBlocked, match="REPOSITORY_API_PATH"):
        client.get_json(m.gh.API_PREFIX + "/compare/" + PUBLISHED + "...main")
    assert client.requests == []


@pytest.mark.parametrize("kind", ["p0_mode", "p0_day", "p0_stage", "p0_rank", "source_grant", "mapped_origin"])
def test_original_p0_membership_and_provenance_not_claimed_by_flags(tmp_path, monkeypatch, kind):
    case = make_case(tmp_path, monkeypatch)
    receipt = deepcopy(case["source"]["receipt"])
    contract = deepcopy(case["source"]["contract"])
    snapshot = m.gh.parse_json(case["archive_files"]["candidate_natural_forward/day_20260914.json"])
    imported = m.gh.parse_json(case["archive_files"]["p0_source_receipt.json"])
    if kind == "p0_mode": receipt["generation_mode"] = "RECOVERY"
    elif kind == "p0_day": receipt["exec_date"] = "20260916"
    elif kind == "p0_stage": contract["rows"][0]["stage_transition"] = "4→5"
    elif kind == "p0_rank": contract["rows"][0]["promotion_rank"] = 1.0
    elif kind == "source_grant": snapshot["D_source_evidence"]["source_authority_issued"] = True
    else: snapshot["D_source_evidence"]["source_file_bindings"][0]["origin_path"] += ".other"
    with pytest.raises(ValueError):
        m._p0_contract(receipt, contract, snapshot, case["source"]["revision"], imported)


def test_no_time_or_ack_override_cli(monkeypatch):
    for option in ("--now", "--ack", "--artifact", "--signal-date", "--activate", "--output"):
        with pytest.raises(SystemExit): m.main([option, "untrusted"])
