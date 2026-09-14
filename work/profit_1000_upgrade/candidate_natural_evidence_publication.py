"""Independently observe a Git-held research capsule and issue bounded proof.

No original P0 Pages ZIP is needed. The same observer run's ACK artifact must
still be available (currently 90-day retention): this is not permanent offline
attestation. An injected client never issues the private proof class.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import weakref
import zipfile
import zlib

ROOT = Path(__file__).absolute().parents[2]
CAPTURE_PATH = "work/profit_1000_upgrade/candidate_natural_evidence.py"
CAPTURE_SHA = "d40677e35ecbb8c255c1b039e0eee03b66e97eaa2dccc973ac69b514c0de7dd3"
WORKFLOW_PATH = ".github/workflows/research_candidate_natural_observer.yml"
WORKFLOW_NAME = "DC20 · Preserve natural candidate publication (research)"
OBSERVER_PATH = "work/profit_1000_upgrade/candidate_natural_observer.py"
WRITER_PATH = "work/profit_1000_upgrade/candidate_natural_evidence_git.py"
OBSERVER_SHA = "d96a5c349ceea76a3fe2f74111f75bfa87a652bdb556883bf97d761bb9345774"
WORKFLOW_SHA = "01ce1bc9b651b6f4a5f6debd265f130dfdd6db317f3b5cd473a8405caa788fcf"
WRITER_SHA = "e876d5865d72e8dddef1e920a68dfdf9d24e26d7b647b96672a7ff89c6358a76"
JOBS = ("Validate publication evidence preservation", "Verify and preserve original publication evidence")
PREFIX = "work/profit_1000_upgrade/candidate_natural_evidence/"
SCHEMA = "dc20_independently_observed_durable_research_publication_v1"
MAX_ZIP_BYTES, MAX_EXPANDED_BYTES, MAX_MEMBERS = 96 * 1024**2, 65 * 1024**2, 80


def require(ok, reason):
    if not ok: raise ValueError(reason)


def sha(raw): return hashlib.sha256(raw).hexdigest()


_p = ROOT / CAPTURE_PATH
require(_p.is_file() and _p.stat().st_nlink == 1 and not any(p.is_symlink() for p in (_p, *_p.parents))
    and sha(_p.read_bytes()) == CAPTURE_SHA, "FROZEN_EVIDENCE_CAPTURE_REQUIRED")
from work.profit_1000_upgrade import candidate_natural_evidence as capture
gh, publication = capture.gh, capture.publication
SELF_SHA = sha(gh.read(Path(__file__).absolute())[0])


def code_guard():
    require(Path(capture.__file__).absolute() == ROOT / CAPTURE_PATH, "EXACT_CAPTURE_IMPORT_ORIGIN_REQUIRED")
    local, state = publication.code_guard()
    states = []
    for relative, expected in ((CAPTURE_PATH, CAPTURE_SHA), (OBSERVER_PATH, OBSERVER_SHA),
            (WORKFLOW_PATH, WORKFLOW_SHA), (WRITER_PATH, WRITER_SHA)):
        require(expected != "0" * 64, "OBSERVER_REGISTRATION_NOT_ACTIVE")
        raw, identity = gh.read(ROOT / relative)
        require(sha(raw) == expected, "OBSERVER_CODE_OR_POLICY_CHANGED")
        local[relative] = raw; states.append(identity)
    own, own_id = gh.read(Path(__file__).absolute())
    require(sha(own) == SELF_SHA, "PUBLICATION_ISSUER_CODE_CHANGED")
    return local, (state, tuple(states), own_id, capture.code_guard())


def _run(run, registration, run_id):
    require(type(registration.get("id")) is int and registration["id"] > 0
        and registration.get("path") == WORKFLOW_PATH and registration.get("name") == WORKFLOW_NAME
        and registration.get("state") == "active", "REGISTERED_OBSERVER_WORKFLOW_REQUIRED")
    require(type(run.get("id")) is int and run["id"] == int(run_id)
        and type(run.get("workflow_id")) is int and run["workflow_id"] == registration["id"]
        and run.get("path") == WORKFLOW_PATH and run.get("name") == WORKFLOW_NAME
        and run.get("head_branch") == "main" and type(run.get("run_attempt")) is int and run["run_attempt"] == 1
        and run.get("status") == "completed" and run.get("conclusion") == "success"
        and run.get("event") in ("workflow_run", "workflow_dispatch")
        and run.get("repository", {}).get("full_name") == gh.REPOSITORY
        and run.get("head_repository", {}).get("full_name") == gh.REPOSITORY,
        "SUCCESSFUL_FIRST_MAIN_OBSERVER_REQUIRED")
    gh.exact_sha(run.get("head_sha"), 40)
    require(gh.utc(run.get("created_at")) <= gh.utc(run.get("run_started_at")) <= gh.utc(run.get("updated_at")),
        "OBSERVER_RUN_TIME_INVALID")


def _jobs(document, run):
    jobs = document.get("jobs")
    require(type(jobs) is list and type(document.get("total_count")) is int and document["total_count"] == len(jobs) == 2,
        "EXACT_OBSERVER_JOB_PAIR_REQUIRED")
    found, ids = {}, set()
    for job in jobs:
        require(type(job) is dict and type(job.get("id")) is int and job["id"] > 0 and job["id"] not in ids
            and type(job.get("run_id")) is int and job["run_id"] == run["id"]
            and job.get("name") in JOBS and job["name"] not in found
            and job.get("status") == "completed" and job.get("conclusion") == "success", "OBSERVER_JOB_NOT_SUCCESSFUL")
        require(gh.utc(run["run_started_at"]) <= gh.utc(job.get("started_at")) <= gh.utc(job.get("completed_at"))
            <= gh.utc(run["updated_at"]), "OBSERVER_JOB_TIME_INVALID")
        found[job["name"]] = {k: job[k] for k in ("id", "run_id", "name", "status", "conclusion", "started_at", "completed_at")}
        ids.add(job["id"])
    ordered = [found[name] for name in JOBS]
    require(gh.utc(ordered[0]["completed_at"]) <= gh.utc(ordered[1]["started_at"]), "OBSERVER_VALIDATION_MUST_PRECEDE_CAPTURE")
    return ordered


def _artifact(document, run):
    entries = document.get("artifacts")
    require(type(entries) is list and type(document.get("total_count")) is int and document["total_count"] == len(entries) <= 100
        and all(type(e) is dict for e in entries), "COMPLETE_OBSERVER_ARTIFACT_LIST_REQUIRED")
    chosen = [a for a in entries if a.get("name") == f"dc20-candidate-observer-{run['id']}-1"]
    require(len(chosen) == 1, "UNIQUE_SAME_OBSERVER_ARTIFACT_REQUIRED")
    a = chosen[0]; origin = a.get("workflow_run", {})
    require(type(a.get("id")) is int and a["id"] > 0 and a.get("expired") is False
        and type(a.get("size_in_bytes")) is int and 0 < a["size_in_bytes"] <= MAX_ZIP_BYTES
        and type(origin.get("id")) is int and origin["id"] == run["id"]
        and origin.get("head_branch") == "main" and origin.get("head_sha") == run["head_sha"], "UNEXPIRED_SAME_RUN_ACK_REQUIRED")
    require(type(a.get("digest")) is str and a["digest"].startswith("sha256:"), "EXTERNAL_OBSERVER_ARCHIVE_DIGEST_REQUIRED")
    gh.exact_sha(a["digest"][7:])
    require(gh.utc(run["run_started_at"]) <= gh.utc(a.get("created_at")) <= gh.utc(a.get("updated_at")) <= gh.utc(run["updated_at"]),
        "OBSERVER_ARTIFACT_TIME_INVALID")
    return {k: a[k] for k in ("id", "name", "size_in_bytes", "digest", "expired", "created_at", "updated_at")}


def read_observer_archive(raw):
    """Read only a rootless or one exact observer root; never scan for evidence."""
    require(type(raw) is bytes and 0 < len(raw) <= MAX_ZIP_BYTES, "OBSERVER_ARCHIVE_SIZE_LIMIT")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries, directories, total = {}, set(), 0
            infos = archive.infolist()
            require(0 < len(infos) <= MAX_MEMBERS, "OBSERVER_ARCHIVE_MEMBER_LIMIT")
            for info in infos:
                name = gh.relative(info.filename[:-1] if info.is_dir() else info.filename)
                require(name not in entries and name not in directories and not info.flag_bits & 1, "DUPLICATE_OR_ENCRYPTED_OBSERVER_MEMBER")
                mode = (info.external_attr >> 16) & 0o170000
                require(mode in ((0, stat.S_IFDIR) if info.is_dir() else (0, stat.S_IFREG)), "REGULAR_OBSERVER_MEMBERS_ONLY")
                require(0 <= info.file_size <= capture.MAX_FILE_BYTES, "OBSERVER_MEMBER_SIZE_LIMIT")
                total += info.file_size
                require(total <= MAX_EXPANDED_BYTES, "OBSERVER_EXPANSION_LIMIT")
                if info.is_dir(): directories.add(name)
                else: entries[name] = info
            roots = {p.split("/", 1)[0] for p in (*entries, *directories) if p.startswith("dc20-candidate-observer-")}
            require(len(roots) <= 1, "ONE_OBSERVER_ARCHIVE_ROOT_REQUIRED")
            root = next(iter(roots)) + "/" if roots else ""
            require(not root or re.fullmatch(r"dc20-candidate-observer-[A-Za-z0-9_-]+/", root), "EXACT_OBSERVER_ROOT_REQUIRED")
            normalized = {}
            for name, info in entries.items():
                require(name.startswith(root), "MIXED_OBSERVER_ROOTS")
                relative = name[len(root):]
                require(relative in ("publication.json", "context.json", "capture.json", "capsule/manifest.json")
                    or re.fullmatch(r"capsule/bodies/[0-9a-f]{64}\.bin", relative), "UNKNOWN_OBSERVER_MEMBER_NOT_READ")
                require(relative not in normalized, "DUPLICATE_NORMALIZED_OBSERVER_MEMBER")
                normalized[relative] = info
            allowed_dirs = {root[:-1]} if root else set()
            allowed_dirs |= {root + "capsule", root + "capsule/bodies"}
            require(directories <= allowed_dirs, "UNKNOWN_OBSERVER_DIRECTORY_NOT_READ")
            require(not any(str(parent) in entries for name in entries for parent in PurePosixPath(name).parents if str(parent) != "."),
                "OBSERVER_FILE_DIRECTORY_CONFLICT")
            require({"publication.json", "context.json", "capture.json", "capsule/manifest.json"} <= set(normalized),
                "COMPLETE_OBSERVER_OUTPUT_REQUIRED")
            manifest_raw = archive.read(normalized["capsule/manifest.json"])
            preview = capture._safe_body(manifest_raw)
            require(type(preview.get("files")) is list and len(preview["files"]) < capture.MAX_FILES,
                "BOUNDED_ORIGINAL_CAPSULE_INDEX_REQUIRED")
            indexed = set()
            for binding in preview["files"]:
                require(type(binding) is dict and binding.get("path") == "bodies/" + gh.exact_sha(binding.get("sha256")) + ".bin"
                    and binding["path"] not in indexed, "EXACT_CAPSULE_MEMBER_INDEX_REQUIRED")
                indexed.add(binding["path"])
            require(set(normalized) == {"publication.json", "context.json", "capture.json", "capsule/manifest.json"}
                | {"capsule/"+p for p in indexed}, "UNREGISTERED_HASH_BODY_NOT_READ")
            result = {"capsule/manifest.json": manifest_raw}
            for path, info in normalized.items():
                if path == "capsule/manifest.json": continue
                raw_body = archive.read(info)
                require(len(raw_body) == info.file_size, "OBSERVER_ARCHIVE_BODY_CHANGED")
                capture._safe_body(raw_body)
                result[path] = raw_body
            return result
    except (zipfile.BadZipFile, RuntimeError, OSError, EOFError, zlib.error):
        raise ValueError("INVALID_OBSERVER_ARCHIVE") from None


def _context(context, run, manifest, observation):
    expected = {"observer_workflow_path": WORKFLOW_PATH, "observer_run_id": run["id"], "run_attempt": 1,
        "code_head_sha": run["head_sha"], "repository": gh.REPOSITORY, "branch": "main",
        "schema_version": "dc20_candidate_natural_observer_context_v1", "signal_date": manifest["signal_date"],
        "freeze_run_id": int(manifest["freeze_run_id"]), "snapshot_file_sha256": manifest["snapshot_file_sha256"],
        "manifest_sha256": None, "publication_observation_sha256": manifest["native_observation"]["sha256"],
        "capture_module_sha256": CAPTURE_SHA, "coordinator_sha256": OBSERVER_SHA, "writer_sha256": WRITER_SHA,
        "created_at_host_utc": context.get("created_at_host_utc"), "original_prospective_publication_observed": True,
        "evidence_natural_admission_issued": False, "production_activation_allowed": False, "actual_execution_claimed": False}
    expected["manifest_sha256"] = context["manifest_sha256"]
    gh.exact_sha(expected["manifest_sha256"])
    publication.natural.scorer._exact(context, expected, "EXACT_OBSERVER_CONTEXT_BINDING_REQUIRED")
    require(observation["model_canonical_sha256"] == publication.natural.MODEL_SHA
        and observation["model_evaluation_sha256"] == publication.natural.EVALUATION_SHA, "FROZEN_REAL_MODEL_BINDING_REQUIRED")


_KEY, _ISSUED = object(), weakref.WeakKeyDictionary()


class VerifiedResearchPublication:
    """Private immutable observer-bound research proof, not an order or fill."""
    __slots__ = ("_raw", "_guard", "__weakref__")
    def __init__(self, key=None, report=None, guard=None):
        require(key is _KEY and type(self) is VerifiedResearchPublication, "PRIVATE_PUBLICATION_ISSUER_REQUIRED")
        object.__setattr__(self, "_raw", gh.json_bytes(report))
        object.__setattr__(self, "_guard", sha(repr(guard).encode()))
        _ISSUED[self] = (sha(self._raw), self._guard)
    def __setattr__(self, name, value): raise AttributeError("IMMUTABLE_RESEARCH_PUBLICATION")
    def __getattr__(self, name):
        if name in ("signal_date", "snapshot_file_sha256", "publication_observation_sha256", "evidence_manifest_sha256",
                "observer_run_id", "evidence_commit"):
            return gh.parse_json(self._raw)[name]
        raise AttributeError(name)
    @property
    def report(self): return gh.parse_json(self._raw)
    def assert_unchanged(self):
        require(type(self) is VerifiedResearchPublication and _ISSUED.get(self) == (sha(self._raw), self._guard), "UNISSUED_OR_MUTATED_RESEARCH_PUBLICATION")
        require(sha(repr(code_guard()).encode()) == self._guard, "PUBLICATION_PROOF_CODE_CHANGED")


def verify_published_evidence(*, evidence_commit, observer_run_id, github_client):
    evidence_commit = gh.exact_sha(evidence_commit, 40); observer_run_id = gh.exact_id(observer_run_id)
    local, before = code_guard()
    reads = publication._Reads(github_client)
    workflow_api = "/actions/workflows/research_candidate_natural_observer.yml"
    run_api = "/actions/runs/" + observer_run_id
    registration = reads.json(workflow_api)
    run = reads.json(run_api); _run(run, registration, observer_run_id)
    jobs = _jobs(reads.json(run_api + "/attempts/1/jobs?per_page=100"), run)
    artifact = _artifact(reads.json(run_api + "/artifacts?per_page=100"), run)
    archive = read_observer_archive(reads.archive(artifact))
    ack, context, captured = (gh.parse_json(archive[p]) for p in ("publication.json", "context.json", "capture.json"))
    require(ack.get("schema_version") == "dc20_candidate_natural_evidence_git_ack_v1"
        and ack.get("status") == "EVIDENCE_GIT_ACKNOWLEDGED" and ack.get("commit_sha") == evidence_commit,
        "SAME_OBSERVER_ACK_COMMIT_REQUIRED")
    manifest_raw = archive["capsule/manifest.json"]
    bodies = {p.removeprefix("capsule/"): b for p, b in archive.items() if p.startswith("capsule/bodies/")}
    manifest = capture.verify_materials(manifest_raw, bodies, expected_manifest_sha256=context["manifest_sha256"])
    require(manifest["test_transport_injected"] is False, "SYNTHETIC_CAPSULE_CANNOT_ISSUE_PUBLICATION")
    observation = gh.parse_json(bodies[manifest["native_observation"]["path"]])
    _context(context, run, manifest, observation)
    day = manifest["signal_date"]; require(ack.get("signal_date") == day, "ACK_DAY_CHANGED")
    for key, value in {"independent_observer_job_check_required": True, "natural_forward_admission_issued": False,
            "production_activation_allowed": False, "actual_execution_claimed": False, "existing_files_modified": False}.items():
        publication.natural.scorer._exact(ack.get(key), value, "ACK_QUALIFICATION_CHANGED")
    wanted_result = capture._result(Path(captured["output_root"]), manifest, manifest_raw)
    publication.natural.scorer._exact(captured, wanted_result, "ORIGINAL_CAPTURE_RESULT_CHANGED")
    cutoff = publication.workflow.aware(observation["publication_deadline"])
    created, acknowledged = publication.workflow.aware(context["created_at_host_utc"]), publication.workflow.aware(ack["acknowledged_at_host_utc"])
    require(gh.utc(observation["latest_publication_bound_utc"]) <= gh.utc(jobs[1]["started_at"]) <= created <= acknowledged
        <= gh.utc(jobs[1]["completed_at"]) < cutoff, "INDEPENDENT_OBSERVER_MISSES_PRE_AUCTION_DEADLINE")
    require((cutoff - acknowledged).total_seconds() > 300, "ORIGINAL_OBSERVER_T0920_SAFETY_BOUNDARY")
    commit, tree = reads.tree(evidence_commit)
    require(commit["tree"]["sha"] == ack["tree_sha"] and [p["sha"] for p in commit.get("parents", [])] == [ack["parent_sha"]],
        "OBSERVER_ACK_GIT_COMMIT_CHANGED")
    _, parent_tree = reads.tree(gh.exact_sha(ack["parent_sha"], 40))
    _, code_tree = reads.tree(run["head_sha"])
    for path, raw in local.items():
        publication._blob_matches(code_tree, path, raw); publication._blob_matches(tree, path, raw)
    prefix = PREFIX + day + "/"
    files = {prefix + "manifest.json": manifest_raw, prefix + "context.json": archive["context.json"],
        **{prefix + p: raw for p, raw in bodies.items()}}
    expected_ack = [{"path": p, "sha256": sha(raw), "git_blob_sha1": gh.git_blob(raw), "bytes": len(raw)} for p, raw in sorted(files.items())]
    publication.natural.scorer._exact(ack.get("files"), expected_ack, "ORIGINAL_CAPSULE_GIT_ACK_FILES_CHANGED")
    for path, raw in files.items(): publication._blob_matches(tree, path, raw)
    # Tree SHA is independently recomputed by validate_tree; ZIP bytes match its
    # Git blob SHA. No one-HTTP-request-per-body and no fake merged HTTP body.
    leaves = lambda t: {p: (v["mode"], v["type"], v["sha"]) for p, v in t.items() if v["type"] != "tree"}
    require(not any(p.startswith(prefix) for p in parent_tree)
        and leaves(tree) == {**leaves(parent_tree), **{p: ("100644", "blob", gh.git_blob(b)) for p, b in files.items()}},
        "ONLY_FRESH_SINGLE_DAY_CAPSULE_ADDITIONS_ALLOWED")
    parents = {str(p) for name in files for p in PurePosixPath(name).parents if str(p) != "."}
    require(set(tree) == set(parent_tree) | set(files) | parents
        and all(tree[p]["type"] == "tree" and tree[p]["mode"] == "040000" for p in parents)
        and all(tree[p] == v for p, v in parent_tree.items() if v["type"] == "tree" and p not in parents),
        "UNRELATED_OBSERVER_TREE_CHANGE")
    for route in (workflow_api, run_api, run_api + "/attempts/1/jobs?per_page=100", run_api + "/artifacts?per_page=100"):
        reads.json(route)
    current = reads.json("/git/ref/heads/main", immutable=False)
    require(current.get("ref") == "refs/heads/main" and current.get("object", {}).get("type") == "commit", "CURRENT_MAIN_REF_REQUIRED")
    current_sha = gh.exact_sha(current["object"]["sha"], 40)
    _, current_tree = reads.tree(current_sha)
    for path, raw in files.items(): publication._blob_matches(current_tree, path, raw)
    require(code_guard() == (local, before), "OBSERVER_ISSUER_CODE_CHANGED_DURING_VERIFY")
    report = {"schema_version": SCHEMA, "status": "INDEPENDENT_OBSERVER_RESEARCH_PUBLICATION_OBSERVED" if reads.actual else "SYNTHETIC_OBSERVER_CHECK_ONLY",
        "signal_date": day, "snapshot_file_sha256": manifest["snapshot_file_sha256"],
        "publication_observation_sha256": manifest["native_observation"]["sha256"], "evidence_manifest_sha256": sha(manifest_raw),
        "observer_run_id": int(observer_run_id), "observer_code_head_sha": run["head_sha"], "evidence_commit": evidence_commit,
        "evidence_tree_sha": ack["tree_sha"], "observer_artifact": artifact, "observer_ack_sha256": sha(archive["publication.json"]),
        "independent_observer_jobs": jobs, "freeze_run_id": int(manifest["freeze_run_id"]),
        "research_prospective_publication_observed": reads.actual, "injected_client_for_test": not reads.actual,
        "current_main_evidence_bytes_preserved": True, "current_main_sha": current_sha, "git_ancestry_verified": False,
        "original_p0_artifact_refetched": False, "reissuance_requires_unexpired_observer_ack_artifact": True,
        "permanent_offline_attestation_verified": False, "evidence_file_bindings": expected_ack,
        "issuer_sha256": SELF_SHA, "capture_sha256": CAPTURE_SHA, "bounded_request_cost": reads.cost,
        "source_authority_issued": False, "natural_outcome_admission_issued": False, "production_activation_allowed": False,
        "formal_model_replacement_allowed": False, "actual_execution_claimed": False, "actual_capacity_verified": False,
        "provider_timestamp_semantics_confirmed": False, "future_outcomes_read": False, "model_training_performed": False,
        "predictions_recomputed": False, "files_written": 0, "remote_writes_performed": 0}
    if not reads.actual: return report
    return VerifiedResearchPublication(_KEY, report, (local, before))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-commit", required=True)
    parser.add_argument("--observer-run-id", required=True)
    args = parser.parse_args(argv)
    proof = verify_published_evidence(evidence_commit=args.evidence_commit, observer_run_id=args.observer_run_id,
        github_client=gh.GitHubReadClient(os.environ.get(gh.TOKEN_ENV, "")))
    require(type(proof) is VerifiedResearchPublication, "REAL_OBSERVER_PROOF_REQUIRED")
    proof.assert_unchanged()
    print(gh.json_bytes(proof.report).decode(), end="")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("DURABLE_RESEARCH_PUBLICATION_NOT_VERIFIED")
        raise SystemExit(1)
