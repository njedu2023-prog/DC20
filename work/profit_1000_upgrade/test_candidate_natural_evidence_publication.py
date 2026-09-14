"""Synthetic observer run/artifact/Git only; never issues live-source proof."""
from copy import deepcopy
import io
from pathlib import Path
import socket
import stat
import zipfile

import pytest

from work.profit_1000_upgrade import candidate_natural_evidence_publication as m
from work.profit_1000_upgrade import test_candidate_natural_evidence as capture_tests
from work.profit_1000_upgrade import test_candidate_natural_publication as f

RUN_ID, WORKFLOW_ID = "88812345678", 888222
HEAD, COMMIT, PARENT = "9"*40, "8"*40, "7"*40


def make_case(tmp_path, monkeypatch, *, prefix=""):
    case = f.make_case(tmp_path, monkeypatch)
    case.update(opener=capture_tests.Opener(case), destination=tmp_path.resolve() / "capsule")
    _, _, manifest, bodies = capture_tests.materials(case)
    # Deliberately hypothetical real-capture flag for fake Git fixtures only.
    # FakeGitHub remains non-authoritative and never returns the private class.
    manifest["test_transport_injected"] = False
    manifest_raw = m.gh.json_bytes(manifest)
    captured = m.capture._result(case["destination"], manifest, manifest_raw)
    day = manifest["signal_date"]
    context = {"observer_workflow_path": m.WORKFLOW_PATH, "observer_run_id": int(RUN_ID), "run_attempt": 1,
        "code_head_sha": HEAD, "repository": m.gh.REPOSITORY, "branch": "main",
        "schema_version": "dc20_candidate_natural_observer_context_v1", "signal_date": day,
        "freeze_run_id": int(manifest["freeze_run_id"]), "snapshot_file_sha256": manifest["snapshot_file_sha256"],
        "manifest_sha256": m.sha(manifest_raw), "publication_observation_sha256": manifest["native_observation"]["sha256"],
        "capture_module_sha256": m.CAPTURE_SHA, "coordinator_sha256": m.OBSERVER_SHA, "writer_sha256": m.WRITER_SHA,
        "created_at_host_utc": "2026-09-14T12:15:00+00:00", "original_prospective_publication_observed": True,
        "evidence_natural_admission_issued": False, "production_activation_allowed": False, "actual_execution_claimed": False}
    context_raw = m.gh.json_bytes(context)
    root = m.PREFIX + day + "/"
    files = {root + "manifest.json": manifest_raw, root + "context.json": context_raw, **{root+p: raw for p, raw in bodies.items()}}
    local, _ = m.code_guard()
    old = {**local, "formal_protected.txt": b"unchanged"}
    parent_tree, tree = f.p0.git_tree(old), f.p0.git_tree({**old, **files})
    ack = {"schema_version": "dc20_candidate_natural_evidence_git_ack_v1", "status": "EVIDENCE_GIT_ACKNOWLEDGED",
        "signal_date": day, "commit_sha": COMMIT, "parent_sha": PARENT, "tree_sha": tree["sha"],
        "files": [{"path": p, "sha256": m.sha(b), "git_blob_sha1": m.gh.git_blob(b), "bytes": len(b)} for p, b in sorted(files.items())],
        "acknowledged_at_host_utc": "2026-09-14T12:16:00+00:00", "independent_observer_job_check_required": True,
        "natural_forward_admission_issued": False, "production_activation_allowed": False, "actual_execution_claimed": False,
        "existing_files_modified": False}
    archive_files = {"publication.json": m.gh.json_bytes(ack), "context.json": context_raw, "capture.json": m.gh.json_bytes(captured),
        "capsule/manifest.json": manifest_raw, **{"capsule/"+p: raw for p, raw in bodies.items()}}
    archive_raw = f.zip_bytes(archive_files, prefix=prefix)
    run = {"id": int(RUN_ID), "workflow_id": WORKFLOW_ID, "path": m.WORKFLOW_PATH, "name": m.WORKFLOW_NAME,
        "head_branch": "main", "head_sha": HEAD, "run_attempt": 1, "status": "completed", "conclusion": "success",
        "event": "workflow_run", "repository": {"full_name": m.gh.REPOSITORY}, "head_repository": {"full_name": m.gh.REPOSITORY},
        "created_at": "2026-09-14T12:11:00Z", "run_started_at": "2026-09-14T12:12:00Z", "updated_at": "2026-09-14T12:21:00Z"}
    jobs = [{"id": 501+i, "run_id": int(RUN_ID), "name": name, "status": "completed", "conclusion": "success",
        "started_at": "2026-09-14T12:12:00Z" if i == 0 else "2026-09-14T12:14:00Z",
        "completed_at": "2026-09-14T12:13:00Z" if i == 0 else "2026-09-14T12:20:00Z"} for i, name in enumerate(m.JOBS)]
    artifact = {"id": 98765, "name": f"dc20-candidate-observer-{RUN_ID}-1", "expired": False,
        "size_in_bytes": len(archive_raw), "digest": "sha256:"+m.sha(archive_raw),
        "workflow_run": {"id": int(RUN_ID), "head_branch": "main", "head_sha": HEAD},
        "created_at": "2026-09-14T12:17:00Z", "updated_at": "2026-09-14T12:18:00Z"}
    api = m.gh.API_PREFIX
    responses = {api + "/actions/workflows/research_candidate_natural_observer.yml": {
        "id": WORKFLOW_ID, "path": m.WORKFLOW_PATH, "name": m.WORKFLOW_NAME, "state": "active"},
        api + "/actions/runs/"+RUN_ID: run,
        api + "/actions/runs/"+RUN_ID+"/attempts/1/jobs?per_page=100": {"total_count": 2, "jobs": jobs},
        api + "/actions/runs/"+RUN_ID+"/artifacts?per_page=100": {"total_count": 1, "artifacts": [artifact]},
        api + "/git/commits/"+HEAD: {"sha": HEAD, "tree": {"sha": parent_tree["sha"]}, "parents": []},
        api + "/git/commits/"+PARENT: {"sha": PARENT, "tree": {"sha": parent_tree["sha"]}, "parents": [{"sha": HEAD}]},
        api + "/git/commits/"+COMMIT: {"sha": COMMIT, "tree": {"sha": tree["sha"]}, "parents": [{"sha": PARENT}]},
        api + "/git/trees/"+parent_tree["sha"]+"?recursive=1": parent_tree,
        api + "/git/trees/"+tree["sha"]+"?recursive=1": tree,
        api + "/git/ref/heads/main": {"ref": "refs/heads/main", "object": {"type": "commit", "sha": COMMIT}}}
    client = f.FakeGitHub(responses, {98765: archive_raw})
    monkeypatch.setattr(socket, "socket", lambda *a, **kw: pytest.fail("NETWORK"))
    return dict(client=client, run=run, jobs=jobs, artifact=artifact, archive_files=archive_files, prefix=prefix,
        manifest=manifest, captured=captured, context=context, ack=ack, files=files, tree=tree, parent_tree=parent_tree)


def run(case):
    return m.verify_published_evidence(evidence_commit=COMMIT, observer_run_id=RUN_ID, github_client=case["client"])


def rearchive(case):
    raw = f.zip_bytes(case["archive_files"], prefix=case["prefix"])
    case["client"].archives[98765] = raw
    case["artifact"].update(size_in_bytes=len(raw), digest="sha256:"+m.sha(raw))


@pytest.mark.parametrize("prefix", ["", "dc20-candidate-observer-fixture/"])
def test_full_synthetic_cross_day_chain_no_original_pages_no_authority(tmp_path, monkeypatch, prefix):
    case = make_case(tmp_path, monkeypatch, prefix=prefix)
    result = run(case)
    assert type(result) is dict and result["status"] == "SYNTHETIC_OBSERVER_CHECK_ONLY"
    assert result["injected_client_for_test"] is True and result["research_prospective_publication_observed"] is False
    assert result["original_p0_artifact_refetched"] is False and result["bounded_request_cost"] <= 25
    assert result["evidence_manifest_sha256"] == case["context"]["manifest_sha256"]
    assert result["snapshot_file_sha256"] == case["manifest"]["snapshot_file_sha256"]
    assert result["reissuance_requires_unexpired_observer_ack_artifact"] and not result["permanent_offline_attestation_verified"]
    assert [x for x in case["client"].requests if type(x) is tuple] == [("artifact", 98765)]
    assert all(not value for key, value in result.items() if key.endswith("_allowed") or key.endswith("_issued"))


@pytest.mark.parametrize("field,value", [("workflow_id", True), ("path", "other"), ("name", "other"), ("event", "push"),
    ("run_attempt", 2), ("head_branch", "other"), ("status", "in_progress"), ("conclusion", "failure"), ("head_sha", "fake"),
    ("head_repository", {"full_name": "other/repo"})])
def test_wrong_observer_run_blocks_before_archive(tmp_path, monkeypatch, field, value):
    case = make_case(tmp_path, monkeypatch); case["run"][field] = value
    with pytest.raises(ValueError): run(case)
    assert not any(type(x) is tuple for x in case["client"].requests)


@pytest.mark.parametrize("kind", ["failed", "late", "duplicate", "foreign", "validation_order", "artifact_expired", "artifact_digest"])
def test_independent_jobs_artifact_gate(tmp_path, monkeypatch, kind):
    case = make_case(tmp_path, monkeypatch)
    if kind == "failed": case["jobs"][1]["conclusion"] = "failure"
    elif kind == "late":
        case["jobs"][1]["completed_at"] = "2026-09-15T01:25:00Z"; case["run"]["updated_at"] = "2026-09-15T01:26:00Z"
    elif kind == "duplicate": case["jobs"][1]["id"] = case["jobs"][0]["id"]
    elif kind == "foreign": case["jobs"][1]["run_id"] += 1
    elif kind == "validation_order": case["jobs"][0]["completed_at"] = "2026-09-14T12:16:00Z"
    elif kind == "artifact_expired": case["artifact"]["expired"] = True
    elif kind == "artifact_digest": case["artifact"]["digest"] = "sha256:"+"0"*64
    with pytest.raises(ValueError): run(case)


@pytest.mark.parametrize("kind", ["ack_commit", "ack_grant", "context_snapshot", "context_module", "manifest_source", "test_transport", "body", "late_ack", "missing_file"])
def test_original_capsule_crossbindings_never_reconstructed_as_truth(tmp_path, monkeypatch, kind):
    case = make_case(tmp_path, monkeypatch)
    if kind.startswith("ack_") or kind == "late_ack":
        if kind == "ack_commit": case["ack"]["commit_sha"] = "0"*40
        elif kind == "ack_grant": case["ack"]["natural_forward_admission_issued"] = True
        else: case["ack"]["acknowledged_at_host_utc"] = "2026-09-15T01:24:00+00:00"
        case["archive_files"]["publication.json"] = m.gh.json_bytes(case["ack"])
    elif kind.startswith("context_"):
        case["context"]["snapshot_file_sha256" if kind == "context_snapshot" else "coordinator_sha256"] = "0"*64
        case["archive_files"]["context.json"] = m.gh.json_bytes(case["context"])
    elif kind in ("manifest_source", "test_transport"):
        if kind == "manifest_source": case["manifest"]["original_source_bindings"].pop()
        else: case["manifest"]["test_transport_injected"] = True
        raw = m.gh.json_bytes(case["manifest"]); case["archive_files"]["capsule/manifest.json"] = raw
        case["context"]["manifest_sha256"] = m.sha(raw); case["archive_files"]["context.json"] = m.gh.json_bytes(case["context"])
    elif kind == "body": case["archive_files"][next(p for p in case["archive_files"] if p.startswith("capsule/bodies/"))] += b" "
    else: case["archive_files"].pop("capture.json")
    rearchive(case)
    with pytest.raises((ValueError, KeyError)): run(case)


@pytest.mark.parametrize("path", ["future_outcomes_20260915.json", "capsule/future_daily.csv", "../../publication.json", "/publication.json", "second-root/context.json", "capsule/bodies/"+"0"*64+".bin"])
def test_unknown_member_rejected_before_read(tmp_path, monkeypatch, path):
    case = make_case(tmp_path, monkeypatch)
    raw = f.zip_bytes(case["archive_files"], extra=[(path, b"FUTURE_TRAP")])
    original = zipfile.ZipFile.open
    def trap(self, member, *a, **kw):
        assert (member.filename if isinstance(member, zipfile.ZipInfo) else member) != path, "UNKNOWN_BODY_READ"
        return original(self, member, *a, **kw)
    monkeypatch.setattr(zipfile.ZipFile, "open", trap)
    with pytest.raises(ValueError): m.read_observer_archive(raw)


@pytest.mark.parametrize("kind", ["duplicate", "symlink", "fifo", "two_roots", "parentfile", "badjson", "limit"])
def test_archive_safety(tmp_path, monkeypatch, kind):
    case = make_case(tmp_path, monkeypatch); files = deepcopy(case["archive_files"]); extra = []
    if kind == "duplicate": extra = [("publication.json", files["publication.json"])]
    elif kind in ("symlink", "fifo"):
        info = zipfile.ZipInfo("publication.json"); info.external_attr = (stat.S_IFLNK if kind == "symlink" else stat.S_IFIFO) << 16
        extra = [(info, files.pop("publication.json"))]
    elif kind == "two_roots": extra = [("dc20-candidate-observer-other/publication.json", files["publication.json"])]
    elif kind == "parentfile": files["capsule"] = b"{}"
    elif kind == "badjson": files["publication.json"] = b'{"x":1,"x":2}'
    elif kind == "limit": monkeypatch.setattr(m, "MAX_MEMBERS", 1)
    with pytest.raises(ValueError): m.read_observer_archive(f.zip_bytes(files, extra=extra))


@pytest.mark.parametrize("kind", ["source_blob", "code_blob", "parent", "current_main"])
def test_git_bound_bytes_and_no_unrelated_changes(tmp_path, monkeypatch, kind):
    case = make_case(tmp_path, monkeypatch)
    if kind in ("source_blob", "code_blob"):
        path = next(iter(case["files"])) if kind == "source_blob" else m.OBSERVER_PATH
        next(x for x in case["tree"]["tree"] if x["path"] == path)["sha"] = "0"*40
    elif kind == "parent": case["client"].responses[m.gh.API_PREFIX+"/git/commits/"+COMMIT]["parents"] = []
    else: case["client"].responses[m.gh.API_PREFIX+"/git/ref/heads/main"]["object"]["sha"] = PARENT
    with pytest.raises(ValueError): run(case)


def test_ordinary_dict_and_object_new_cannot_issue():
    with pytest.raises(ValueError): m.VerifiedResearchPublication(report={})
    fake = object.__new__(m.VerifiedResearchPublication)
    object.__setattr__(fake, "_raw", b"{}")
    object.__setattr__(fake, "_guard", "0"*64)
    with pytest.raises(ValueError): fake.assert_unchanged()


def test_private_type_seal_and_offline_guard_only(monkeypatch):
    # Test only the private seal implementation, not source admission.
    guard = m.code_guard()
    proof = m.VerifiedResearchPublication(m._KEY, {"signal_date": "20260914", "snapshot_file_sha256": "f"*64}, guard)
    monkeypatch.setattr(m.gh.GitHubReadClient, "get_json", lambda *a, **kw: pytest.fail("PROOF_GUARD_NETWORK"))
    proof.assert_unchanged()
    copied = proof.report; copied["signal_date"] = "20990101"
    assert proof.signal_date == "20260914"
    with pytest.raises(AttributeError): proof.signal_date = "20990101"
    object.__setattr__(proof, "_raw", b"{}")
    with pytest.raises(ValueError): proof.assert_unchanged()


def test_registration_and_final_code_guard(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch)
    monkeypatch.setattr(m, "OBSERVER_SHA", "0"*64)
    with pytest.raises(ValueError, match="NOT_ACTIVE"): run(case)
    assert not case["client"].requests


def test_private_guard_cannot_be_rebased_or_subclassed(monkeypatch):
    original = m.code_guard()
    proof = m.VerifiedResearchPublication(m._KEY, {"signal_date": "20260914"}, original)
    changed = (original[0], ("DIFFERENT_FILE_IDENTITY",))
    monkeypatch.setattr(m, "code_guard", lambda: changed)
    object.__setattr__(proof, "_guard", m.sha(repr(changed).encode()))
    with pytest.raises(ValueError, match="UNISSUED_OR_MUTATED"): proof.assert_unchanged()
    class Subclass(m.VerifiedResearchPublication): pass
    with pytest.raises(ValueError, match="PRIVATE"): Subclass(m._KEY, {}, original)


def test_source_body_mutated_during_final_api_boundary(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch)
    original = case["client"].get_json; calls = 0
    run_path = m.gh.API_PREFIX+"/actions/runs/"+RUN_ID
    def mutate(path):
        nonlocal calls
        if path == run_path:
            calls += 1
            if calls == 2: case["run"]["conclusion"] = "failure"
        return original(path)
    case["client"].get_json = mutate
    with pytest.raises(ValueError, match="EVIDENCE_CHANGED"): run(case)


def test_code_identity_mutated_during_final_check(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch); original = m.code_guard; calls = 0
    def changed():
        nonlocal calls
        calls += 1
        result = original()
        return result if calls == 1 else (result[0], ("DIFFERENT_IDENTITY",))
    monkeypatch.setattr(m, "code_guard", changed)
    with pytest.raises(ValueError, match="DURING_VERIFY"): run(case)


def test_unknown_response_secret_rejected_without_copy(tmp_path, monkeypatch):
    case = make_case(tmp_path, monkeypatch)
    case["context"]["unexpected"] = "https://archive.actions.githubusercontent.com/p?sig=opaque"
    case["archive_files"]["context.json"] = m.gh.json_bytes(case["context"]); rearchive(case)
    with pytest.raises(ValueError, match="SIGNED_OR_CDN"): run(case)


@pytest.mark.parametrize("field,value", [("observer_run_id", True), ("run_attempt", 1.0),
    ("evidence_natural_admission_issued", True), ("created_at_host_utc", "2026-09-14T12:15:00")])
def test_exact_context_fields_and_aware_clock(tmp_path, monkeypatch, field, value):
    case = make_case(tmp_path, monkeypatch); case["context"][field] = value
    case["archive_files"]["context.json"] = m.gh.json_bytes(case["context"]); rearchive(case)
    with pytest.raises(ValueError): run(case)
