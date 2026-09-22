"""GET-only duplicate trigger classification; never imports, scores or publishes.

A green P0 redeployment is not a new source publication. Skip it only after
checking the existing four candidate files against that D's original sources.
The original fresh-source importer and publication admission stay unchanged.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import stat
import zipfile

from work.profit_1000_upgrade import candidate_natural_workflow as workflow

SCHEMA = "dc20_candidate_natural_trigger_gate_20260915_v1"
MODULE_PATH = "work/profit_1000_upgrade/candidate_natural_trigger_gate.py"
FORWARD_PATH = ".github/workflows/research_candidate_natural_forward.yml"
FORWARD_NAME = "DC20 · Fixed candidate natural forward (research)"
FORWARD_ID = 357010624
FORWARD_JOBS = ("Validate fixed natural candidate contracts", "Freeze original P0 bound new model research slots")
PREFLIGHT_STEP = "Check whether original P0 needs a new candidate freeze"
SKIPPED_STEPS = (
    "Resolve immutable upstream commits",
    "Snapshot protected secondary state",
    "Sync exact-D independent inputs",
    "Publish primary promotion list in the isolated scope",
    "Build exact P0 candidate patch",
    "Upload immutable P0 candidate",
)
FLAGS = {"new_prediction_freeze_verified": False, "natural_forward_admission_issued": False,
    "production_activation_allowed": False, "model_predictions_computed": False,
    "files_published": 0, "existing_files_modified": False}
PUBLIC_DIAGNOSTICS = frozenset({"GITHUB_GET_FAILED", "GET_BUDGET_EXCEEDED",
    "PARTIAL_EXISTING_CANDIDATE_FREEZE_REQUIRES_REPAIR",
    "P0_ALREADY_EXISTS_BUT_CANDIDATE_FREEZE_MISSING_REQUIRES_REPAIR",
    "EXISTING_FREEZE_OBSERVER_EVIDENCE_MISSING_REQUIRES_REPAIR",
    "EXISTING_OBSERVER_BODY_MISSING_REQUIRES_REPAIR", "EXISTING_OBSERVER_MANIFEST_BINDING_CHANGED",
    "EXISTING_OBSERVER_PUBLICATION_NOT_VERIFIED", "EXISTING_OBSERVER_WAS_NOT_TIMELY",
    "EXISTING_FREEZE_IS_NOT_THIS_P0_D", "ORIGINAL_GENERATING_P0_RUN_CHANGED",
    "P0_SKIPPED_CAS_WITHOUT_VERIFIED_DUPLICATE", "NOOP_FREEZE_CODE_VERSION_CHANGED",
    "SUCCESSFUL_FIRST_MAIN_OBSERVER_REQUIRED", "OBSERVER_JOB_NOT_SUCCESSFUL"})


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def _jobs(document, run_id):
    jobs = document.get("jobs")
    require(type(jobs) is list and type(document.get("total_count")) is int
        and document["total_count"] == len(jobs) <= 100, "COMPLETE_TRIGGER_JOB_LIST_REQUIRED")
    found, ids = {}, set()
    for job in jobs:
        require(type(job) is dict and type(job.get("id")) is int and job["id"] > 0
            and job["id"] not in ids and type(job.get("run_id")) is int and job["run_id"] == int(run_id)
            and type(job.get("name")) is str and job["name"] not in found,
            "TRIGGER_JOB_IDENTITY_CHANGED")
        ids.add(job["id"]); found[job["name"]] = job
    return found


def _step_states(job):
    require(type(job.get("steps")) is list, "COMPLETE_TRIGGER_STEPS_REQUIRED")
    states, numbers = {}, set()
    for step in job["steps"]:
        require(type(step) is dict and type(step.get("number")) is int and step["number"] > 0
            and step["number"] not in numbers and type(step.get("name")) is str
            and step["name"] not in states and step.get("status") == "completed",
            "TRIGGER_STEP_IDENTITY_CHANGED")
        numbers.add(step["number"]); states[step["name"]] = step.get("conclusion")
    return states


def _p0_new_generation(gh, document, run, run_id):
    found = _jobs(document, run_id)
    require(set(gh.CRITICAL_JOBS) <= set(found), "CRITICAL_P0_TRIGGER_JOB_MISSING")
    compute, cas, deploy = [found[name] for name in gh.CRITICAL_JOBS]
    if cas.get("conclusion") != "skipped":
        gh.validate_jobs(document, run, run_id)
        return True
    require(all(j.get("status") == "completed" for j in (compute, cas, deploy))
        and compute.get("conclusion") == deploy.get("conclusion") == "success",
        "P0_REDEPLOY_REQUIRES_SUCCESSFUL_COMPUTE_AND_DEPLOY")
    states = _step_states(compute)
    require(states.get("Resolve exact D and reject a completed duplicate") == "success"
        and all(states.get(name) == "skipped" for name in SKIPPED_STEPS),
        "P0_SKIPPED_CAS_WITHOUT_VERIFIED_DUPLICATE")
    require(gh.utc(run["run_started_at"]) <= gh.utc(compute.get("started_at"))
        <= gh.utc(compute.get("completed_at")) <= gh.utc(deploy.get("started_at"))
        <= gh.utc(deploy.get("completed_at")) <= gh.utc(run["updated_at"]),
        "P0_REDEPLOY_TIME_ORDER_INVALID")
    return False


def _tree(gh, client, commit_sha):
    commit_sha = gh.exact_sha(commit_sha, 40)
    commit = client.get_json(gh.API_PREFIX + "/git/commits/" + commit_sha)
    require(commit.get("sha") == commit_sha and type(commit.get("tree")) is dict,
        "EXACT_TRIGGER_COMMIT_REQUIRED")
    tree_sha = gh.exact_sha(commit["tree"].get("sha"), 40)
    tree = gh.validate_tree(client.get_json(gh.API_PREFIX + "/git/trees/" + tree_sha + "?recursive=1"), tree_sha)
    return commit, tree


def _existing_day(gh, runner, client, tree, published_tree, day, trade, exit_day, revision):
    names = [workflow.PREFIX + f"{kind}_{day}.json" for kind in ("day", "p0_sources", "local_freeze", "workflow")]
    present = [name in tree for name in names]
    if not any(present):
        return None
    require(all(present), "PARTIAL_EXISTING_CANDIDATE_FREEZE_REQUIRES_REPAIR")
    raw = [gh.load_blob(client, tree, name) for name in names]
    snapshot, imported, local, context = map(gh.parse_json, raw)
    require([snapshot.get(k) for k in ("signal_date", "exec_date", "exit_date")] == [day, trade, exit_day],
        "EXISTING_CANDIDATE_DATES_CHANGED")
    require(context.get("repository") == gh.REPOSITORY and context.get("workflow_path") == FORWARD_PATH
        and context.get("signal_date") == day and context.get("snapshot_file_sha256") == gh.sha256(raw[0])
        and context.get("source_import_receipt_sha256") == gh.sha256(raw[1])
        and context.get("local_freeze_receipt_sha256") == gh.sha256(raw[2]), "EXISTING_FREEZE_BINDINGS_CHANGED")
    from work.profit_1000_upgrade import candidate_snapshot_versions as profiles
    profiles.validate_snapshot(raw[0], gh.sha256(raw[0]))
    eligible = snapshot["schema_version"] == profiles.eligible.SCHEMA
    coordinator = workflow
    if not eligible:
        from work.profit_1000_upgrade import candidate_legacy_workflow as legacy
        require(Path(legacy.__file__).absolute() == workflow.ROOT / "work/profit_1000_upgrade/candidate_legacy_workflow.py"
            and gh.sha256(gh.read(Path(legacy.__file__).absolute())[0]) == "0623a000570a7994b4f3687f778eda66499e1026d8b606c4376f31b44031f0ce",
            "LEGACY_COORDINATOR_CODE_CHANGED")
        coordinator = legacy
    prepared, _ = coordinator.prepare_publication(raw[0], local, imported, context,
        now=workflow.aware(local["local_operation_completed_at_utc"]))
    require(prepared == dict(zip(names, raw)), "EXISTING_FOUR_FILE_BYTES_CHANGED")
    rows = snapshot["prediction"]["rows"]
    promotions = ([{**r, "candidate_rank": None, "candidate_score": None}
        for r in snapshot["prediction"]["promotion_rows"]] if eligible else rows)
    require(snapshot["candidate_slots"] == runner._slots(rows, "candidate_rank")
        and snapshot["promotion_slots"] == runner._slots(promotions, "promotion_rank"), "EXISTING_TOP_SLOTS_CHANGED")
    paths = gh.adapter._p0_paths(day)
    p0 = {role: gh.load_blob(client, published_tree, name) for role, name in paths.items()}
    require({role: gh.sha256(body) for role, body in p0.items()} == imported["expected_p0_sha256"],
        "EXISTING_FREEZE_IS_NOT_THIS_P0_D")
    contract = gh.parse_json(p0["three_rank_json"])
    require(contract.get("bundle_sha256") == revision["primary_d_bundle_sha256"]
        and contract.get("top10_count") == revision["primary_d_top10_count"] == len(promotions),
        "EXISTING_P0_REVISION_CHANGED")
    original_commit, original_tree = _tree(gh, client, imported["published_git_sha"])
    require(original_commit["tree"]["sha"] == imported["published_tree_sha"]
        and [p.get("sha") for p in original_commit.get("parents", [])] == [imported["published_parent_sha"]],
        "ORIGINAL_P0_COMMIT_BINDING_CHANGED")
    for binding in imported["source_file_bindings"]:
        entry = original_tree.get(binding["path"], {})
        require(entry.get("type") == "blob" and entry.get("mode") == "100644"
            and entry.get("sha") == binding["git_blob_sha1"] and entry.get("size") == binding["bytes"],
            "ORIGINAL_28_SOURCE_BLOB_IDENTITY_CHANGED")
    original_id = gh.exact_id(str(imported["run"]["id"]))
    original_path = gh.API_PREFIX + "/actions/runs/" + original_id
    original_run = client.get_json(original_path)
    require(gh.validate_run(original_run, original_id) == imported["run"]
        and gh.validate_jobs(client.get_json(original_path + "/attempts/1/jobs?per_page=100"), original_run, original_id)
            == imported["critical_jobs"], "ORIGINAL_GENERATING_P0_RUN_CHANGED")
    observer_id = _existing_evidence(gh, client, tree, day, snapshot, context)
    return {"snapshot_file_sha256": gh.sha256(raw[0]), "original_freeze_run_id": context["run_id"],
        "original_observer_run_id": observer_id,
        "original_p0_run_id": int(original_id), "existing_four_file_bindings": [
            {"path": name, "sha256": gh.sha256(body), "git_blob_sha1": gh.git_blob(body)} for name, body in zip(names, raw)]}


def _existing_evidence(gh, client, tree, day, snapshot, freeze_context):
    """Do not hide a missing observer behind a valid prediction-only freeze."""
    from work.profit_1000_upgrade import candidate_natural_evidence_publication as issuer
    prefix = issuer.PREFIX + day + "/"
    require(prefix + "context.json" in tree and prefix + "manifest.json" in tree,
        "EXISTING_FREEZE_OBSERVER_EVIDENCE_MISSING_REQUIRES_REPAIR")
    context = gh.parse_json(gh.load_blob(client, tree, prefix + "context.json"))
    manifest_raw = gh.load_blob(client, tree, prefix + "manifest.json")
    manifest = gh.parse_json(manifest_raw)
    require(gh.sha256(manifest_raw) == context.get("manifest_sha256")
        and manifest.get("schema_version") == issuer.capture.SCHEMA
        and manifest.get("test_transport_injected") is False
        and manifest.get("signal_date") == day and manifest.get("freeze_run_id") == str(freeze_context["run_id"])
        and manifest.get("snapshot_file_sha256") == freeze_context["snapshot_file_sha256"]
        and (manifest.get("capture_code_sha256"), manifest.get("publication_verifier_sha256"))
            in issuer.capture.capture_contracts(),
        "EXISTING_OBSERVER_MANIFEST_BINDING_CHANGED")
    files = manifest.get("files")
    require(type(files) is list and 0 < len(files) < issuer.capture.MAX_FILES, "EXISTING_OBSERVER_INVENTORY_INVALID")
    seen = set()
    for binding in files:
        require(type(binding) is dict and binding.get("path") == "bodies/" + gh.exact_sha(binding.get("sha256")) + ".bin"
            and binding["path"] not in seen, "EXISTING_OBSERVER_BODY_IDENTITY_CHANGED")
        seen.add(binding["path"])
        entry = tree.get(prefix + binding["path"], {})
        require(entry.get("type") == "blob" and entry.get("mode") == "100644"
            and type(binding.get("bytes")) is int and binding["bytes"] == entry.get("size"),
            "EXISTING_OBSERVER_BODY_MISSING_REQUIRES_REPAIR")
    native = manifest["native_observation"]
    require(native in files, "EXISTING_OBSERVER_NATIVE_BINDING_CHANGED")
    observation_raw = gh.load_blob(client, tree, prefix + native["path"])
    require(gh.sha256(observation_raw) == native["sha256"], "EXISTING_OBSERVER_NATIVE_BYTES_CHANGED")
    observation = gh.parse_json(observation_raw)
    require(observation.get("research_prospective_publication_observed") is True
        and observation.get("injected_client_for_test") is False
        and observation.get("freeze_run_id") == freeze_context["run_id"]
        and observation.get("snapshot_file_sha256") == freeze_context["snapshot_file_sha256"]
        and observation.get("verifier_sha256") == manifest["publication_verifier_sha256"],
        "EXISTING_OBSERVER_PUBLICATION_NOT_VERIFIED")
    observer_id = gh.exact_id(str(context["observer_run_id"]))
    path = gh.API_PREFIX + "/actions/runs/" + observer_id
    run = client.get_json(path)
    registration = client.get_json(gh.API_PREFIX + "/actions/workflows/research_candidate_natural_observer.yml")
    issuer._run(run, registration, observer_id)
    jobs = issuer._jobs(client.get_json(path + "/attempts/1/jobs?per_page=100"), run)
    issuer._context(context, run, manifest, observation)
    artifact = issuer._artifact(client.get_json(path + "/artifacts?per_page=100"), run)
    archive = client.get_artifact_zip(artifact["id"])
    require(type(archive) is bytes and len(archive) == artifact["size_in_bytes"]
        and gh.sha256(archive) == artifact["digest"][7:], "EXISTING_OBSERVER_ACK_ARCHIVE_CHANGED")
    originals = issuer.read_observer_archive(archive)
    original_bodies = {name.removeprefix("capsule/"): body for name, body in originals.items()
        if name.startswith("capsule/bodies/")}
    require(originals["capsule/manifest.json"] == manifest_raw
        and gh.parse_json(originals["context.json"]) == context
        and issuer.capture.verify_materials(manifest_raw, original_bodies,
            expected_manifest_sha256=context["manifest_sha256"]) == manifest,
        "EXISTING_OBSERVER_CAPSULE_DIFFERS_FROM_ORIGINAL_ACK")
    original_files = {prefix + "manifest.json": manifest_raw, prefix + "context.json": originals["context.json"],
        **{prefix + name: body for name, body in original_bodies.items()}}
    ack = gh.parse_json(originals["publication.json"])
    expected_files = [{"path": name, "sha256": gh.sha256(body), "git_blob_sha1": gh.git_blob(body), "bytes": len(body)}
        for name, body in sorted(original_files.items())]
    require(ack.get("schema_version") == "dc20_candidate_natural_evidence_git_ack_v1"
        and ack.get("status") == "EVIDENCE_GIT_ACKNOWLEDGED" and ack.get("signal_date") == day
        and ack.get("files") == expected_files and ack.get("existing_files_modified") is False,
        "EXISTING_OBSERVER_ORIGINAL_ACK_BINDINGS_CHANGED")
    for binding in expected_files:
        entry = tree.get(binding["path"], {})
        require(entry.get("type") == "blob" and entry.get("mode") == "100644"
            and entry.get("size") == binding["bytes"] and entry.get("sha") == binding["git_blob_sha1"],
            "EXISTING_OBSERVER_ACK_FILE_CHANGED")
    _, cutoff = workflow.dependencies()[0]._window(day, snapshot["exec_date"])
    require(gh.utc(observation["latest_publication_bound_utc"]) <= gh.utc(jobs[1]["started_at"])
        <= workflow.aware(context["created_at_host_utc"]) <= workflow.aware(ack["acknowledged_at_host_utc"])
        <= gh.utc(jobs[1]["completed_at"]) < cutoff
        and (cutoff - workflow.aware(ack["acknowledged_at_host_utc"])).total_seconds() > 300,
        "EXISTING_OBSERVER_WAS_NOT_TIMELY")
    return int(observer_id)


def inspect_p0(*, run_id, github_client):
    runner, gh = workflow.dependencies()
    run_id = gh.exact_id(run_id)
    path = gh.API_PREFIX + "/actions/runs/" + run_id
    run = github_client.get_json(path)
    bound_run = gh.validate_run(run, run_id)
    jobs = github_client.get_json(path + "/attempts/1/jobs?per_page=100")
    generated = _p0_new_generation(gh, jobs, run, run_id)
    artifact = gh.select_artifact(github_client.get_json(path + "/artifacts?per_page=100"), run, run_id)
    archive = github_client.get_artifact_zip(artifact["id"])
    require(type(archive) is bytes and len(archive) == artifact["size_in_bytes"]
        and gh.sha256(archive) == artifact["digest"][7:], "P0_TRIGGER_ARTIFACT_DIGEST_CHANGED")
    revision, revision_sha = gh.revision_from_zip(archive)
    day, trade, exit_day, published = gh.validate_revision(revision, run, run_id)
    _, published_tree = _tree(gh, github_client, published)
    if not generated:
        require(published == run["head_sha"], "NOOP_P0_MUST_REDEPLOY_ITS_UNCHANGED_BASE")
    head = github_client.get_json(gh.API_PREFIX + "/git/ref/heads/main")
    require(head.get("ref") == "refs/heads/main" and head.get("object", {}).get("type") == "commit",
        "CURRENT_MAIN_TRIGGER_REF_REQUIRED")
    current = gh.exact_sha(head["object"].get("sha"), 40)
    _, current_tree = _tree(gh, github_client, current)
    existing = _existing_day(gh, runner, github_client, current_tree, published_tree, day, trade, exit_day, revision)
    require(generated or existing is not None, "P0_ALREADY_EXISTS_BUT_CANDIDATE_FREEZE_MISSING_REQUIRES_REPAIR")
    require(gh.validate_run(github_client.get_json(path), run_id) == bound_run
        and github_client.get_json(path + "/attempts/1/jobs?per_page=100") == jobs, "P0_TRIGGER_CHANGED_DURING_CHECK")
    workflow.publication_guard()
    return {"schema_version": SCHEMA, "status": "NEW_P0_REQUIRES_ORIGINAL_IMPORT" if existing is None
        else "EXISTING_CANDIDATE_VERIFIED_NO_NEW_FREEZE", "needs_work": existing is None,
        "p0_run_id": int(run_id), "p0_new_generation": generated, "signal_date": day,
        "observed_main_sha": current, "p0_pages_revision_sha256": revision_sha,
        "existing": existing, **FLAGS}


def inspect_freeze(*, run_id, github_client):
    _, gh = workflow.dependencies()
    run_id = gh.exact_id(run_id)
    path = gh.API_PREFIX + "/actions/runs/" + run_id
    run = github_client.get_json(path)
    require(type(run.get("id")) is int and run["id"] == int(run_id)
        and type(run.get("workflow_id")) is int and run["workflow_id"] == FORWARD_ID
        and run.get("path") == FORWARD_PATH and run.get("name") == FORWARD_NAME
        and run.get("head_branch") == "main" and type(run.get("run_attempt")) is int and run["run_attempt"] == 1
        and run.get("status") == "completed" and run.get("conclusion") == "success"
        and run.get("event") in ("workflow_run", "workflow_dispatch")
        and run.get("repository", {}).get("full_name") == gh.REPOSITORY
        and run.get("head_repository", {}).get("full_name") == gh.REPOSITORY, "EXACT_SUCCESSFUL_FREEZE_TRIGGER_REQUIRED")
    document = github_client.get_json(path + "/attempts/1/jobs?per_page=100")
    found = _jobs(document, run_id)
    require(set(found) == set(FORWARD_JOBS), "EXACT_FORWARD_JOB_PAIR_REQUIRED")
    validate, freeze = [found[name] for name in FORWARD_JOBS]
    require(validate.get("status") == freeze.get("status") == "completed"
        and validate.get("conclusion") == "success" and freeze.get("conclusion") in ("success", "skipped"),
        "UNSUCCESSFUL_FREEZE_TRIGGER")
    if freeze["conclusion"] == "success":
        return {"schema_version": SCHEMA, "status": "FREEZE_REQUIRES_ORIGINAL_OBSERVER", "needs_work": True, **FLAGS}
    require(_step_states(validate).get(PREFLIGHT_STEP) == "success", "FREEZE_SKIPPED_WITHOUT_VERIFIED_PREFLIGHT")
    _, code_tree = _tree(gh, github_client, run["head_sha"])
    for name in (MODULE_PATH, FORWARD_PATH):
        raw = gh.read(workflow.ROOT / name)[0]
        require(code_tree.get(name, {}).get("sha") == gh.git_blob(raw), "NOOP_FREEZE_CODE_VERSION_CHANGED")
    artifacts = github_client.get_json(path + "/artifacts?per_page=100")
    renamed = {**artifacts, "artifacts": [dict(a, name="github-pages") if a.get("name")
        == f"dc20-candidate-preflight-{run_id}-1" else a for a in artifacts.get("artifacts", [])]}
    artifact = gh.select_artifact(renamed, run, run_id)
    archive = github_client.get_artifact_zip(artifact["id"])
    require(type(archive) is bytes and len(archive) == artifact["size_in_bytes"]
        and gh.sha256(archive) == artifact["digest"][7:], "FREEZE_PREFLIGHT_ARTIFACT_CHANGED")
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        entries = zipped.infolist()
        require(len(entries) == 1 and entries[0].filename == "candidate-preflight.json"
            and entries[0].file_size <= 128 * 1024 and not entries[0].flag_bits & 1
            and stat.S_IFMT(entries[0].external_attr >> 16) in (0, stat.S_IFREG), "EXACT_PREFLIGHT_RECEIPT_REQUIRED")
        receipt = gh.parse_json(zipped.read(entries[0]))
    require(receipt.get("schema_version") == SCHEMA and receipt.get("status") == "EXISTING_CANDIDATE_VERIFIED_NO_NEW_FREEZE"
        and receipt.get("needs_work") is False and type(receipt.get("p0_run_id")) is int,
        "PREFLIGHT_DID_NOT_VERIFY_EXISTING_FREEZE")
    rechecked = inspect_p0(run_id=str(receipt["p0_run_id"]), github_client=github_client)
    require(rechecked["needs_work"] is False and rechecked["existing"] == receipt.get("existing")
        and rechecked["signal_date"] == receipt.get("signal_date"), "EXISTING_FREEZE_CHANGED_AFTER_PREFLIGHT")
    require(github_client.get_json(path) == run
        and github_client.get_json(path + "/attempts/1/jobs?per_page=100") == document,
        "FREEZE_TRIGGER_CHANGED_DURING_CHECK")
    return {**rechecked, "status": "NO_NEW_FREEZE_EXISTING_EVIDENCE_PRESERVED", "trigger_freeze_run_id": int(run_id)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("p0", "freeze"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    _, gh = workflow.dependencies()
    target = gh.path(args.receipt, exists=False)
    require(workflow.ROOT != target and workflow.ROOT not in target.parents and not target.exists(),
        "NEW_PREFLIGHT_RECEIPT_OUTSIDE_REPOSITORY_REQUIRED")
    gh.path(target.parent, directory=True)
    client = gh.GitHubReadClient(os.environ.get(gh.TOKEN_ENV, ""))
    result = (inspect_p0 if args.kind == "p0" else inspect_freeze)(run_id=args.run_id, github_client=client)
    gh.write_new(target.parent, target.name, gh.json_bytes(result))
    output = gh.path(os.environ.get("GITHUB_OUTPUT"))
    with output.open("a", encoding="utf-8") as handle:
        handle.write("needs_work=" + ("true" if result["needs_work"] else "false") + "\n")
    print(json.dumps({"status": result["status"], "needs_work": result["needs_work"], "signal_date": result.get("signal_date")}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        reason = str(exc) if str(exc) in PUBLIC_DIAGNOSTICS else "INVALID_OR_CHANGED_TRIGGER_EVIDENCE"
        print(json.dumps({"status": "CANDIDATE_TRIGGER_PREFLIGHT_BLOCKED", "reason": reason}))
        raise SystemExit(1)
