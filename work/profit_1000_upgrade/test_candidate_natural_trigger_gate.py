"""Duplicate trigger regressions on synthetic immutable Git/API observations."""
import base64
from copy import deepcopy
import socket

import pytest

from work.profit_1000_upgrade import candidate_natural_trigger_gate as gate
from work.profit_1000_upgrade import test_candidate_natural_publication as fixtures
from work.profit_1000_upgrade import test_candidate_natural_evidence as capture_tests
from work.profit_1000_upgrade import candidate_natural_evidence_publication as issuer

gh = fixtures.m.gh
NOOP_ID = "43212345679"
OBSERVER_ID = "88812345678"


def complete_case(tmp_path, monkeypatch):
    case = fixtures.make_case(tmp_path, monkeypatch)
    case.update(opener=capture_tests.Opener(case), destination=tmp_path.resolve() / "capsule")
    _, _, manifest, bodies = capture_tests.materials(case)
    manifest["test_transport_injected"] = False  # Synthetic API fixture only.
    raw_manifest = gh.json_bytes(manifest)
    context = {"observer_workflow_path": issuer.WORKFLOW_PATH, "observer_run_id": int(OBSERVER_ID),
        "run_attempt": 1, "code_head_sha": "9" * 40, "repository": gh.REPOSITORY, "branch": "main",
        "schema_version": "dc20_candidate_natural_observer_context_v1", "signal_date": manifest["signal_date"],
        "freeze_run_id": int(fixtures.RUN_ID), "snapshot_file_sha256": manifest["snapshot_file_sha256"],
        "manifest_sha256": gh.sha256(raw_manifest), "publication_observation_sha256": manifest["native_observation"]["sha256"],
        "capture_module_sha256": issuer.CAPTURE_SHA, "coordinator_sha256": issuer.OBSERVER_SHA,
        "writer_sha256": issuer.WRITER_SHA, "created_at_host_utc": "2026-09-14T12:15:00+00:00",
        "original_prospective_publication_observed": True, "evidence_natural_admission_issued": False,
        "production_activation_allowed": False, "actual_execution_claimed": False}
    prefix = issuer.PREFIX + manifest["signal_date"] + "/"
    capsule = {prefix + "context.json": gh.json_bytes(context), prefix + "manifest.json": raw_manifest,
        **{prefix + name: raw for name, raw in bodies.items()}}
    source = {**case["source"]["sources"], **case["files"], **capsule}
    tree = fixtures.p0.git_tree(source)
    client = case["client"]
    client.responses[gh.API_PREFIX + "/git/ref/heads/main"]["object"]["sha"] = "6" * 40
    client.responses[gh.API_PREFIX + "/git/commits/" + "6" * 40] = {"sha": "6" * 40, "tree": {"sha": tree["sha"]}}
    client.responses[gh.API_PREFIX + "/git/trees/" + tree["sha"] + "?recursive=1"] = tree
    for name, raw in capsule.items():
        blob = gh.git_blob(raw)
        client.responses[gh.API_PREFIX + "/git/blobs/" + blob] = {"sha": blob, "size": len(raw),
            "encoding": "base64", "content": base64.encodebytes(raw).decode()}
    run = {**deepcopy(case["run"]), "id": int(OBSERVER_ID), "workflow_id": 357027830,
        "path": issuer.WORKFLOW_PATH, "name": issuer.WORKFLOW_NAME, "head_sha": "9" * 40,
        "created_at": "2026-09-14T12:11:00Z", "run_started_at": "2026-09-14T12:12:00Z", "updated_at": "2026-09-14T12:21:00Z"}
    jobs = [{"id": 501 + i, "run_id": int(OBSERVER_ID), "name": name, "status": "completed", "conclusion": "success",
        "started_at": "2026-09-14T12:12:00Z" if i == 0 else "2026-09-14T12:14:00Z",
        "completed_at": "2026-09-14T12:13:00Z" if i == 0 else "2026-09-14T12:20:00Z"} for i, name in enumerate(issuer.JOBS)]
    client.responses[gh.API_PREFIX + "/actions/runs/" + OBSERVER_ID] = run
    client.responses[gh.API_PREFIX + "/actions/runs/" + OBSERVER_ID + "/attempts/1/jobs?per_page=100"] = {"jobs": jobs, "total_count": 2}
    client.responses[gh.API_PREFIX + "/actions/workflows/research_candidate_natural_observer.yml"] = {
        "id": 357027830, "path": issuer.WORKFLOW_PATH, "name": issuer.WORKFLOW_NAME, "state": "active"}
    ack = {"schema_version": "dc20_candidate_natural_evidence_git_ack_v1", "status": "EVIDENCE_GIT_ACKNOWLEDGED",
        "signal_date": manifest["signal_date"], "commit_sha": "6" * 40, "existing_files_modified": False,
        "acknowledged_at_host_utc": "2026-09-14T12:16:00+00:00", "files": [
            {"path": name, "sha256": gh.sha256(raw), "git_blob_sha1": gh.git_blob(raw), "bytes": len(raw)}
            for name, raw in sorted(capsule.items())]}
    archive = fixtures.zip_bytes({"publication.json": gh.json_bytes(ack), "context.json": gh.json_bytes(context),
        "capture.json": gh.json_bytes(issuer.capture._result(case["destination"], manifest, raw_manifest)),
        "capsule/manifest.json": raw_manifest, **{"capsule/" + name: body for name, body in bodies.items()}})
    artifact = {"id": 9911, "name": f"dc20-candidate-observer-{OBSERVER_ID}-1", "expired": False,
        "size_in_bytes": len(archive), "digest": "sha256:" + gh.sha256(archive),
        "workflow_run": {"id": int(OBSERVER_ID), "head_branch": "main", "head_sha": run["head_sha"]},
        "created_at": "2026-09-14T12:17:00Z", "updated_at": "2026-09-14T12:18:00Z"}
    client.responses[gh.API_PREFIX + "/actions/runs/" + OBSERVER_ID + "/artifacts?per_page=100"] = {"artifacts": [artifact], "total_count": 1}
    client.archives[9911] = archive
    case.update(capsule_files=capsule, observer_run=run, observer_jobs=jobs, evidence_tree=tree,
        evidence_sources=source, evidence_native_path=prefix + manifest["native_observation"]["path"])
    return case


def noop_case(tmp_path, monkeypatch):
    case = complete_case(tmp_path, monkeypatch)
    client = case["client"]
    original = case["source"]
    run = deepcopy(original["run"])
    run.update(id=int(NOOP_ID), head_sha=fixtures.PUBLISHED)
    jobs = deepcopy(original["jobs"])
    for job in jobs:
        job["run_id"] = int(NOOP_ID)
    jobs[1]["conclusion"] = "skipped"
    jobs[0]["steps"] = [{"number": i + 1, "name": name, "status": "completed", "conclusion": state}
        for i, (name, state) in enumerate([
            ("Resolve exact D and reject a completed duplicate", "success"),
            *((name, "skipped") for name in gate.SKIPPED_STEPS)])]
    revision = deepcopy(original["revision"])
    revision.update(run_id=int(NOOP_ID), head_sha=fixtures.PUBLISHED)
    archive = fixtures.p0.pages_zip(revision)
    artifact = deepcopy(original["artifact"])
    artifact.update(id=12346, size_in_bytes=len(archive), digest="sha256:" + gh.sha256(archive))
    artifact["workflow_run"].update(id=int(NOOP_ID), head_sha=run["head_sha"])
    path = gh.API_PREFIX + "/actions/runs/" + NOOP_ID
    client.responses.update({path: run, path + "/attempts/1/jobs?per_page=100": {"jobs": jobs, "total_count": 3},
        path + "/artifacts?per_page=100": {"artifacts": [artifact], "total_count": 1}})
    client.archives[12346] = archive
    case.update(noop_run=run, noop_jobs=jobs, noop_artifact=artifact, noop_path=path)
    return case


def test_successful_p0_redeploy_preserves_frozen_slots_without_source_reimport(tmp_path, monkeypatch):
    case = noop_case(tmp_path, monkeypatch)
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("NETWORK"))
    monkeypatch.setattr(gh, "import_p0_sources", lambda *a, **k: pytest.fail("REIMPORT"))
    monkeypatch.setattr(fixtures.m.natural.scorer, "predict_forward", lambda *a, **k: pytest.fail("RESCORE"))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    request_start = len(case["client"].requests)
    out = gate.inspect_p0(run_id=NOOP_ID, github_client=case["client"])
    assert out["status"] == "EXISTING_CANDIDATE_VERIFIED_NO_NEW_FREEZE"
    assert out["needs_work"] is False and out["p0_new_generation"] is False
    assert out["existing"]["original_freeze_run_id"] == int(fixtures.RUN_ID)
    assert out["existing"]["original_p0_run_id"] == int(fixtures.p0.RUN_ID)
    assert len(out["existing"]["existing_four_file_bindings"]) == 4
    assert out["model_predictions_computed"] is out["existing_files_modified"] is False
    assert sum(2 if type(item) is tuple else 1 for item in case["client"].requests[request_start:]) <= gh.MAX_API_CALLS
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(gh.ImportBlocked, match="COMPUTE_CAS_DEPLOY_ALL_SUCCESS"):
        gh.validate_jobs(case["client"].responses[case["noop_path"] + "/attempts/1/jobs?per_page=100"],
            case["noop_run"], NOOP_ID)


def test_same_original_successful_run_retrigger_is_idempotent(tmp_path, monkeypatch):
    case = complete_case(tmp_path, monkeypatch)
    out = gate.inspect_p0(run_id=fixtures.p0.RUN_ID, github_client=case["client"])
    assert not out["needs_work"] and out["p0_new_generation"]


def test_new_successful_p0_still_requires_original_strict_import(tmp_path, monkeypatch):
    case = fixtures.make_case(tmp_path, monkeypatch)
    case["client"].responses[gh.API_PREFIX + "/git/ref/heads/main"]["object"]["sha"] = fixtures.p0.PUBLISHED
    out = gate.inspect_p0(run_id=fixtures.p0.RUN_ID, github_client=case["client"])
    assert out["needs_work"] and out["status"] == "NEW_P0_REQUIRES_ORIGINAL_IMPORT"
    assert out["existing"] is None and not out["natural_forward_admission_issued"]


@pytest.mark.parametrize("fault", ["missing", "partial", "cas_failure", "compute_skipped", "deploy_failure",
    "generation_ran", "duplicate_check_missing", "duplicate_step", "incomplete_jobs", "foreign_job", "original_cas_changed",
    "missing_observer_capsule", "observer_failed", "observer_in_progress"])
def test_incomplete_failed_or_unbound_duplicate_never_turns_green(tmp_path, monkeypatch, fault):
    case = noop_case(tmp_path, monkeypatch)
    client, jobs = case["client"], case["noop_jobs"]
    if fault == "missing":
        client.responses[gh.API_PREFIX + "/git/ref/heads/main"]["object"]["sha"] = fixtures.p0.PUBLISHED
    elif fault == "partial":
        files = {next(iter(case["files"])): next(iter(case["files"].values()))}
        tree = fixtures.p0.git_tree(files)
        client.responses[gh.API_PREFIX + "/git/commits/" + "a" * 40] = {"sha": "a" * 40, "tree": {"sha": tree["sha"]}}
        client.responses[gh.API_PREFIX + "/git/trees/" + tree["sha"] + "?recursive=1"] = tree
        client.responses[gh.API_PREFIX + "/git/ref/heads/main"]["object"]["sha"] = "a" * 40
    elif fault == "cas_failure": jobs[1]["conclusion"] = "failure"
    elif fault == "compute_skipped": jobs[0]["conclusion"] = "skipped"
    elif fault == "deploy_failure": jobs[2]["conclusion"] = "failure"
    elif fault == "generation_ran": jobs[0]["steps"][-1]["conclusion"] = "success"
    elif fault == "duplicate_check_missing": jobs[0]["steps"].pop(0)
    elif fault == "duplicate_step": jobs[0]["steps"].append(deepcopy(jobs[0]["steps"][0]))
    elif fault == "incomplete_jobs": client.responses[case["noop_path"] + "/attempts/1/jobs?per_page=100"]["total_count"] += 1
    elif fault == "foreign_job": jobs[1]["run_id"] += 1
    elif fault == "original_cas_changed":
        client.responses[case["source"]["api_base"] + "/attempts/1/jobs?per_page=100"]["jobs"][1]["conclusion"] = "skipped"
    elif fault == "missing_observer_capsule":
        client.responses[gh.API_PREFIX + "/git/ref/heads/main"]["object"]["sha"] = fixtures.PUBLISHED
    elif fault == "observer_failed": case["observer_jobs"][1]["conclusion"] = "failure"
    else: case["observer_run"]["status"] = "in_progress"
    with pytest.raises(ValueError): gate.inspect_p0(run_id=NOOP_ID, github_client=client)


def test_same_length_non_native_capsule_damage_is_not_a_successful_noop(tmp_path, monkeypatch):
    case = noop_case(tmp_path, monkeypatch)
    sources = dict(case["evidence_sources"])
    name = next(name for name in case["capsule_files"] if "/bodies/" in name and name != case["evidence_native_path"])
    original = sources[name]
    sources[name] = bytes([original[0] ^ 1]) + original[1:]
    assert len(sources[name]) == len(original)
    tree = fixtures.p0.git_tree(sources)
    client = case["client"]
    client.responses[gh.API_PREFIX + "/git/commits/" + "6" * 40]["tree"]["sha"] = tree["sha"]
    client.responses[gh.API_PREFIX + "/git/trees/" + tree["sha"] + "?recursive=1"] = tree
    with pytest.raises(ValueError, match="EXISTING_OBSERVER_ACK_FILE_CHANGED"):
        gate.inspect_p0(run_id=NOOP_ID, github_client=client)


def observer_noop_case(tmp_path, monkeypatch):
    case = noop_case(tmp_path, monkeypatch)
    client = case["client"]
    receipt = gate.inspect_p0(run_id=NOOP_ID, github_client=client)
    run = case["run"]
    run["head_sha"] = "a" * 40
    case["jobs"][1]["conclusion"] = "skipped"
    case["jobs"][0]["steps"] = [{"number": 1, "name": gate.PREFLIGHT_STEP, "status": "completed", "conclusion": "success"}]
    code = {p: (gate.workflow.ROOT / p).read_bytes() for p in (gate.MODULE_PATH, gate.FORWARD_PATH)}
    tree = fixtures.p0.git_tree(code)
    client.responses[gh.API_PREFIX + "/git/commits/" + "a" * 40] = {"sha": "a" * 40, "tree": {"sha": tree["sha"]}}
    client.responses[gh.API_PREFIX + "/git/trees/" + tree["sha"] + "?recursive=1"] = tree
    archive = fixtures.zip_bytes({"candidate-preflight.json": gh.json_bytes(receipt)})
    artifact = deepcopy(case["artifact"])
    artifact.update(id=991, name=f"dc20-candidate-preflight-{fixtures.RUN_ID}-1", size_in_bytes=len(archive),
        digest="sha256:" + gh.sha256(archive))
    artifact["workflow_run"]["head_sha"] = run["head_sha"]
    client.responses[gh.API_PREFIX + fixtures.API_RUN + "/artifacts?per_page=100"] = {"artifacts": [artifact], "total_count": 1}
    client.archives[991] = archive
    return case


def test_noop_freeze_skips_observer_only_after_rechecking_original_frozen_day(tmp_path, monkeypatch):
    case = observer_noop_case(tmp_path, monkeypatch)
    request_start = len(case["client"].requests)
    out = gate.inspect_freeze(run_id=fixtures.RUN_ID, github_client=case["client"])
    assert out["status"] == "NO_NEW_FREEZE_EXISTING_EVIDENCE_PRESERVED" and not out["needs_work"]
    assert not out["new_prediction_freeze_verified"] and out["files_published"] == 0
    assert sum(2 if type(item) is tuple else 1 for item in case["client"].requests[request_start:]) <= gh.MAX_API_CALLS


@pytest.mark.parametrize("fault", ["missing_gate", "gate_failed", "code_changed", "bad_digest", "missing_day"])
def test_observer_does_not_skip_on_an_unverified_green_status(tmp_path, monkeypatch, fault):
    case = observer_noop_case(tmp_path, monkeypatch)
    client = case["client"]
    if fault == "missing_gate": case["jobs"][0]["steps"] = []
    elif fault == "gate_failed": case["jobs"][0]["steps"][0]["conclusion"] = "failure"
    elif fault == "code_changed": case["run"]["head_sha"] = fixtures.CODE_HEAD
    elif fault == "bad_digest": client.archives[991] += b"changed"
    else: client.responses[gh.API_PREFIX + "/git/ref/heads/main"]["object"]["sha"] = fixtures.p0.PUBLISHED
    with pytest.raises(ValueError): gate.inspect_freeze(run_id=fixtures.RUN_ID, github_client=client)


def test_real_new_freeze_keeps_original_observer_flow(tmp_path, monkeypatch):
    case = fixtures.make_case(tmp_path, monkeypatch)
    out = gate.inspect_freeze(run_id=fixtures.RUN_ID, github_client=case["client"])
    assert out["needs_work"] and out["status"] == "FREEZE_REQUIRES_ORIGINAL_OBSERVER"


def test_workflows_keep_mutating_jobs_behind_read_only_gate():
    import yaml
    for name, job, output in (("forward", "freeze", "needs_freeze"), ("observer", "observe", "needs_observer")):
        path = gate.workflow.ROOT / f".github/workflows/research_candidate_natural_{name}.yml"
        doc = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        assert doc["jobs"]["validate"]["permissions"] == {"actions": "read", "contents": "read"}
        assert f"needs.validate.outputs.{output} == 'true'" in doc["jobs"][job]["if"]
        steps = doc["jobs"]["validate"]["steps"]
        gate_step = next(step for step in steps if step.get("id") == "trigger")
        assert "candidate_natural_trigger_gate" in gate_step["run"]
        assert "DC20_CANDIDATE_GITHUB_TOKEN" in gate_step["env"]
