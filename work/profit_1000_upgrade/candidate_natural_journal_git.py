"""Append one content-addressed research day journal, never market authority.

Fixed repository/namespace, finite HTTPS Git CAS, no retries or redirects.
Input and output are storage bytes only; callers separately verify provenance.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import PurePosixPath
import re
import time
from urllib import error, request

REPO = "njedu2023-prog/DC20"
PREFIX = "work/profit_1000_upgrade/candidate_natural_journal/"
MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES = 1025, 8 * 1024**2, 128 * 1024**2
MAX_CALLS, MAX_SECONDS, TIMEOUT, MAX_HTTP_BYTES, MAX_TREE_ENTRIES = 600, 600, 20, 16 * 1024**2, 200000


def require(ok, reason):
    if not ok: raise ValueError(reason)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def digest(raw): return hashlib.sha256(raw).hexdigest()
def git_blob(raw): return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def sha40(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{40}", value), "EXACT_GIT_SHA_REQUIRED")
    return value


def _pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "DUPLICATE_GIT_JSON_KEY")
        result[key] = value
    return result


def parse(raw):
    return json.loads(raw, object_pairs_hook=_pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("NONFINITE_GIT_JSON")))


def _secret(raw, token):
    require(token.encode() not in raw, "CREDENTIAL_PAYLOAD_REJECTED")
    # Inspect each JSON string independently too: duplicate keys or malformed
    # enclosing JSON must not hide an escaped credential in a preserved blob.
    for match in re.finditer(r'"(?:\\.|[^"\\])*"', raw.decode("utf-8", errors="ignore")):
        try: text = json.loads(match.group())
        except ValueError: continue
        require(token not in text, "CREDENTIAL_PAYLOAD_REJECTED")
    try: value = parse(raw)
    except (ValueError, UnicodeError): return
    todo = [value]
    while todo:
        value = todo.pop()
        if type(value) is str: require(token not in value, "CREDENTIAL_PAYLOAD_REJECTED")
        elif type(value) is dict: todo.extend(value.keys()); todo.extend(value.values())
        elif type(value) is list: todo.extend(value)


class JournalGitWriter:
    def __init__(self, token):
        require(type(token) is str and token and not any(c.isspace() for c in token), "TOKEN_REQUIRED")
        self.token, self.calls, self.started = token, 0, time.monotonic()
        class NoRedirect(request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs): return None
        self.opener = request.build_opener(request.ProxyHandler({}), NoRedirect())

    def call(self, method, suffix, payload=None):
        allowed = ((method == "GET" and (suffix == "/git/ref/heads/main" or
            re.fullmatch(r"/git/commits/[0-9a-f]{40}|/git/trees/[0-9a-f]{40}\?recursive=1", suffix))) or
            (method == "POST" and suffix in ("/git/blobs", "/git/trees", "/git/commits")) or
            (method == "PATCH" and suffix == "/git/refs/heads/main"))
        require(allowed and (method != "GET" or payload is None), "NON_JOURNAL_GIT_ROUTE_REJECTED")
        require(method != "PATCH" or type(payload) is dict and set(payload) == {"sha", "force"}
            and payload["force"] is False and sha40(payload["sha"]), "NONFORCE_MAIN_CAS_ONLY")
        require(self.calls < MAX_CALLS and time.monotonic()-self.started + TIMEOUT < MAX_SECONDS, "JOURNAL_GIT_BUDGET_EXCEEDED")
        body = None if payload is None else encoded(payload)
        if body is not None:
            require(len(body) <= MAX_HTTP_BYTES, "GIT_REQUEST_BYTE_LIMIT")
            _secret(body, self.token)
            if suffix == "/git/blobs":
                require(type(payload) is dict and set(payload) == {"encoding", "content"} and payload["encoding"] == "base64",
                    "EXACT_BLOB_PAYLOAD_REQUIRED")
                decoded = base64.b64decode(payload["content"], validate=True)
                require(len(decoded) <= MAX_FILE_BYTES, "JOURNAL_BLOB_BYTE_LIMIT")
                _secret(decoded, self.token)
        self.calls += 1
        req = request.Request("https://api.github.com/repos/" + REPO + suffix, data=body, method=method,
            headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "DC20-journal-Git-CAS"})
        try:
            with self.opener.open(req, timeout=TIMEOUT) as response:
                require(response.status == (200 if method in ("GET", "PATCH") else 201), "EXACT_GIT_HTTP_STATUS_REQUIRED")
                raw = response.read(MAX_HTTP_BYTES+1)
                require(len(raw) <= MAX_HTTP_BYTES and time.monotonic()-self.started < MAX_SECONDS, "GIT_RESPONSE_BUDGET_EXCEEDED")
                _secret(raw, self.token)
                value = parse(raw)
                require(type(value) is dict, "GIT_JSON_OBJECT_REQUIRED")
                return value
        except (error.URLError, TimeoutError, OSError):
            raise ValueError("JOURNAL_GIT_FAILED_NO_RETRY") from None


def validate_files(files):
    require(type(files) is dict and 2 <= len(files) <= MAX_FILES, "BOUNDED_JOURNAL_FILES_REQUIRED")
    identities, total, manifest = set(), 0, None
    for path, raw in files.items():
        require(type(path) is str, "EXACT_JOURNAL_PATH_REQUIRED")
        match = re.fullmatch(re.escape(PREFIX) + r"([0-9]{8})/([0-9]{8})/(manifest\.json|blobs/([0-9a-f]{64})\.bin)", path)
        require(match and type(raw) is bytes and 0 < len(raw) <= MAX_FILE_BYTES, "JOURNAL_PATH_OR_BODY_REJECTED")
        day, asof, name, body_sha = match.groups()
        datetime.strptime(day, "%Y%m%d"); datetime.strptime(asof, "%Y%m%d")
        require("20260914" <= day <= asof, "NATURAL_D_ASOF_REQUIRED")
        require(body_sha is None or digest(raw) == body_sha, "CONTENT_ADDRESSED_JOURNAL_BODY_CHANGED")
        identities.add((day, asof)); total += len(raw)
        if name == "manifest.json": manifest = raw
    require(len(identities) == 1 and manifest is not None and total <= MAX_TOTAL_BYTES, "ONE_COMPLETE_BOUNDED_JOURNAL_REQUIRED")
    return next(iter(identities))


def _tree(document, expected):
    """Independently recompute every directory's Git tree object SHA."""
    require(document.get("sha") == expected and document.get("truncated") is False and type(document.get("tree")) is list
        and 0 < len(document["tree"]) <= MAX_TREE_ENTRIES, "COMPLETE_GIT_TREE_REQUIRED")
    entries, children = {}, {}
    for item in document["tree"]:
        require(type(item) is dict and type(item.get("path")) is str, "EXACT_TREE_ENTRY_REQUIRED")
        path = item["path"]
        require(path and "\\" not in path and not path.startswith("/") and "\0" not in path
            and all(p not in ("", ".", "..") for p in path.split("/")) and path not in entries, "INVALID_OR_DUPLICATE_TREE_PATH")
        kind = item.get("type"); sha40(item.get("sha"))
        require(kind in ("blob", "tree", "commit") and item.get("mode") in {
            "blob": ("100644", "100755", "120000"), "tree": ("040000",), "commit": ("160000",)}[kind], "INVALID_TREE_MODE")
        entries[path] = item
        if kind == "tree": children.setdefault(path, [])
        parent = str(PurePosixPath(path).parent)
        children.setdefault("" if parent == "." else parent, []).append(item)
    for parent, items in children.items():
        require(not parent or parent in entries and entries[parent]["type"] == "tree", "TREE_PARENT_REQUIRED")
        items.sort(key=lambda x: (PurePosixPath(x["path"]).name+("/" if x["type"] == "tree" else "")).encode())
        body = b"".join(x["mode"].lstrip("0").encode()+b" "+PurePosixPath(x["path"]).name.encode()+b"\0"+bytes.fromhex(x["sha"]) for x in items)
        observed = hashlib.sha1(b"tree "+str(len(body)).encode()+b"\0"+body).hexdigest()
        require(observed == (entries[parent]["sha"] if parent else expected), "GIT_TREE_OBJECT_SHA_MISMATCH")
    return entries


def publish_new_journal(files, *, github_client, deadline=None, clock=lambda: datetime.now(timezone.utc), pre_cas_guard=None):
    day, asof = validate_files(files)
    sealed = tuple(sorted(files.items())); begun = time.monotonic(); calls = 0
    def check_time():
        require(time.monotonic()-begun < MAX_SECONDS, "JOURNAL_TOTAL_BUDGET_EXCEEDED")
        now = clock(); require(now.tzinfo is not None, "AWARE_STORAGE_CLOCK_REQUIRED")
        require(deadline is None or deadline.tzinfo is not None and now < deadline, "JOURNAL_STORAGE_DEADLINE_PASSED")
        return now
    def call(method, suffix, payload=None):
        nonlocal calls
        check_time(); require(calls < MAX_CALLS, "JOURNAL_GIT_CALL_BUDGET_EXCEEDED")
        calls += 1
        result = github_client.call(method, suffix, payload)
        check_time(); return result
    def unchanged(): require(tuple(sorted(files.items())) == sealed, "JOURNAL_PAYLOAD_CHANGED")
    check_time(); unchanged()
    ref = call("GET", "/git/ref/heads/main")
    require(ref.get("ref") == "refs/heads/main" and ref.get("object", {}).get("type") == "commit", "MAIN_COMMIT_REF_REQUIRED")
    head = sha40(ref["object"]["sha"])
    parent = call("GET", "/git/commits/"+head)
    require(parent["sha"] == head, "EXACT_JOURNAL_PARENT_REQUIRED")
    tree_sha = sha40(parent["tree"]["sha"])
    old = _tree(call("GET", "/git/trees/"+tree_sha+"?recursive=1"), tree_sha)
    prefix = PREFIX+day+"/"+asof
    require(not any(p == prefix or p.startswith(prefix+"/") for p in old), "EXISTING_OR_PARTIAL_D_ASOF_KEEP_ORIGINAL")
    reusable = {x["sha"] for x in old.values() if x["type"] == "blob"}
    unique = {git_blob(raw): raw for _, raw in sealed}
    require(all(unique[git_blob(raw)] == raw for _, raw in sealed), "CONFLICTING_GIT_OBJECT_BYTES")
    require(calls + len(set(unique)-reusable) + 5 <= MAX_CALLS, "NEW_UNIQUE_BLOBS_EXCEED_GIT_BUDGET")
    created = set()
    for object_sha, raw in unique.items():
        if object_sha in reusable: continue
        result = call("POST", "/git/blobs", {"encoding": "base64", "content": base64.b64encode(raw).decode()})
        require(result["sha"] == object_sha, "CREATED_JOURNAL_BLOB_CHANGED")
        created.add(object_sha)
    additions = [{"path": path, "type": "blob", "mode": "100644", "sha": git_blob(raw)} for path, raw in sealed]
    made = call("POST", "/git/trees", {"base_tree": tree_sha, "tree": additions})
    new_tree_sha = sha40(made["sha"])
    new = _tree(call("GET", "/git/trees/"+new_tree_sha+"?recursive=1"), new_tree_sha)
    parents = {str(p) for path in files for p in PurePosixPath(path).parents if str(p) != "."}
    require(set(new) == set(old) | set(files) | parents
        and all(new[p]["type"] == "tree" and new[p]["mode"] == "040000" for p in parents)
        and all(new[p] == x for p, x in old.items() if x["type"] == "tree" and p not in parents), "UNRELATED_JOURNAL_TREE_CHANGE")
    leaves = lambda tree: {p: (x["mode"], x["type"], x["sha"]) for p, x in tree.items() if x["type"] != "tree"}
    require(leaves(new) == {**leaves(old), **{x["path"]: (x["mode"], x["type"], x["sha"]) for x in additions}}, "OLD_FILE_CHANGED_OR_UNEXPECTED_ADDITION")
    made = call("POST", "/git/commits", {"message": "research: append original day journal "+day+"/"+asof,
        "tree": new_tree_sha, "parents": [head]})
    new_head = sha40(made["sha"])
    require(made["tree"]["sha"] == new_tree_sha and [x["sha"] for x in made["parents"]] == [head], "CREATED_JOURNAL_COMMIT_CHANGED")
    require(call("GET", "/git/ref/heads/main")["object"]["sha"] == head, "MAIN_MOVED_NO_RETRY")
    if pre_cas_guard is not None: pre_cas_guard()
    unchanged(); check_time()
    moved = call("PATCH", "/git/refs/heads/main", {"sha": new_head, "force": False})
    require(moved["object"]["sha"] == new_head, "UNCERTAIN_JOURNAL_CAS_READ_ONLY_RECONCILIATION_REQUIRED")
    unchanged()
    if pre_cas_guard is not None: pre_cas_guard()
    unchanged(); acknowledged = check_time()
    return {"schema_version": "dc20_natural_journal_git_storage_ack_v1", "status": "JOURNAL_STORAGE_ACKNOWLEDGED",
        "signal_date": day, "as_of_date": asof, "commit_sha": new_head, "parent_sha": head, "tree_sha": new_tree_sha,
        "files": [{"path": p, "sha256": digest(raw), "git_blob_sha1": git_blob(raw), "bytes": len(raw)} for p, raw in sealed],
        "created_blob_objects": len(created), "reused_existing_blob_objects": len(set(unique)&reusable),
        "unique_blob_objects": len(unique), "git_api_calls": calls, "acknowledged_at_host_utc": acknowledged.astimezone(timezone.utc).isoformat(),
        "storage_integrity_only": True, "source_authority_issued": False, "natural_forward_admission_issued": False,
        "production_activation_allowed": False, "actual_execution_claimed": False, "existing_files_modified": False}
