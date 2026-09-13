"""Read-only GitHub P0 original-byte importer, never a candidate publisher.

Only an explicitly supplied successful P0 run is considered. Its own immutable
Pages artifact identifies a Git commit; the current public site is never read.
The 28 files are copied verbatim into a fresh source directory. This establishes
API-observed provenance and byte identity, NOT new prediction timing, complete
feature admission, model authority or permission to trade. No market API/fit.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile


CODE_ROOT = Path(__file__).absolute().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(CODE_ROOT / "src"))
    sys.path.insert(0, str(CODE_ROOT))

SCHEMA = "dc20_natural_candidate_p0_github_import_20260914_v1"
REPOSITORY = "njedu2023-prog/DC20"
API_PREFIX = f"/repos/{REPOSITORY}"
WORKFLOW_ID = 343703608
WORKFLOW_NAME = "DC2.0 · Publish Primary D List (P0)"
WORKFLOW_PATH = ".github/workflows/run_primary_d_daily.yml"
ADAPTER_PATH = "work/profit_1000_upgrade/candidate_d_source_adapter.py"
ADAPTER_SHA = "bb9b32de5be84b55793d8e8c19dd6960727e90de83605c4f5a1c360dbd3d982e"
TOKEN_ENV = "DC20_CANDIDATE_GITHUB_TOKEN"
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_JSON_BYTES = 88 * 1024 * 1024
MAX_ZIP_BYTES = 128 * 1024 * 1024
MAX_TAR_BYTES = 512 * 1024 * 1024
MAX_REVISION_BYTES = 1024 * 1024
MAX_ENTRIES = 20000
MAX_API_CALLS = 50
SOCKET_TIMEOUT_SECONDS = 20
CRITICAL_JOBS = (
    "Build immutable promotion-only D candidate",
    "CAS publish one primary D commit",
    "Deploy exact primary D revision / deploy",
)


class ImportBlocked(ValueError):
    """Import not accepted; a partial fresh directory is not usable evidence."""


def require(ok, reason):
    if not ok:
        raise ImportBlocked(reason)


def sha256(body):
    return hashlib.sha256(body).hexdigest()


def git_blob(body):
    return hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()


def exact_sha(value, width=64):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{%d}" % width, value), "INVALID_SHA")
    return value


def exact_id(value):
    require(type(value) is str and re.fullmatch(r"[1-9][0-9]*", value), "EXPLICIT_RUN_ID_REQUIRED")
    return value


def date(value):
    require(type(value) is str and re.fullmatch(r"[0-9]{8}", value), "INVALID_DATE")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError:
        raise ImportBlocked("INVALID_DATE") from None
    return value


def utc(value):
    require(type(value) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,7})?Z", value),
        "EXACT_UTC_TIMESTAMP_REQUIRED")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ImportBlocked("EXACT_UTC_TIMESTAMP_REQUIRED") from None


def relative(value):
    require(type(value) is str and value and "\\" not in value and "\0" not in value
        and not any(ord(c) < 32 for c in value) and not value.startswith("/")
        and PurePosixPath(value).as_posix() == value
        and all(part not in ("", ".", "..") for part in value.split("/")), "UNSAFE_RELATIVE_PATH")
    return value


def path(value, *, directory=False, exists=True):
    require(isinstance(value, (str, Path)), "ABSOLUTE_PATH_REQUIRED")
    p = Path(value)
    require(p.is_absolute() and ".." not in p.parts
        and not any(q.is_symlink() for q in (p, *p.parents)), "ABSOLUTE_UNALIASED_PATH_REQUIRED")
    if exists:
        require(p.is_dir() if directory else p.is_file(), "MISSING_PATH")
        if not directory:
            s = p.stat()
            require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1, "REGULAR_SINGLE_LINK_FILE_REQUIRED")
    return p


def fingerprint(p):
    s = p.stat()
    return (s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def read(p, limit=MAX_SOURCE_BYTES):
    p = path(p)
    before = fingerprint(p)
    require(before[4] <= limit, "LOCAL_FILE_SIZE_LIMIT")
    with p.open("rb") as f:
        body = f.read(limit + 1)
    require(len(body) == before[4] and fingerprint(path(p)) == before, "LOCAL_FILE_CHANGED_DURING_READ")
    return body, before


def object_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def parse_json(body):
    require(type(body) is bytes and len(body) <= MAX_JSON_BYTES, "JSON_SIZE_LIMIT")
    try:
        result = json.loads(body, object_pairs_hook=object_pairs,
            parse_constant=lambda _: require(False, "NONFINITE_JSON"))
    except (UnicodeError, json.JSONDecodeError):
        raise ImportBlocked("INVALID_JSON") from None
    require(type(result) is dict, "JSON_OBJECT_REQUIRED")
    return result


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


SELF_SHA = sha256(read(Path(__file__).absolute())[0])
require(sha256(read(CODE_ROOT / ADAPTER_PATH)[0]) == ADAPTER_SHA, "FROZEN_ADAPTER_CHANGED")
from work.profit_1000_upgrade import candidate_d_source_adapter as adapter


def code_guard():
    require(Path(adapter.__file__).absolute() == CODE_ROOT / ADAPTER_PATH, "ADAPTER_IMPORT_ORIGIN_CHANGED")
    own, own_stat = read(Path(__file__).absolute())
    dep, dep_stat = read(CODE_ROOT / ADAPTER_PATH)
    require(sha256(own) == SELF_SHA and sha256(dep) == ADAPTER_SHA, "FROZEN_IMPORT_CODE_CHANGED")
    checker = adapter.publisher.build_primary_d_runtime_index
    require(getattr(checker, "__module__", None) == "scripts.publish_primary_three_rank"
        and getattr(getattr(checker, "__code__", None), "co_filename", None)
            == str(CODE_ROOT / "scripts/publish_primary_three_rank.py"), "P0_CHECKER_IMPORT_ORIGIN_CHANGED")
    return own_stat, dep_stat, adapter._code_guard()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubReadClient:
    """GET only. Token is sent to api.github.com, never the artifact CDN.

    Twenty seconds is a socket timeout, NOT an absolute response deadline.
    The parent workflow owns its independent outer wall-clock budget.
    """
    def __init__(self, token):
        require(type(token) is str and token and not any(c.isspace() for c in token), "MISSING_DEDICATED_GITHUB_TOKEN")
        self._token = token
        self._opener = urllib.request.build_opener(NoRedirect())
        self.calls = 0

    def _open(self, url, *, authorization, limit, allow_302=False):
        self.calls += 1
        require(self.calls <= MAX_API_CALLS, "GET_BUDGET_EXCEEDED")
        parsed = urllib.parse.urlsplit(url)
        require(parsed.scheme == "https" and parsed.username is None and parsed.password is None
            and not parsed.fragment and parsed.port in (None, 443), "UNSAFE_GET_URL")
        if authorization:
            require(parsed.hostname == "api.github.com", "AUTHORIZATION_HOST_REJECTED")
        else:
            host = parsed.hostname or ""
            require(any(host.endswith(suffix) and host != suffix[1:]
                for suffix in (".blob.core.windows.net", ".actions.githubusercontent.com")), "ARTIFACT_DOWNLOAD_HOST_REJECTED")
        headers = {"User-Agent": "DC20-research-p0-source-import", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}
        if authorization:
            headers["Authorization"] = "Bearer " + self._token
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with self._opener.open(request, timeout=SOCKET_TIMEOUT_SECONDS) as response:
                require(response.status == 200, "UNEXPECTED_HTTP_STATUS")
                body = response.read(limit + 1)
                require(len(body) <= limit and self._token.encode() not in body, "RESPONSE_SIZE_OR_CREDENTIAL_REJECTED")
                return body
        except urllib.error.HTTPError as exc:
            if allow_302 and exc.code == 302:
                location = exc.headers.get("Location")
                require(type(location) is str and self._token not in location, "INVALID_ARTIFACT_REDIRECT")
                return location
            raise ImportBlocked("GITHUB_GET_FAILED") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ImportBlocked("GITHUB_GET_FAILED") from None

    def get_json(self, api_path):
        require(type(api_path) is str and api_path.startswith(API_PREFIX + "/")
            and "#" not in api_path and "\\" not in api_path and ".." not in api_path
            and "@" not in api_path, "EXACT_REPOSITORY_API_PATH_REQUIRED")
        return parse_json(self._open("https://api.github.com" + api_path,
            authorization=True, limit=MAX_JSON_BYTES))

    def get_artifact_zip(self, artifact_id):
        require(type(artifact_id) is int and artifact_id > 0, "EXACT_ARTIFACT_ID_REQUIRED")
        redirect = self._open(f"https://api.github.com{API_PREFIX}/actions/artifacts/{artifact_id}/zip",
            authorization=True, limit=MAX_ZIP_BYTES, allow_302=True)
        require(type(redirect) is str, "ARTIFACT_302_REQUIRED")
        return self._open(redirect, authorization=False, limit=MAX_ZIP_BYTES)


def revision_from_zip(body):
    require(type(body) is bytes and len(body) <= MAX_ZIP_BYTES, "ARTIFACT_ZIP_SIZE_LIMIT")
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            entries = archive.infolist()
            require(len(entries) == 1 and entries[0].filename == "artifact.tar", "EXACT_PAGES_ARTIFACT_TAR_REQUIRED")
            entry = entries[0]
            mode = entry.external_attr >> 16
            require(not entry.is_dir() and not entry.flag_bits & 1 and not stat.S_ISLNK(mode)
                and (stat.S_IFMT(mode) in (0, stat.S_IFREG))
                and entry.file_size <= MAX_TAR_BYTES, "UNSAFE_PAGES_ZIP_ENTRY")
            with archive.open(entry) as stream:
                tar_body = stream.read(MAX_TAR_BYTES + 1)
            require(len(tar_body) == entry.file_size, "PAGES_TAR_SIZE_MISMATCH")  # Reading also verifies ZIP CRC.
        revision, seen, files, total, count = None, set(), set(), 0, 0
        with tarfile.open(fileobj=io.BytesIO(tar_body), mode="r:") as archive:
            for entry in archive:
                count += 1
                require(count <= MAX_ENTRIES, "PAGES_TAR_ENTRY_LIMIT")
                name = entry.name[2:] if entry.name.startswith("./") else entry.name
                if name in ("", "."):
                    require(entry.isdir() and name not in seen, "INVALID_TAR_ROOT")
                    seen.add(name)
                    continue
                name = relative(name.rstrip("/") if entry.isdir() else name)
                require(name not in seen and (entry.isfile() or entry.isdir()) and not entry.issparse()
                    and not entry.linkname, "UNSAFE_OR_DUPLICATE_TAR_ENTRY")
                require(not any(str(parent) in files for parent in PurePosixPath(name).parents)
                    and (not entry.isfile() or not any(old.startswith(name + "/") for old in seen)),
                    "TAR_FILE_DIRECTORY_COLLISION")
                seen.add(name)
                if entry.isfile(): files.add(name)
                total += entry.size
                require(entry.size >= 0 and total <= MAX_TAR_BYTES, "PAGES_TAR_EXPANSION_LIMIT")
                if name == "revision.json":
                    require(entry.isfile() and entry.size <= MAX_REVISION_BYTES, "INVALID_REVISION_ENTRY")
                    stream = archive.extractfile(entry)
                    require(stream is not None, "MISSING_REVISION_STREAM")
                    revision = stream.read(MAX_REVISION_BYTES + 1)
                    require(len(revision) == entry.size, "REVISION_SIZE_MISMATCH")
        require(revision is not None, "SAME_RUN_REVISION_MISSING")
        return parse_json(revision), sha256(revision)
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, UnicodeError):
        raise ImportBlocked("INVALID_PAGES_ARCHIVE") from None


def validate_run(run, run_id):
    require(type(run) is dict and type(run.get("id")) is int and run["id"] == int(run_id)
        and type(run.get("workflow_id")) is int and run["workflow_id"] == WORKFLOW_ID
        and run.get("path") == WORKFLOW_PATH and run.get("head_branch") == "main"
        and type(run.get("run_attempt")) is int and run["run_attempt"] == 1
        and run.get("status") == "completed" and run.get("conclusion") == "success"
        and run.get("repository", {}).get("full_name") == REPOSITORY
        and run.get("head_repository", {}).get("full_name") == REPOSITORY,
        "EXACT_SUCCESSFUL_P0_RUN_REQUIRED")
    exact_sha(run.get("head_sha"), 40)
    require(run.get("event") in ("schedule", "workflow_run", "workflow_dispatch"), "P0_RUN_EVENT_REJECTED")
    if run["event"] != "workflow_dispatch":
        require(run.get("name") == WORKFLOW_NAME, "P0_RUN_NAME_REJECTED")
    else:
        require(type(run.get("display_title")) is str and run.get("name") == run["display_title"]
            and re.fullmatch(r"DC20 controlled daily NATURAL \| D=[0-9]{8}", run["display_title"]),
            "CONTROLLED_NATURAL_DISPATCH_REQUIRED")
    created, started, updated = (utc(run.get(k)) for k in ("created_at", "run_started_at", "updated_at"))
    require(created <= started <= updated, "P0_RUN_TIME_ORDER_INVALID")
    result = {k: run[k] for k in ("id", "workflow_id", "path", "head_branch", "head_sha", "run_attempt",
        "status", "conclusion", "event", "name", "created_at", "run_started_at", "updated_at")}
    if run["event"] == "workflow_dispatch": result["display_title"] = run["display_title"]
    return result


def validate_jobs(document, run, run_id):
    jobs = document.get("jobs")
    require(type(jobs) is list and type(document.get("total_count")) is int
        and document["total_count"] == len(jobs) <= 100, "COMPLETE_JOB_LIST_REQUIRED")
    selected, ids = {}, set()
    for job in jobs:
        require(type(job) is dict and type(job.get("id")) is int and job["id"] > 0 and job["id"] not in ids
            and type(job.get("run_id")) is int and job["run_id"] == int(run_id), "JOB_IDENTITY_MISMATCH")
        ids.add(job["id"])
        name = job.get("name")
        if name not in CRITICAL_JOBS:
            continue
        require(name not in selected and job.get("status") == "completed" and job.get("conclusion") == "success",
            "COMPUTE_CAS_DEPLOY_ALL_SUCCESS_REQUIRED")
        require(utc(run["run_started_at"]) <= utc(job.get("started_at")) <= utc(job.get("completed_at"))
            <= utc(run["updated_at"]), "CRITICAL_JOB_TIME_ORDER_INVALID")
        selected[name] = {k: job[k] for k in ("id", "run_id", "name", "status", "conclusion", "started_at", "completed_at")}
    require(set(selected) == set(CRITICAL_JOBS), "CRITICAL_JOB_MISSING")
    ordered = [selected[name] for name in CRITICAL_JOBS]
    require(all(utc(ordered[i]["completed_at"]) <= utc(ordered[i+1]["started_at"]) for i in (0, 1)),
        "COMPUTE_CAS_DEPLOY_SEQUENCE_INVALID")
    return ordered


def select_artifact(document, run, run_id):
    entries = document.get("artifacts")
    require(type(entries) is list and type(document.get("total_count")) is int
        and document["total_count"] == len(entries) <= 100 and all(type(e) is dict for e in entries),
        "COMPLETE_ARTIFACT_LIST_REQUIRED")
    candidates = [e for e in entries if e.get("name") == "github-pages"]
    require(len(candidates) == 1, "UNIQUE_SAME_RUN_PAGES_ARTIFACT_REQUIRED")
    artifact = candidates[0]
    origin = artifact.get("workflow_run")
    require(type(artifact.get("id")) is int and artifact["id"] > 0 and artifact.get("expired") is False
        and type(artifact.get("size_in_bytes")) is int and 0 < artifact["size_in_bytes"] <= MAX_ZIP_BYTES
        and type(origin) is dict and type(origin.get("id")) is int and origin["id"] == int(run_id)
        and origin.get("head_branch") == "main" and origin.get("head_sha") == run["head_sha"], "ARTIFACT_ORIGIN_INVALID")
    digest = artifact.get("digest")
    require(type(digest) is str and digest.startswith("sha256:"), "ARTIFACT_EXTERNAL_SHA_REQUIRED")
    exact_sha(digest[7:])
    require(utc(run["run_started_at"]) <= utc(artifact.get("created_at")) <= utc(artifact.get("updated_at"))
        <= utc(run["updated_at"]), "ARTIFACT_TIME_ORDER_INVALID")
    return {k: artifact[k] for k in ("id", "name", "size_in_bytes", "digest", "expired", "created_at", "updated_at")}


def validate_revision(revision, run, run_id):
    require(revision.get("schema_version") == "decision_pages_revision_v4_primary_first"
        and revision.get("repository") == REPOSITORY and revision.get("branch") == "main"
        and type(revision.get("run_id")) is int and revision["run_id"] == int(run_id)
        and type(revision.get("run_attempt")) is int and revision["run_attempt"] == 1
        and revision.get("workflow") == WORKFLOW_NAME and revision.get("event_name") == run["event"]
        and revision.get("primary_d_status") == "READY" and revision.get("primary_d_generation_mode") == "NATURAL",
        "SAME_RUN_NATURAL_P0_REVISION_REQUIRED")
    day = date(revision.get("primary_d_signal_date"))
    trade, exit_day = date(revision.get("primary_d_exec_date")), date(revision.get("primary_d_exit_date"))
    require(day >= "20260914" and day < trade < exit_day and revision.get("signal_date") == day,
        "NATURAL_FORWARD_D_NOT_REDATED_REQUIRED")
    require(revision.get("primary_d_receipt_url") == adapter._p0_paths(day)["receipt"], "EXACT_D_RECEIPT_URL_REQUIRED")
    exact_sha(revision.get("head_sha"), 40)
    exact_sha(revision.get("primary_d_bundle_sha256"))
    require(type(revision.get("primary_d_top10_count")) is int and 0 <= revision["primary_d_top10_count"] <= 10,
        "INVALID_TOPN_COUNT")
    if run["event"] == "workflow_dispatch":
        require(run.get("name") == run.get("display_title") == f"DC20 controlled daily NATURAL | D={day}",
            "CONTROLLED_NATURAL_DISPATCH_REQUIRED")
    return day, trade, exit_day, revision["head_sha"]


def validate_tree(document, expected_sha):
    require(document.get("sha") == expected_sha and document.get("truncated") is False
        and type(document.get("tree")) is list and 0 < len(document["tree"]) <= MAX_ENTRIES, "COMPLETE_GIT_TREE_REQUIRED")
    entries, children = {}, {}
    for item in document["tree"]:
        require(type(item) is dict, "INVALID_GIT_TREE_ENTRY")
        name = relative(item.get("path"))
        require(name not in entries and item.get("type") in ("blob", "tree", "commit"), "DUPLICATE_OR_INVALID_TREE_ENTRY")
        exact_sha(item.get("sha"), 40)
        require(item.get("mode") in {"blob": ("100644", "100755", "120000"), "tree": ("040000",),
            "commit": ("160000",)}[item["type"]], "INVALID_GIT_MODE")
        entries[name] = item
        parent = str(PurePosixPath(name).parent)
        parent = "" if parent == "." else parent
        children.setdefault(parent, []).append(item)
    for parent, values in children.items():
        require(not parent or parent in entries and entries[parent]["type"] == "tree", "GIT_TREE_PARENT_MISSING")
        values.sort(key=lambda x: (PurePosixPath(x["path"]).name + ("/" if x["type"] == "tree" else "")).encode())
        body = b"".join(x["mode"].lstrip("0").encode() + b" " + PurePosixPath(x["path"]).name.encode()
            + b"\0" + bytes.fromhex(x["sha"]) for x in values)
        observed = hashlib.sha1(b"tree " + str(len(body)).encode() + b"\0" + body).hexdigest()
        require(observed == (entries[parent]["sha"] if parent else expected_sha), "GIT_TREE_OBJECT_SHA_MISMATCH")
    return entries


def load_blob(client, entries, logical):
    require(logical in entries, "REGISTERED_GIT_BLOB_MISSING")
    node = entries[logical]
    require(node["type"] == "blob" and node["mode"] == "100644" and type(node.get("size")) is int
        and 0 <= node["size"] <= MAX_SOURCE_BYTES, "REGULAR_BOUNDED_ORIGINAL_BLOB_REQUIRED")
    response = client.get_json(f"{API_PREFIX}/git/blobs/{node['sha']}")
    require(response.get("sha") == node["sha"] and response.get("encoding") == "base64"
        and type(response.get("size")) is int and response["size"] == node["size"]
        and type(response.get("content")) is str, "GIT_BLOB_ENVELOPE_MISMATCH")
    encoded = response["content"]
    require(len(encoded) <= 2 * MAX_SOURCE_BYTES and re.fullmatch(r"[A-Za-z0-9+/=\r\n]*", encoded), "INVALID_BASE64_BLOB")
    try:
        body = base64.b64decode(encoded.replace("\n", "").replace("\r", ""), validate=True)
    except ValueError:
        raise ImportBlocked("INVALID_BASE64_BLOB") from None
    require(len(body) == node["size"] and git_blob(body) == node["sha"], "ORIGINAL_GIT_BLOB_SHA_MISMATCH")
    return body


def _index(root, day):
    paths = {k: root / v for k, v in adapter._p0_paths(day).items()}
    return adapter.publisher.build_primary_d_runtime_index(root, receipt_path=paths["receipt"],
        runtime_path=paths["runtime_features"], three_rank_json_path=paths["three_rank_json"], three_rank_csv_path=paths["three_rank_csv"])


def inventory(root, expected):
    path(root, directory=True)
    actual, directories = set(), set()
    allowed_dirs = {str(PurePosixPath(name).parent) for name in expected}
    allowed_dirs = {str(p) for name in allowed_dirs for p in (PurePosixPath(name), *PurePosixPath(name).parents) if str(p) != "."}
    pending = [root]
    while pending:
        directory = pending.pop()
        path(directory, directory=True)
        with os.scandir(directory) as stream:
            entries = sorted(stream, key=lambda item: item.name)
        for entry in entries:
            require(not entry.is_symlink(), "SOURCE_SYMLINK_REJECTED")
            p = directory / entry.name
            rel = p.relative_to(root).as_posix()
            if entry.is_dir(follow_symlinks=False):
                # Unknown directories are rejected before any recursive scan.
                require(rel in allowed_dirs, "UNREGISTERED_SOURCE_DIRECTORY")
                directories.add(rel)
                pending.append(p)
            else:
                # In particular, never read/hash an injected outcome file.
                require(rel in expected, "UNREGISTERED_SOURCE_FILE")
                body, identity = read(p)
                require(sha256(body) == expected[rel]["sha256"] and len(body) == expected[rel]["bytes"]
                    and git_blob(body) == expected[rel]["git_blob_sha1"]
                    and ("identity" not in expected[rel] or identity == expected[rel]["identity"]), "SOURCE_INVENTORY_OR_BYTES_CHANGED")
                actual.add(rel)
    require(actual == set(expected) and directories == allowed_dirs, "EXACT_IMPORTED_INVENTORY_REQUIRED")


def write_new(root, logical, body):
    """Exclusive no-follow writes through directory descriptors, never extractall."""
    parts = relative(logical).split("/")
    require(hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY"), "NOFOLLOW_DIRECTORY_SUPPORT_REQUIRED")
    root_stat = fingerprint(path(root, directory=True))[:3]
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        s = os.fstat(fd)
        require((s.st_dev, s.st_ino, s.st_mode) == root_stat, "WRITE_ROOT_CHANGED")
        for part in parts[:-1]:
            try: os.mkdir(part, mode=0o700, dir_fd=fd)
            except FileExistsError: pass
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        target_fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(target_fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)


def import_p0_sources(source_root, *, expected_run_id, github_client):
    """Import exact published bytes. Injected clients are explicitly test-only.

    The returned ordinary dictionary is NOT a source-authority object. Call the
    pinned source adapter/runner separately; neither a score nor a new freeze is
    issued here. Failure may leave partial files in this formerly empty root.
    """
    root = path(source_root, directory=True)
    # Directory link count/mtime legitimately change while creating descendants.
    root_identity = fingerprint(root)[:3]
    require(not any(root.iterdir()), "EMPTY_NEW_SOURCE_ROOT_REQUIRED")
    run_id = exact_id(expected_run_id)
    code_before = code_guard()
    run_path = f"{API_PREFIX}/actions/runs/{run_id}"
    job_path = run_path + "/attempts/1/jobs?per_page=100"
    artifacts_path = run_path + "/artifacts?per_page=100"
    run = github_client.get_json(run_path)
    run_bound = validate_run(run, run_id)
    jobs = validate_jobs(github_client.get_json(job_path), run, run_id)
    artifact = select_artifact(github_client.get_json(artifacts_path), run, run_id)
    archive = github_client.get_artifact_zip(artifact["id"])
    require(type(archive) is bytes and len(archive) == artifact["size_in_bytes"]
        and sha256(archive) == artifact["digest"][7:], "ARTIFACT_SIZE_OR_EXTERNAL_DIGEST_MISMATCH")
    revision, revision_sha = revision_from_zip(archive)
    day, trade, exit_day, published = validate_revision(revision, run, run_id)
    commit = github_client.get_json(f"{API_PREFIX}/git/commits/{published}")
    require(commit.get("sha") == published and type(commit.get("tree")) is dict
        and type(commit.get("parents")) is list and len(commit["parents"]) == 1, "EXACT_SINGLE_PARENT_PUBLISHED_COMMIT_REQUIRED")
    tree_sha = exact_sha(commit["tree"].get("sha"), 40)
    entries = validate_tree(github_client.get_json(f"{API_PREFIX}/git/trees/{tree_sha}?recursive=1"), tree_sha)
    paths = adapter._p0_paths(day)
    sources = {logical: load_blob(github_client, entries, logical) for logical in paths.values()}
    receipt, contract = parse_json(sources[paths["receipt"]]), parse_json(sources[paths["three_rank_json"]])
    require(receipt.get("generation_mode") == "NATURAL" and receipt.get("primary_status") == "READY"
        and receipt.get("prospective") is True and receipt.get("forward_eligible") is True
        and receipt.get("not_forward_generated") is False and receipt.get("future_market_data_consumed") is False
        and receipt.get("latest_fallback_used") is False and receipt.get("action_authorized") is False
        and receipt.get("action_input_consumed") is False and type(receipt.get("formal_trade_count")) is int
        and receipt["formal_trade_count"] == 0, "NATURAL_P0_RECEIPT_BOUNDARY_REQUIRED")
    require(receipt.get("signal_date") == contract.get("signal_date") == day
        and receipt.get("exec_date") == contract.get("exec_date") == trade
        and receipt.get("exit_date") == contract.get("exit_date") == exit_day
        and contract.get("bundle_sha256") == revision["primary_d_bundle_sha256"]
        and type(contract.get("top10_count")) is int and contract["top10_count"] == revision["primary_d_top10_count"],
        "P0_REVISION_RECEIPT_CONTRACT_MISMATCH")
    require(commit["parents"][0].get("sha") == exact_sha(receipt["inputs"].get("git_head"), 40), "PUBLISHED_PARENT_NOT_RECEIPT_BASE")
    registered, daily, _, _ = adapter._registration(receipt, contract, day)
    for logical, expected in registered.items():
        body = load_blob(github_client, entries, logical)
        require(sha256(body) == expected, "RECEIPT_ORIGINAL_SOURCE_SHA_MISMATCH")
        sources[logical] = body
    require(len(sources) == 28, "EXACT_28_ORIGINAL_FILES_REQUIRED")
    for item in daily:
        if item["trade_date"] != day:
            require(entries[item["path"]]["sha"] == item["git_blob_sha1"], "RECEIPT_HISTORICAL_GIT_BLOB_MISMATCH")
    calendar = receipt["inputs"]["calendar"]
    require(calendar.get("git_mode") == "100644" and calendar.get("git_blob_sha1") == entries[adapter.CALENDAR_PATH]["sha"],
        "RECEIPT_CALENDAR_GIT_BLOB_MISMATCH")
    require(path(root, directory=True) == root and fingerprint(root)[:3] == root_identity and not any(root.iterdir()),
        "SOURCE_ROOT_CHANGED_BEFORE_WRITE")
    bindings = {}
    for logical, body in sorted(sources.items()):
        destination = root / relative(logical)
        write_new(root, logical, body)
        _, identity = read(destination)
        bindings[logical] = {"path": logical, "sha256": sha256(body), "git_blob_sha1": git_blob(body),
            "git_mode": "100644", "bytes": len(body), "identity": identity}
    # This is the original four-file native checker, not a model or D projection.
    index = _index(root, day)
    require(validate_run(github_client.get_json(run_path), run_id) == run_bound, "RUN_CHANGED_DURING_IMPORT")
    require(validate_jobs(github_client.get_json(job_path), run, run_id) == jobs, "JOBS_CHANGED_DURING_IMPORT")
    require(select_artifact(github_client.get_json(artifacts_path), run, run_id) == artifact, "ARTIFACT_CHANGED_DURING_IMPORT")
    require(_index(root, day) == index, "P0_INDEX_CHANGED_DURING_IMPORT")
    inventory(root, bindings)
    require(code_guard() == code_before, "CODE_CHANGED_DURING_IMPORT")
    inventory(root, bindings)
    require(fingerprint(path(root, directory=True))[:3] == root_identity and expected_run_id == run_id,
        "ROOT_OR_RUN_ID_CHANGED")
    return {"schema_version": SCHEMA, "status": "IMPORTED_ORIGINAL_P0_BYTES_REQUIRES_D_ADAPTER",
        "repository": REPOSITORY, "source_root": str(root), "signal_date": day, "exec_date": trade, "exit_date": exit_day,
        "published_git_sha": published, "published_tree_sha": tree_sha, "published_parent_sha": receipt["inputs"]["git_head"],
        "expected_p0_sha256": {role: sha256(sources[logical]) for role, logical in paths.items()},
        "source_file_bindings": [{k: v for k, v in item.items() if k != "identity"} for item in bindings.values()],
        "original_file_count": 28, "run": run_bound, "critical_jobs": jobs, "pages_artifact": artifact,
        "pages_revision_sha256": revision_sha, "observed_p0_cas_job_completed_at": jobs[1]["completed_at"],
        "timing_semantics": "GITHUB_API_OBSERVED_JOB_COMPLETION_NOT_NEW_PREDICTION_FREEZE",
        "importer_sha256": SELF_SHA, "source_adapter_sha256": ADAPTER_SHA, "source_adapter_dependency_sha256": dict(adapter.CODE_PINS),
        "injected_client_for_test": type(github_client) is not GitHubReadClient,
        "network_calls_performed": github_client.calls if type(github_client) is GitHubReadClient else None,
        "github_original_byte_bindings_verified": True, "p0_native_four_file_contract_checked": True,
        "source_authority_issued": False, "complete_d_feature_admission_performed": False,
        "new_prediction_freeze_verified": False, "production_activation_allowed": False,
        "model_training_performed": False, "model_predictions_computed": False, "future_outcomes_read": False,
        "research_only": True, "remote_writes_performed": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args(argv)
    root = path(args.output, directory=True)
    target = path(args.receipt, exists=False)
    require(root not in target.parents and target != root and not target.exists(), "NEW_RECEIPT_OUTSIDE_SOURCE_ROOT_REQUIRED")
    path(target.parent, directory=True)
    token = os.environ.get(TOKEN_ENV, "")
    client = GitHubReadClient(token)
    result = import_p0_sources(root, expected_run_id=args.expected_run_id, github_client=client)
    body = json_bytes(result)
    require(token.encode() not in body, "CREDENTIAL_IN_RECEIPT_REJECTED")
    path(target.parent, directory=True)
    write_new(target.parent, target.name, body)
    require(read(target)[0] == body, "RECEIPT_CHANGED_AFTER_WRITE")
    inventory(root, {item["path"]: item for item in result["source_file_bindings"]})
    code_guard()
    require(read(target)[0] == body, "RECEIPT_CHANGED_AFTER_FINAL_GUARDS")
    print(json.dumps({"status": result["status"], "signal_date": result["signal_date"],
        "original_file_count": 28, "receipt_sha256": sha256(body), "new_prediction_freeze_verified": False}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        # Never echo token, signed URLs, server bodies or exception tracebacks.
        print("P0_ORIGINAL_SOURCE_IMPORT_BLOCKED", file=sys.stderr)
        raise SystemExit(1)
