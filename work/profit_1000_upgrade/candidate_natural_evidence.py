"""Preserve a successful live research publication observation, without admission.

Original JSON HTTP bodies and Pages revision bytes are content-addressed. ZIP
bodies, Authorization headers and signed redirect URLs are never persisted.
Local integrity alone cannot reissue cross-day prospective-publication proof.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tarfile
import urllib.error
import urllib.parse
import zipfile

ROOT = Path(__file__).absolute().parents[2]
PUBLICATION_PATH = "work/profit_1000_upgrade/candidate_natural_publication.py"
PUBLICATION_SHA = "c4f9da536573e5aab82d66c5424c4c72f80e01f649da2a531ee400f2eb029322"
SCHEMA = "dc20_candidate_natural_durable_evidence_v1"
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_FILES = 64
MAX_CAPTURE_BYTES = 320 * 1024 * 1024
FLAGS = {"research_only": True, "source_authority_issued": False, "natural_forward_admission_issued": False,
    "observer_workflow_publication_verified": False, "production_activation_allowed": False,
    "actual_execution_claimed": False, "actual_capacity_verified": False,
    "provider_timestamp_semantics_confirmed": False, "model_training_performed": False,
    "predictions_recomputed": False, "future_outcomes_read": False, "remote_writes_performed": 0}


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


_p = ROOT / PUBLICATION_PATH
require(_p.is_file() and _p.stat().st_nlink == 1 and not any(p.is_symlink() for p in (_p, *_p.parents))
    and sha(_p.read_bytes()) == PUBLICATION_SHA, "FROZEN_PUBLICATION_VERIFIER_REQUIRED")
from work.profit_1000_upgrade import candidate_natural_publication as publication

gh = publication.gh
SELF_SHA = sha(gh.read(Path(__file__).absolute())[0])


def code_guard():
    require(Path(publication.__file__).absolute() == ROOT / PUBLICATION_PATH, "EVIDENCE_PUBLICATION_IMPORT_CHANGED")
    raw, identity = gh.read(ROOT / PUBLICATION_PATH)
    require(sha(raw) == PUBLICATION_SHA, "EVIDENCE_PUBLICATION_CODE_CHANGED")
    own, own_identity = gh.read(Path(__file__).absolute())
    require(sha(own) == SELF_SHA, "EVIDENCE_CAPTURE_CODE_CHANGED")
    return identity, own_identity, publication.code_guard()


def _route(path):
    require(type(path) is str and path.startswith(gh.API_PREFIX + "/"), "EXACT_OBSERVED_REPOSITORY_ROUTE_REQUIRED")
    suffix = path[len(gh.API_PREFIX):]
    patterns = (r"/actions/workflows/research_candidate_natural_forward\.yml",
        r"/actions/runs/[1-9][0-9]*(?:/attempts/1/jobs\?per_page=100|/artifacts\?per_page=100)?",
        r"/git/(?:commits|blobs)/[0-9a-f]{40}", r"/git/trees/[0-9a-f]{40}\?recursive=1", r"/git/ref/heads/main",
        r"/actions/artifacts/[1-9][0-9]*/zip")
    require(any(re.fullmatch(pattern, suffix) for pattern in patterns), "UNREGISTERED_OBSERVATION_ROUTE")
    return path


def _safe_text(value, token):
    if type(value) is str:
        require(not token or token not in value, "CREDENTIAL_IN_EVIDENCE_REJECTED")
        require(re.search(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})", value) is None,
            "CREDENTIAL_LIKE_VALUE_IN_EVIDENCE_REJECTED")
        for match in re.finditer(r"https?://[^\s\"<>]+", value):
            parsed = urllib.parse.urlsplit(match.group())
            host = parsed.hostname or ""
            keys = {key.lower() for key, _ in urllib.parse.parse_qsl(parsed.query)}
            require(not host.endswith((".blob.core.windows.net", ".actions.githubusercontent.com"))
                and not keys.intersection({"sig", "signature", "token", "access_token", "x-amz-signature", "x-amz-credential",
                    "x-amz-security-token", "se", "sp", "sv", "skoid", "sktid", "skt", "ske", "sks", "skv"}),
                "SIGNED_OR_CDN_URL_IN_EVIDENCE_REJECTED")
    elif type(value) is dict:
        for key, item in value.items():
            _safe_text(key, token); _safe_text(item, token)
    elif type(value) is list:
        for item in value:
            _safe_text(item, token)


def _safe_body(raw, token=None, *, blob_response=False):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_FILE_BYTES, "EVIDENCE_BODY_SIZE_LIMIT")
    require(not token or token.encode() not in raw, "RAW_CREDENTIAL_IN_EVIDENCE_REJECTED")
    value = gh.parse_json(raw)
    _safe_text(value, token)  # JSON escapes are decoded before checking secrets.
    if blob_response:
        require(value.get("encoding") == "base64" and type(value.get("content")) is str, "ORIGINAL_BLOB_ENVELOPE_REQUIRED")
        decoded = base64.b64decode(value["content"], validate=False)
        require(not token or token.encode() not in decoded, "BLOB_CREDENTIAL_IN_EVIDENCE_REJECTED")
        try:
            inside = gh.parse_json(decoded)
        except ValueError:
            _safe_text(decoded.decode("utf-8", errors="strict"), token)
        else:
            _safe_text(inside, token)
    return value


class _Response:
    def __init__(self, original, record, owner):
        self.original, self.record, self.owner = original, record, owner
        self.status = original.status
        self.parts = []
    def __enter__(self):
        self.original.__enter__()
        return self
    def read(self, size):
        raw = self.original.read(size)
        require(type(raw) is bytes, "ORIGINAL_HTTP_BYTES_REQUIRED")
        self.owner.total += len(raw)
        require(self.owner.total <= MAX_CAPTURE_BYTES, "BOUNDED_HTTP_CAPTURE_REQUIRED")
        self.parts.append(raw)
        return raw
    def __exit__(self, *args):
        self.record["raw"] = b"".join(self.parts)
        return self.original.__exit__(*args)


class _Recorder:
    """Wrap only the original no-redirect opener; never log request headers."""
    def __init__(self, opener):
        self.opener, self.records, self.total, self.redirect = opener, [], 0, None
    def open(self, request, *, timeout):
        require(request.get_method() == "GET" and timeout == gh.SOCKET_TIMEOUT_SECONDS, "EXACT_READ_ONLY_HTTP_REQUIRED")
        url = urllib.parse.urlsplit(request.full_url)
        require(url.scheme == "https" and url.username is None and url.password is None and not url.fragment,
            "SAFE_OBSERVATION_URL_REQUIRED")
        if url.hostname == "api.github.com":
            route = _route(url.path + ("?" + url.query if url.query else ""))
            match = re.fullmatch(re.escape(gh.API_PREFIX) + r"/actions/artifacts/([1-9][0-9]*)/zip", route)
            record = {"kind": "ARTIFACT_REDIRECT" if match else "API_JSON", "api_path": route,
                "artifact_id": int(match[1]) if match else None, "status": None}
        else:
            require(self.redirect is not None and request.full_url == self.redirect[0]
                and not request.has_header("Authorization"), "EXACT_UNAUTHENTICATED_ARTIFACT_REDIRECT_REQUIRED")
            record = {"kind": "ARTIFACT_ZIP", "api_path": None, "artifact_id": self.redirect[1], "status": None}
            self.redirect = None
        record["ordinal"] = len(self.records) + 1
        self.records.append(record)
        require(len(self.records) <= gh.MAX_API_CALLS, "OBSERVATION_HTTP_COUNT_LIMIT")
        try:
            response = self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if record["kind"] == "ARTIFACT_REDIRECT" and exc.code == 302:
                self.redirect = (exc.headers.get("Location"), record["artifact_id"])
                record.update(status=302, raw=b"")
            raise
        record["status"] = response.status
        return _Response(response, record, self)


def _revision(raw):
    expected, expected_sha = gh.revision_from_zip(raw)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        tar_raw = archive.read(archive.infolist()[0])
    with tarfile.open(fileobj=io.BytesIO(tar_raw), mode="r:") as tar:
        members = [m for m in tar.getmembers() if m.name in ("revision.json", "./revision.json")]
        require(len(members) == 1, "ORIGINAL_PAGES_REVISION_REQUIRED")
        body = tar.extractfile(members[0]).read(gh.MAX_REVISION_BYTES + 1)
    require(sha(body) == expected_sha and gh.parse_json(body) == expected, "ORIGINAL_REVISION_BYTES_CHANGED")
    return body


def _ref(raw):
    return {"path": "bodies/" + sha(raw) + ".bin", "sha256": sha(raw), "bytes": len(raw)}


def _result(root, manifest, manifest_raw):
    return {"status": "LOCAL_UNPUBLISHED_OBSERVATION_TEST_ONLY" if manifest["test_transport_injected"] else "LOCAL_UNPUBLISHED_OBSERVATION",
        "signal_date": manifest["signal_date"], "snapshot_file_sha256": manifest["snapshot_file_sha256"],
        "publication_observation_sha256": manifest["native_observation"]["sha256"],
        "manifest_path": "manifest.json", "manifest_sha256": sha(manifest_raw), "output_root": str(root),
        "files": [*manifest["files"], {"path": "manifest.json", "sha256": sha(manifest_raw), "bytes": len(manifest_raw)}],
        "test_transport_injected": manifest["test_transport_injected"], **FLAGS}


def verify_materials(manifest_raw, bodies, *, expected_manifest_sha256):
    """Pure byte integrity. This NEVER issues prospective/publication authority."""
    gh.exact_sha(expected_manifest_sha256)
    require(type(manifest_raw) is bytes and sha(manifest_raw) == expected_manifest_sha256, "EXTERNAL_EVIDENCE_MANIFEST_SHA_MISMATCH")
    manifest = _safe_body(manifest_raw)
    require(manifest.get("schema_version") == SCHEMA and manifest.get("status") == "LOCAL_UNPUBLISHED_OBSERVATION"
        and type(manifest.get("test_transport_injected")) is bool, "UNPUBLISHED_EVIDENCE_SCHEMA_REQUIRED")
    for key, expected in FLAGS.items():
        publication.natural.scorer._exact(manifest.get(key), expected, "LOCAL_EVIDENCE_CANNOT_GRANT_AUTHORITY")
    require(manifest.get("capture_code_sha256") == SELF_SHA and manifest.get("publication_verifier_sha256") == PUBLICATION_SHA,
        "CAPTURE_CODE_CONTRACT_CHANGED")
    day = gh.date(manifest.get("signal_date"))
    require(day >= "20260914", "EVIDENCE_NATURAL_START_REQUIRED")
    gh.exact_id(manifest.get("freeze_run_id")); gh.exact_sha(manifest.get("snapshot_file_sha256"))
    require(type(bodies) is dict and type(manifest.get("files")) is list and len(manifest["files"]) < MAX_FILES,
        "BOUNDED_EVIDENCE_INVENTORY_REQUIRED")
    expected_paths, total = set(), len(manifest_raw)
    def body(binding):
        require(type(binding) is dict and set(binding) == {"path", "sha256", "bytes"}
            and binding["path"] == "bodies/" + gh.exact_sha(binding["sha256"]) + ".bin"
            and type(binding["bytes"]) is int and 0 < binding["bytes"] <= MAX_FILE_BYTES, "CONTENT_ADDRESSED_EVIDENCE_REQUIRED")
        raw = bodies[binding["path"]]
        require(type(raw) is bytes and len(raw) == binding["bytes"] and sha(raw) == binding["sha256"], "ORIGINAL_EVIDENCE_BODY_CHANGED")
        return raw
    for binding in manifest["files"]:
        raw = body(binding)
        require(binding["path"] not in expected_paths, "DUPLICATE_EVIDENCE_FILE")
        expected_paths.add(binding["path"]); total += len(raw)
        _safe_body(raw)
    require(set(bodies) == expected_paths and total <= MAX_TOTAL_BYTES, "EXACT_BOUNDED_EVIDENCE_FILES_REQUIRED")
    observation = _safe_body(body(manifest["native_observation"]))
    require(observation.get("schema_version") == publication.SCHEMA and observation.get("freeze_run_id") == int(manifest["freeze_run_id"])
        and observation.get("signal_date") == day and observation.get("snapshot_file_sha256") == manifest["snapshot_file_sha256"]
        and observation.get("verifier_sha256") == PUBLICATION_SHA
        and observation.get("research_prospective_publication_observed") is True
        and observation.get("injected_client_for_test") is False, "ORIGINAL_PUBLICATION_OBSERVATION_CHANGED")
    records = manifest.get("http_observations")
    require(type(records) is list and 0 < len(records) <= gh.MAX_API_CALLS, "BOUNDED_ORIGINAL_HTTP_OBSERVATIONS_REQUIRED")
    redirects, archives, api = [], {}, {}
    for ordinal, record in enumerate(records, 1):
        require(type(record) is dict and set(record) == {"ordinal", "kind", "api_path", "artifact_id", "status", "body_bytes", "body_sha256", "retained_body"}
            and type(record["ordinal"]) is int and record["ordinal"] == ordinal, "ORIGINAL_HTTP_RECORD_SHAPE")
        kind = record["kind"]
        require(type(record["status"]) is int and type(record["body_bytes"]) is int, "EXACT_HTTP_COUNTER_TYPES_REQUIRED")
        if kind == "API_JSON":
            _route(record["api_path"])
            require(record["artifact_id"] is None and record["status"] == 200 and type(record["status"]) is int,
                "SUCCESSFUL_JSON_OBSERVATION_REQUIRED")
            raw = body(record["retained_body"])
            require(record["body_sha256"] == sha(raw) and record["body_bytes"] == len(raw), "ORIGINAL_HTTP_BODY_BINDING_CHANGED")
            api.setdefault(record["api_path"], []).append(_safe_body(raw, blob_response="/git/blobs/" in record["api_path"]))
        elif kind in ("ARTIFACT_REDIRECT", "ARTIFACT_ZIP"):
            require(type(record["artifact_id"]) is int and record["artifact_id"] > 0 and record["retained_body"] is None,
                "ARCHIVE_NOT_PERSISTED_ONLY_BOUND_DIGEST")
            if kind == "ARTIFACT_REDIRECT":
                require(record["api_path"] == gh.API_PREFIX + f"/actions/artifacts/{record['artifact_id']}/zip"
                    and record["status"] == 302 and record["body_bytes"] == 0 and record["body_sha256"] == sha(b""),
                    "ORIGINAL_REDIRECT_METADATA_CHANGED")
                redirects.append(record["artifact_id"])
            else:
                require(record["api_path"] is None and record["status"] == 200 and type(record["body_bytes"]) is int
                    and 0 < record["body_bytes"] <= gh.MAX_ZIP_BYTES and record["artifact_id"] not in archives,
                    "ORIGINAL_ARCHIVE_OBSERVATION_CHANGED")
                gh.exact_sha(record["body_sha256"]); archives[record["artifact_id"]] = record
        else:
            require(False, "UNKNOWN_HTTP_OBSERVATION_KIND")
    require(len(archives) == 2 and sorted(redirects) == sorted(archives), "TWO_ORIGINAL_ARCHIVE_OBSERVATIONS_REQUIRED")
    p0_id = str(observation["original_p0_run_id"])
    p0_run = api[gh.API_PREFIX + "/actions/runs/" + p0_id][0]
    gh.validate_run(p0_run, p0_id)
    p0_artifact = gh.select_artifact(api[gh.API_PREFIX + "/actions/runs/" + p0_id + "/artifacts?per_page=100"][0], p0_run, p0_id)
    require(manifest["pages_archive_evidence"] == p0_artifact, "ORIGINAL_P0_ARTIFACT_METADATA_CHANGED")
    for artifact in (p0_artifact, observation["publication_artifact"]):
        recorded = archives[artifact["id"]]
        require(recorded["body_sha256"] == artifact["digest"][7:] and recorded["body_bytes"] == artifact["size_in_bytes"],
            "ORIGINAL_ARCHIVE_EXTERNAL_DIGEST_CHANGED")
    revision_raw = body(manifest["pages_revision"])
    revision = _safe_body(revision_raw)
    require(gh.validate_revision(revision, p0_run, p0_id) == (day, observation["exec_date"], observation["exit_date"], observation["original_p0_commit_sha"]),
        "ORIGINAL_PAGES_REVISION_IDENTITY_CHANGED")
    require(manifest["original_source_bindings"] == observation["original_source_bindings"]
        and len(manifest["original_source_bindings"]) == 28, "ORIGINAL_SOURCE_GIT_BINDINGS_CHANGED")
    for b in manifest["original_source_bindings"]:
        publication._source_path(b["path"], day)
    return manifest


def verify_local_evidence(root, *, expected_manifest_sha256):
    before = code_guard()
    root = gh.path(root, directory=True)
    manifest_raw, manifest_identity = gh.read(root / "manifest.json", MAX_FILE_BYTES)
    preview = gh.parse_json(manifest_raw)
    require(sha(manifest_raw) == expected_manifest_sha256 and type(preview.get("files")) is list, "EXTERNAL_EVIDENCE_MANIFEST_SHA_MISMATCH")
    wanted = {"manifest.json"}
    for b in preview["files"]:
        require(type(b) is dict and type(b.get("sha256")) is str and b.get("path") == "bodies/" + gh.exact_sha(b["sha256"]) + ".bin",
            "STRICT_EVIDENCE_MEMBER_PATH_REQUIRED")
        wanted.add(b["path"])
    def inventory():
        actual, directories = set(), []
        for directory in (root, root / "bodies"):
            gh.path(directory, directory=True)
            s = directory.stat()
            directories.append((s.st_dev, s.st_ino))
            with os.scandir(directory) as entries:
                for entry in entries:
                    require(not entry.is_symlink(), "EVIDENCE_SYMLINK_REJECTED")
                    rel = (directory / entry.name).relative_to(root).as_posix()
                    if entry.is_dir(follow_symlinks=False):
                        require(rel == "bodies", "UNKNOWN_EVIDENCE_DIRECTORY")
                    else:
                        require(rel in wanted, "UNKNOWN_EVIDENCE_FILE_NOT_READ")
                        actual.add(rel)
        require(actual == wanted, "EXACT_EVIDENCE_DIRECTORY_REQUIRED")
        return directories
    directory_ids = inventory()
    bodies, states = {}, []
    for path in sorted(wanted - {"manifest.json"}):
        raw, identity = gh.read(root / path, MAX_FILE_BYTES)
        bodies[path] = raw; states.append((root / path, identity, sha(raw)))
    manifest = verify_materials(manifest_raw, bodies, expected_manifest_sha256=expected_manifest_sha256)
    require(gh.read(root / "manifest.json", MAX_FILE_BYTES) == (manifest_raw, manifest_identity), "EVIDENCE_MANIFEST_CHANGED")
    for path, identity, digest in states:
        raw, current = gh.read(path, MAX_FILE_BYTES)
        require(current == identity and sha(raw) == digest, "EVIDENCE_BODY_CHANGED_DURING_READ")
    require(code_guard() == before and inventory() == directory_ids, "EVIDENCE_CHANGED_DURING_FINAL_CHECK")
    return {**_result(root, manifest, manifest_raw), "status": "LOCAL_BYTE_INTEGRITY_ONLY_NOT_PUBLICATION_ADMISSION", "manifest": manifest}


def capture_evidence(output_root, *, freeze_run_id, token, transport=None):
    """Verify live once, then preserve only safe bounded original evidence.

    transport is an explicit test seam and ALWAYS marks the whole capsule TEST.
    Neither this capture nor its local integrity checker can issue admission.
    """
    freeze_run_id = gh.exact_id(freeze_run_id)
    output = gh.path(output_root, exists=False)
    require(not output.exists() and ROOT != output and ROOT not in output.parents and output not in ROOT.parents,
        "FRESH_EVIDENCE_ROOT_OUTSIDE_CODE_REQUIRED")
    gh.path(output.parent, directory=True)
    before = code_guard()
    client = gh.GitHubReadClient(token)
    recorder = _Recorder(client._opener if transport is None else transport)
    client._opener = recorder  # No frozen file/class/validator is changed.
    observed = publication.verify_publication(expected_freeze_run_id=freeze_run_id, github_client=client)
    require(observed["research_prospective_publication_observed"] is True and observed["network_calls_performed"] == len(recorder.records),
        "COMPLETE_SAME_PROCESS_PUBLICATION_OBSERVATION_REQUIRED")
    bodies = {}
    def retain(raw, *, blob_response=False):
        _safe_body(raw, token, blob_response=blob_response)
        ref = _ref(raw); bodies[ref["path"]] = raw
        require(len(bodies) < MAX_FILES and sum(map(len, bodies.values())) <= MAX_TOTAL_BYTES, "BOUNDED_PERSISTED_EVIDENCE_REQUIRED")
        return ref
    observations, zip_bytes, api = [], {}, {}
    for record in recorder.records:
        raw = record["raw"]
        binding = None
        if record["kind"] == "API_JSON":
            binding = retain(raw, blob_response="/git/blobs/" in record["api_path"])
            api.setdefault(record["api_path"], []).append(gh.parse_json(raw))
        elif record["kind"] == "ARTIFACT_ZIP":
            zip_bytes[record["artifact_id"]] = raw
        observations.append({k: record[k] for k in ("ordinal", "kind", "api_path", "artifact_id", "status")}
            | {"body_bytes": len(raw), "body_sha256": sha(raw), "retained_body": binding})
    p0_id = str(observed["original_p0_run_id"])
    p0_run = api[gh.API_PREFIX + "/actions/runs/" + p0_id][0]
    pages = gh.select_artifact(api[gh.API_PREFIX + "/actions/runs/" + p0_id + "/artifacts?per_page=100"][0], p0_run, p0_id)
    revision = retain(_revision(zip_bytes[pages["id"]]))
    native_ref = retain(gh.json_bytes(observed))
    manifest = {"schema_version": SCHEMA, "status": "LOCAL_UNPUBLISHED_OBSERVATION", "signal_date": observed["signal_date"],
        "freeze_run_id": freeze_run_id, "snapshot_file_sha256": observed["snapshot_file_sha256"], "native_observation": native_ref,
        "pages_archive_evidence": pages, "pages_revision": revision, "original_source_bindings": observed["original_source_bindings"],
        "http_observations": observations, "files": [_ref(raw) for path, raw in sorted(bodies.items())],
        "capture_code_sha256": SELF_SHA, "publication_verifier_sha256": PUBLICATION_SHA,
        "test_transport_injected": transport is not None, "large_zip_bodies_persisted": False,
        "headers_or_signed_redirect_urls_persisted": False,
        "trust_boundary": "REQUIRES_INDEPENDENT_OBSERVER_RUN_JOB_AND_GIT_MANIFEST_BINDING_FOR_CROSS_DAY_ADMISSION", **FLAGS}
    manifest_raw = gh.json_bytes(manifest)
    verify_materials(manifest_raw, bodies, expected_manifest_sha256=sha(manifest_raw))
    require(len(manifest_raw) <= MAX_FILE_BYTES and len(bodies) + 1 <= MAX_FILES
        and sum(map(len, bodies.values())) + len(manifest_raw) <= MAX_TOTAL_BYTES, "BOUNDED_COMPLETE_CAPSULE_REQUIRED")
    require(code_guard() == before, "CAPTURE_CODE_CHANGED_BEFORE_WRITE")
    require(not output.exists(), "EVIDENCE_OUTPUT_APPEARED")
    output.mkdir(mode=0o700)
    gh.path(output, directory=True)
    for path, raw in sorted(bodies.items()):
        gh.write_new(output, path, raw)
    gh.write_new(output, "manifest.json", manifest_raw)
    verified = verify_local_evidence(output, expected_manifest_sha256=sha(manifest_raw))
    require(code_guard() == before, "CAPTURE_CODE_CHANGED_AFTER_WRITE")
    require(verify_local_evidence(output, expected_manifest_sha256=sha(manifest_raw)) == verified,
        "EVIDENCE_CHANGED_DURING_FINAL_GUARD")
    return _result(output, manifest, manifest_raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze-run-id", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    result = capture_evidence(args.output, freeze_run_id=args.freeze_run_id, token=os.environ.get(gh.TOKEN_ENV, ""))
    print(gh.json_bytes(result).decode(), end="")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("NATURAL_PUBLICATION_EVIDENCE_NOT_ACCEPTED")
        raise SystemExit(1)
