"""Preserve a successful live research publication observation, without admission.

Original JSON HTTP bodies and Pages revision bytes are content-addressed. A
typed continuation also retains its small generation ZIP in a bounded JSON
envelope. Pages/freeze ZIPs, headers and signed URLs are never persisted.
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
import zlib

ROOT = Path(__file__).absolute().parents[2]
PUBLICATION_PATH = "work/profit_1000_upgrade/candidate_natural_publication.py"
PUBLICATION_SHA = '0fd9e3be0640f19553ca4544552b4a250d6653ec2a047184e7fc798e93e22978'
LEGACY_CAPTURE_CONTRACT = (
    "d40677e35ecbb8c255c1b039e0eee03b66e97eaa2dccc973ac69b514c0de7dd3",
    "c4f9da536573e5aab82d66c5424c4c72f80e01f649da2a531ee400f2eb029322",
)
PRE_ELIGIBLE_CAPTURE_CONTRACT = (
    "d70d2d7dac62cf6f3618f92aa69bfd638747642818c43db0c61ff05228f4d089",
    "e2f9abea634cb4a7c0b75af1026c15d7b64e67d6dd4111768bbdf805028d1932",
)
HISTORICAL_CAPTURE_CONTRACT = ('2304a7e9bbf2000de7b42f9d8ab8bb2948bf736a95228ed06271d51f91dc3b90',
 '3a6b5e8434f083200454d5474f6bec0c20d80436b4b86fe7d0a3befd93b2d04f')

SCHEMA = "dc20_candidate_natural_durable_evidence_v1"
MAX_FILE_BYTES = 8 * 1024 * 1024
# This one immutable training ledger is 4,764,361 gzip bytes and exactly
# 14,069,289 UTF-8 CSV bytes. Its persisted API envelope remains below 8 MiB.
ORIGINAL_HISTORY_GZIP_SHA256 = "7cabe48da6375106b22b2c08c17a7b11780861fed319496ee26761d20fa20a46"
ORIGINAL_HISTORY_EXPANDED_BYTES = 14_069_289
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_FILES = 64
MAX_CONTINUATION_FILES = 160
MAX_CONTINUATION_HTTP = 200
CONTINUATION_KIND = "ORIGINAL_GENERATION_REDEPLOYMENT"
GENERATION_ARCHIVE_SCHEMA = "dc20_original_generation_zip_bytes_v1"
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


PRE_BOUNDARY_CAPTURE_CONTRACT = ('1501a23e51d6e3867af2b1d457cd1690b88b38b750f9d1a50cb42521da4910bc', 'e550b2a59f1147785d9a89d704158b0ffb1fbd007464f8017119c292fbbbaac6')

# Capture/observer bytes already issued on D20261028 before this reader repair.
# They remain readable only with their original complete pair and date bound.
PRE_CONTINUATION_CAPTURE_CONTRACT = (
    "37122ef4efd86a138c15bfbc4e7487db957bca874a47e18d35688b8c64bc2c10",
    "0fd9e3be0640f19553ca4544552b4a250d6653ec2a047184e7fc798e93e22978")

def capture_contracts():
    return (LEGACY_CAPTURE_CONTRACT, PRE_ELIGIBLE_CAPTURE_CONTRACT, HISTORICAL_CAPTURE_CONTRACT, PRE_BOUNDARY_CAPTURE_CONTRACT, PRE_CONTINUATION_CAPTURE_CONTRACT, (SELF_SHA, PUBLICATION_SHA))


def code_guard():
    require(Path(publication.__file__).absolute() == ROOT / PUBLICATION_PATH, "EVIDENCE_PUBLICATION_IMPORT_CHANGED")
    raw, identity = gh.read(ROOT / PUBLICATION_PATH)
    require(sha(raw) == PUBLICATION_SHA, "EVIDENCE_PUBLICATION_CODE_CHANGED")
    own, own_identity = gh.read(Path(__file__).absolute())
    require(sha(own) == SELF_SHA, "EVIDENCE_CAPTURE_CODE_CHANGED")
    return identity, own_identity, publication.code_guard()


def _route(path):
    require(type(path) is str, "EXACT_OBSERVED_REPOSITORY_ROUTE_REQUIRED")
    if re.fullmatch(r"/repos/njedu2023-prog/(?:a-top10|a-share-top3-data)/git/commits/[0-9a-f]{40}", path):
        return path
    require(path.startswith(gh.API_PREFIX + "/"), "EXACT_OBSERVED_REPOSITORY_ROUTE_REQUIRED")
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


def _safe_body(raw, token=None, *, blob_response=False, continuation_blob=False):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_FILE_BYTES, "EVIDENCE_BODY_SIZE_LIMIT")
    require(not token or token.encode() not in raw, "RAW_CREDENTIAL_IN_EVIDENCE_REJECTED")
    value = gh.parse_json(raw)
    _safe_text(value, token)  # JSON escapes are decoded before checking secrets.
    if blob_response:
        require(value.get("encoding") == "base64" and type(value.get("content")) is str, "ORIGINAL_BLOB_ENVELOPE_REQUIRED")
        decoded = base64.b64decode(value["content"], validate=False)
        require(not token or token.encode() not in decoded, "BLOB_CREDENTIAL_IN_EVIDENCE_REJECTED")
        if continuation_blob:
            require(type(value.get("size")) is int and value["size"] == len(decoded)
                and value.get("sha") == gh.git_blob(decoded), "CAPTURED_CONTINUATION_BLOB_IDENTITY_CHANGED")
            continuation, _ = gh.continuation_dependencies()
            expected_model = continuation.ORIGINAL_PROMOTION_MODEL["artifact_sha256"]
            if sha(decoded) == expected_model:
                require(value.get("sha") == gh.git_blob(decoded)
                    == continuation.ORIGINAL_P0_CODE_PROFILE["models/decision_three_engines/promotion.joblib"]
                    and type(value.get("size")) is int and value["size"] == len(decoded),
                    "ORIGINAL_PINNED_MODEL_BLOB_REQUIRED")
                # Fixed artifact bytes are inspected as bytes only. Never
                # deserialize an untrusted pickle or execute a model here.
                _safe_text(decoded.decode("latin1"), token)
                return value
        if continuation_blob and decoded.startswith(b"\x1f\x8b"):
            # Registered original prediction history is gzip. Scan its one
            # bounded text member; the pair replay independently checks the
            # exact source path, Git blob and receipt binding below.
            original_history = sha(decoded) == gh.adapter.HISTORY_SHA == ORIGINAL_HISTORY_GZIP_SHA256
            expanded_limit = ORIGINAL_HISTORY_EXPANDED_BYTES if original_history else MAX_FILE_BYTES
            inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
            try:
                decoded = inflater.decompress(decoded, expanded_limit + 1)
            except zlib.error:
                raise ValueError("ORIGINAL_SOURCE_GZIP_INVALID") from None
            require(len(decoded) <= expanded_limit and inflater.eof
                and not inflater.unused_data and not inflater.unconsumed_tail
                and (not original_history or len(decoded) == ORIGINAL_HISTORY_EXPANDED_BYTES),
                "ORIGINAL_SOURCE_GZIP_BOUNDARY")
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
        require(len(self.records) <= MAX_CONTINUATION_HTTP, "OBSERVATION_HTTP_COUNT_LIMIT")
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


def _file_limit(manifest):
    kind = manifest.get("p0_provenance_kind")
    require(kind in (None, CONTINUATION_KIND), "UNKNOWN_P0_CAPSULE_PROVENANCE")
    return MAX_CONTINUATION_FILES if kind == CONTINUATION_KIND else MAX_FILES


class _ContinuationReplayClient:
    """Replay captured HTTP bytes only; this class cannot issue live authority."""
    continuation_replay = True

    def __init__(self, api, artifact, archive):
        self.api, self.artifact, self.archive = api, artifact, archive
        self.positions, self.calls = {}, 0

    def get_json(self, path):
        _route(path)
        self.calls += 1
        require(self.calls <= MAX_CONTINUATION_HTTP and path in self.api, "CONTINUATION_REPLAY_ROUTE_MISSING")
        position = self.positions.get(path, 0)
        values = self.api[path]
        require(position < len(values), "CONTINUATION_REPLAY_RESPONSE_EXHAUSTED")
        self.positions[path] = position + 1
        return gh.parse_json(gh.json_bytes(values[position]))

    def get_artifact_zip(self, artifact_id):
        self.calls += 2
        require(self.calls <= MAX_CONTINUATION_HTTP and artifact_id == self.artifact["id"],
            "CONTINUATION_REPLAY_ONLY_ORIGINAL_GENERATION_ARCHIVE")
        return self.archive


def _generation_archive_body(raw, artifact):
    value = _safe_body(raw)
    require(type(value) is dict and set(value) == {"schema_version", "artifact_id", "encoding",
        "archive_sha256", "archive_bytes", "body"}
        and value["schema_version"] == GENERATION_ARCHIVE_SCHEMA
        and type(value["artifact_id"]) is int and value["artifact_id"] == artifact["id"]
        and value["encoding"] == "base64" and value["archive_sha256"] == artifact["digest"][7:]
        and type(value["archive_bytes"]) is int and value["archive_bytes"] == artifact["size_in_bytes"]
        and type(value["body"]) is str, "ORIGINAL_GENERATION_ARCHIVE_ENVELOPE")
    try:
        archive = base64.b64decode(value["body"], validate=True)
    except (ValueError, base64.binascii.Error):
        raise ValueError("ORIGINAL_GENERATION_ARCHIVE_BASE64") from None
    require(base64.b64encode(archive).decode() == value["body"]
        and len(archive) == value["archive_bytes"] and sha(archive) == value["archive_sha256"],
        "ORIGINAL_GENERATION_ARCHIVE_BYTES_CHANGED")
    return archive


def _verify_continuation_materials(manifest, body, observation, api, archives):
    continuation, _ = gh.continuation_dependencies()
    proof = observation.get("p0_continuation")
    require(observation.get("p0_provenance_kind") == CONTINUATION_KIND and type(proof) is dict
        and proof.get("schema_version") == continuation.SCHEMA, "ORIGINAL_CONTINUATION_OBSERVATION_REQUIRED")
    original_id, reuse_id = (str(proof[k]["id"]) for k in ("original_generation_run", "successful_reuse_run"))
    original_path, reuse_path = (gh.API_PREFIX + "/actions/runs/" + x for x in (original_id, reuse_id))
    original, reused = api[original_path][0], api[reuse_path][0]
    generation = continuation.select_generation_artifact(gh,
        api[original_path + "/artifacts?per_page=100"][0], original, original_id)
    pages = gh.select_artifact(api[reuse_path + "/artifacts?per_page=100"][0], reused, reuse_id)
    require(manifest["generation_archive_evidence"] == generation == proof["original_generation_artifact"]
        and manifest["pages_archive_evidence"] == pages == proof["successful_reuse_pages_artifact"],
        "CONTINUATION_ARCHIVE_METADATA_CHANGED")
    require(set(archives) == {generation["id"], pages["id"], observation["publication_artifact"]["id"]},
        "THREE_EXACT_CONTINUATION_ARCHIVES_REQUIRED")
    for artifact in (generation, pages, observation["publication_artifact"]):
        recorded = archives[artifact["id"]]
        require(recorded["body_sha256"] == artifact["digest"][7:]
            and recorded["body_bytes"] == artifact["size_in_bytes"], "CONTINUATION_ARCHIVE_EXTERNAL_DIGEST_CHANGED")
    archive = _generation_archive_body(body(manifest["generation_archive_body"]), generation)
    revision_raw = body(manifest["pages_revision"])
    revision = _safe_body(revision_raw)
    require(gh.validate_revision(revision, reused, reuse_id) == (manifest["signal_date"], observation["exec_date"],
        observation["exit_date"], proof["successful_publication_git_sha"])
        and sha(revision_raw) == proof["successful_reuse_revision_sha256"], "CONTINUATION_CAPTURED_REVISION_CHANGED")
    replay = _ContinuationReplayClient(api, generation, archive)
    checked, _, checked_revision = continuation.verify_pair(gh, replay,
        original_run_id=original_id, original_cas_commit=observation["original_p0_commit_sha"], reuse_run_id=reuse_id,
        source_main_sha=proof["source_main_sha"], cached_reuse_revision=(revision, sha(revision_raw)))
    require(checked == proof and checked_revision == revision
        and int(original_id) == observation["original_p0_run_id"]
        and proof["source_file_bindings"] == observation["original_source_bindings"],
        "CONTINUATION_CAPTURED_PROVENANCE_REPLAY_CHANGED")


def verify_materials(manifest_raw, bodies, *, expected_manifest_sha256):
    """Pure byte integrity. This NEVER issues prospective/publication authority."""
    gh.exact_sha(expected_manifest_sha256)
    require(type(manifest_raw) is bytes and sha(manifest_raw) == expected_manifest_sha256, "EXTERNAL_EVIDENCE_MANIFEST_SHA_MISMATCH")
    manifest = _safe_body(manifest_raw)
    require(manifest.get("schema_version") == SCHEMA and manifest.get("status") == "LOCAL_UNPUBLISHED_OBSERVATION"
        and type(manifest.get("test_transport_injected")) is bool, "UNPUBLISHED_EVIDENCE_SCHEMA_REQUIRED")
    for key, expected in FLAGS.items():
        publication.natural.scorer._exact(manifest.get(key), expected, "LOCAL_EVIDENCE_CANNOT_GRANT_AUTHORITY")
    require((manifest.get("capture_code_sha256"), manifest.get("publication_verifier_sha256"))
        in capture_contracts(),
        "CAPTURE_CODE_CONTRACT_CHANGED")
    day = gh.date(manifest.get("signal_date"))
    require(day >= "20260914", "EVIDENCE_NATURAL_START_REQUIRED")
    if (manifest["capture_code_sha256"], manifest["publication_verifier_sha256"]) == PRE_BOUNDARY_CAPTURE_CONTRACT:
        require(day < "20261028", "PRE_BOUNDARY_CAPTURE_DATE_CHANGED")
    if (manifest["capture_code_sha256"], manifest["publication_verifier_sha256"]) == PRE_CONTINUATION_CAPTURE_CONTRACT:
        require(day <= "20261028", "PRE_CONTINUATION_CAPTURE_DATE_CHANGED")
    gh.exact_id(manifest.get("freeze_run_id")); gh.exact_sha(manifest.get("snapshot_file_sha256"))
    file_limit = _file_limit(manifest)
    continuation_kind = manifest.get("p0_provenance_kind") == CONTINUATION_KIND
    require(not continuation_kind or (manifest["capture_code_sha256"], manifest["publication_verifier_sha256"])
        in ((SELF_SHA, PUBLICATION_SHA), PRE_BOUNDARY_CAPTURE_CONTRACT, PRE_CONTINUATION_CAPTURE_CONTRACT),
        "CONTINUATION_REQUIRES_REVIEWED_CAPTURE_PROFILE")
    require(type(bodies) is dict and type(manifest.get("files")) is list and len(manifest["files"]) < file_limit,
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
        and observation.get("verifier_sha256") == manifest["publication_verifier_sha256"]
        and observation.get("research_prospective_publication_observed") is True
        and observation.get("injected_client_for_test") is False, "ORIGINAL_PUBLICATION_OBSERVATION_CHANGED")
    records = manifest.get("http_observations")
    http_limit = MAX_CONTINUATION_HTTP if continuation_kind else gh.MAX_API_CALLS
    require(type(records) is list and 0 < len(records) <= http_limit, "BOUNDED_ORIGINAL_HTTP_OBSERVATIONS_REQUIRED")
    redirects, archives, api = [], {}, {}
    for ordinal, record in enumerate(records, 1):
        require(type(record) is dict and set(record) == {"ordinal", "kind", "api_path", "artifact_id", "status", "body_bytes", "body_sha256", "retained_body"}
            and type(record["ordinal"]) is int and record["ordinal"] == ordinal, "ORIGINAL_HTTP_RECORD_SHAPE")
        kind = record["kind"]
        require(type(record["status"]) is int and type(record["body_bytes"]) is int, "EXACT_HTTP_COUNTER_TYPES_REQUIRED")
        if kind == "API_JSON":
            _route(record["api_path"])
            require(continuation_kind or record["api_path"].startswith(gh.API_PREFIX + "/"),
                "SINGLE_RUN_CAPSULE_EXTERNAL_ROUTE_REJECTED")
            require(record["artifact_id"] is None and record["status"] == 200 and type(record["status"]) is int,
                "SUCCESSFUL_JSON_OBSERVATION_REQUIRED")
            raw = body(record["retained_body"])
            require(record["body_sha256"] == sha(raw) and record["body_bytes"] == len(raw), "ORIGINAL_HTTP_BODY_BINDING_CHANGED")
            api.setdefault(record["api_path"], []).append(_safe_body(raw, blob_response="/git/blobs/" in record["api_path"], continuation_blob=continuation_kind))
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
    require(len(archives) == (3 if continuation_kind else 2) and sorted(redirects) == sorted(archives),
        "EXACT_TYPED_ORIGINAL_ARCHIVE_OBSERVATIONS_REQUIRED")
    if continuation_kind:
        _verify_continuation_materials(manifest, body, observation, api, archives)
    else:
        require(not {"generation_archive_evidence", "generation_archive_body"}.intersection(manifest)
            and "p0_continuation" not in observation and "p0_provenance_kind" not in observation,
            "SINGLE_RUN_CAPSULE_CANNOT_HIDE_CONTINUATION")
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
    bindings = manifest["original_source_bindings"]
    count = 28 + sum(b.get("path") == gh.adapter.SUSPENSION_PATH for b in bindings if type(b) is dict)
    if "boundary_registration" in manifest:
        envelope = _safe_body(body(manifest["boundary_registration"]))
        require(set(envelope) == {"receipt_raw", "contract_raw"}, "BOUNDARY_REGISTRATION_SHAPE")
        receipt_raw, contract_raw = envelope["receipt_raw"].encode(), envelope["contract_raw"].encode()
        receipt, contract = gh.parse_json(receipt_raw), gh.parse_json(contract_raw)
        registered, _, _, _ = gh.adapter._registration(receipt, contract, day)
        require(any(item.get("table") == gh.adapter.BOUNDARY_TABLE
            for item in receipt["inputs"]["runtime_consumed_market_files"]), "TYPED_BOUNDARY_REGISTRATION_REQUIRED")
        paths = gh.adapter._p0_paths(day)
        wanted = set(registered) | set(paths.values())
        require({b["path"] for b in bindings} == wanted, "BOUNDARY_REGISTERED_BINDINGS_CHANGED")
        indexed = {b["path"]: b for b in bindings}
        require(all(indexed[p]["sha256"] == digest for p, digest in registered.items())
            and indexed[paths["receipt"]]["sha256"] == sha(receipt_raw)
            and indexed[paths["three_rank_json"]]["sha256"] == sha(contract_raw),
            "BOUNDARY_RECEIPT_OR_SOURCE_DIGEST_CHANGED")
        count = len(wanted)
    require(bindings == observation["original_source_bindings"] and len(bindings) == count,
        "ORIGINAL_SOURCE_GIT_BINDINGS_CHANGED")
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
    continuation_kind = observed.get("p0_provenance_kind") == CONTINUATION_KIND
    file_limit = MAX_CONTINUATION_FILES if continuation_kind else MAX_FILES
    bodies = {}
    def retain(raw, *, blob_response=False):
        _safe_body(raw, token, blob_response=blob_response, continuation_blob=continuation_kind)
        ref = _ref(raw); bodies[ref["path"]] = raw
        require(len(bodies) < file_limit and sum(map(len, bodies.values())) <= MAX_TOTAL_BYTES, "BOUNDED_PERSISTED_EVIDENCE_REQUIRED")
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
    continuation_fields = {}
    p0_id = str(observed["p0_continuation"]["successful_reuse_run"]["id"] if continuation_kind
        else observed["original_p0_run_id"])
    p0_run = api[gh.API_PREFIX + "/actions/runs/" + p0_id][0]
    pages = gh.select_artifact(api[gh.API_PREFIX + "/actions/runs/" + p0_id + "/artifacts?per_page=100"][0], p0_run, p0_id)
    revision = retain(_revision(zip_bytes[pages["id"]]))
    if continuation_kind:
        generation = observed["p0_continuation"]["original_generation_artifact"]
        original_zip = zip_bytes[generation["id"]]
        require(not token or token.encode() not in original_zip, "CREDENTIAL_IN_GENERATION_ARCHIVE_REJECTED")
        envelope = gh.json_bytes({"schema_version": GENERATION_ARCHIVE_SCHEMA, "artifact_id": generation["id"],
            "encoding": "base64", "archive_sha256": sha(original_zip), "archive_bytes": len(original_zip),
            "body": base64.b64encode(original_zip).decode()})
        _generation_archive_body(envelope, generation)
        continuation_fields = {"p0_provenance_kind": CONTINUATION_KIND,
            "generation_archive_evidence": generation, "generation_archive_body": retain(envelope),
            "original_generation_archive_bytes_persisted": True}
    boundary_fields = {}
    original = publication.read_archive(zip_bytes[observed["publication_artifact"]["id"]])["sources"]
    paths = gh.adapter._p0_paths(observed["signal_date"])
    original_receipt = gh.parse_json(original[paths["receipt"]])
    if any(item.get("table") == gh.adapter.BOUNDARY_TABLE
           for item in original_receipt["inputs"]["runtime_consumed_market_files"]):
        boundary_fields["boundary_registration"] = retain(gh.json_bytes({
            "receipt_raw": original[paths["receipt"]].decode(),
            "contract_raw": original[paths["three_rank_json"]].decode()}))
    native_ref = retain(gh.json_bytes(observed))
    manifest = {"schema_version": SCHEMA, "status": "LOCAL_UNPUBLISHED_OBSERVATION", "signal_date": observed["signal_date"],
        "freeze_run_id": freeze_run_id, "snapshot_file_sha256": observed["snapshot_file_sha256"], "native_observation": native_ref,
        "pages_archive_evidence": pages, "pages_revision": revision, "original_source_bindings": observed["original_source_bindings"],
        "http_observations": observations, "files": [_ref(raw) for path, raw in sorted(bodies.items())],
        "capture_code_sha256": SELF_SHA, "publication_verifier_sha256": PUBLICATION_SHA,
        "test_transport_injected": transport is not None, "large_zip_bodies_persisted": False,
        "headers_or_signed_redirect_urls_persisted": False,
        **continuation_fields, **boundary_fields,
        "trust_boundary": "REQUIRES_INDEPENDENT_OBSERVER_RUN_JOB_AND_GIT_MANIFEST_BINDING_FOR_CROSS_DAY_ADMISSION", **FLAGS}
    manifest_raw = gh.json_bytes(manifest)
    verify_materials(manifest_raw, bodies, expected_manifest_sha256=sha(manifest_raw))
    require(len(manifest_raw) <= MAX_FILE_BYTES and len(bodies) + 1 <= file_limit
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
