"""Pure synthetic Git tests: no credential discovery, network or publication."""
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import PurePosixPath

import pytest

from work.profit_1000_upgrade import candidate_natural_journal_git as m

NOW = datetime(2026, 9, 16, 8, tzinfo=timezone.utc)
HEAD, COMMIT = "a"*40, "c"*40


def tree(files):
    directory = {}
    for path, raw in files.items():
        cursor = directory
        parts = path.split("/")
        for part in parts[:-1]: cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = raw
    entries = []
    def walk(node, prefix):
        rows = []
        for name, value in node.items():
            path = prefix+name
            if type(value) is dict:
                obj = walk(value, path+"/")
                item = {"path": path, "type": "tree", "mode": "040000", "sha": obj}
            else:
                item = {"path": path, "type": "blob", "mode": "100644", "sha": m.git_blob(value), "size": len(value)}
            entries.append(item); rows.append(item)
        rows.sort(key=lambda x: (PurePosixPath(x["path"]).name+("/" if x["type"] == "tree" else "")).encode())
        raw = b"".join(x["mode"].lstrip("0").encode()+b" "+PurePosixPath(x["path"]).name.encode()+b"\0"+bytes.fromhex(x["sha"]) for x in rows)
        return hashlib.sha1(b"tree "+str(len(raw)).encode()+b"\0"+raw).hexdigest()
    return {"sha": walk(directory, ""), "truncated": False, "tree": entries}


def files():
    prefix = m.PREFIX+"20260914/20260916/"
    a, b = b'{"original":"A"}', b'{"original":"B"}'
    return {prefix+"manifest.json": b'{"storage_only":true}',
        prefix+"blobs/"+m.digest(a)+".bin": a, prefix+"blobs/"+m.digest(b)+".bin": b}


class FakeGit:
    def __init__(self, fault=None, old=None):
        self.old_files = {"protected/original.txt": b"OLD_UNCHANGED", **(old or {})}
        self.old_tree = tree(self.old_files)
        self.objects = {m.git_blob(raw): raw for raw in self.old_files.values()}
        self.calls, self.patches, self.fault, self.ref_reads = [], [], fault, 0
    def call(self, method, suffix, payload=None):
        self.calls.append((method, suffix, deepcopy(payload)))
        if method == "GET" and suffix == "/git/ref/heads/main":
            self.ref_reads += 1
            return {"ref": "refs/heads/main", "object": {"type": "commit", "sha": "e"*40 if self.fault == "race" and self.ref_reads > 1 else HEAD}}
        if suffix == "/git/commits/"+HEAD: return {"sha": HEAD, "tree": {"sha": self.old_tree["sha"]}, "parents": []}
        if method == "GET" and suffix == "/git/trees/"+self.old_tree["sha"]+"?recursive=1":
            value = deepcopy(self.old_tree)
            if self.fault == "truncated": value["truncated"] = True
            if self.fault == "bad_tree_hash": value["tree"][0]["sha"] = "0"*40
            return value
        if method == "POST" and suffix == "/git/blobs":
            raw = base64.b64decode(payload["content"], validate=True); obj = m.git_blob(raw); self.objects[obj] = raw
            return {"sha": "0"*40 if self.fault == "blob" else obj}
        if method == "POST" and suffix == "/git/trees":
            assert payload["base_tree"] == self.old_tree["sha"]
            self.new_files = {**self.old_files, **{x["path"]: self.objects[x["sha"]] for x in payload["tree"]}}
            if self.fault == "old_change": self.new_files["protected/original.txt"] = b"changed"
            if self.fault == "extra_file": self.new_files["outputs/decision/bad.json"] = b"not allowed"
            self.new_tree = tree(self.new_files)
            return {"sha": self.new_tree["sha"]}
        if method == "GET" and suffix == "/git/trees/"+self.new_tree["sha"]+"?recursive=1":
            result = deepcopy(self.new_tree)
            if self.fault == "duplicate": result["tree"].append(deepcopy(result["tree"][-1]))
            return result
        if suffix == "/git/commits":
            return {"sha": COMMIT, "tree": {"sha": payload["tree"]}, "parents": [{"sha": "f"*40 if self.fault == "parent" else HEAD}]}
        if method == "PATCH":
            self.patches.append(payload)
            assert suffix == "/git/refs/heads/main" and payload == {"sha": COMMIT, "force": False}
            return {"object": {"sha": "0"*40 if self.fault == "uncertain" else COMMIT}}
        raise AssertionError((method, suffix, payload))


def run(git, values=None, **kwargs):
    return m.publish_new_journal(files() if values is None else values, github_client=git, clock=kwargs.pop("clock", lambda: NOW), **kwargs)


def test_exact_single_day_append_storage_only():
    git = FakeGit(); result = run(git)
    assert result["status"] == "JOURNAL_STORAGE_ACKNOWLEDGED" and len(git.patches) == 1
    assert result["signal_date"] == "20260914" and result["as_of_date"] == "20260916"
    assert result["created_blob_objects"] == 3 and result["git_api_calls"] == 11
    assert git.new_files["protected/original.txt"] == b"OLD_UNCHANGED"
    assert result["storage_integrity_only"] and all(result[k] is False for k in ("source_authority_issued", "natural_forward_admission_issued",
        "production_activation_allowed", "actual_execution_claimed", "existing_files_modified"))


def test_reuse_current_main_git_blob_and_post_each_unique_body_once():
    value = files(); manifest = next(p for p in value if p.endswith("manifest.json"))
    same = next(raw for p, raw in value.items() if "/blobs/" in p)
    value[manifest] = same
    git = FakeGit(old={"older/same.bin": same})
    result = run(git, value)
    assert result["created_blob_objects"] == 1 and result["reused_existing_blob_objects"] == 1
    assert result["unique_blob_objects"] == 2 and len(result["files"]) == 3
    assert len([x for x in git.calls if x[:2] == ("POST", "/git/blobs")]) == 1


@pytest.mark.parametrize("fault", ["truncated", "bad_tree_hash", "blob", "old_change", "extra_file", "duplicate", "parent", "race"])
def test_invalid_remote_or_race_never_moves_ref(fault):
    git = FakeGit(fault)
    with pytest.raises(ValueError): run(git)
    assert not git.patches


@pytest.mark.parametrize("path", [m.PREFIX+"20260914/20260916/manifest.json", m.PREFIX+"20260914/20260916/blobs/"+"0"*64+".bin"])
def test_partial_existing_version_is_never_overwritten(path):
    git = FakeGit(old={path: b"KEEP"})
    with pytest.raises(ValueError, match="PARTIAL"): run(git)
    assert not any(x[0] == "POST" for x in git.calls)


def test_other_day_asof_is_preserved_and_new_version_allowed():
    git = FakeGit(old={m.PREFIX+"20260914/20260915/manifest.json": b"ORIGINAL"})
    result = run(git)
    assert result["existing_files_modified"] is False and git.new_files[m.PREFIX+"20260914/20260915/manifest.json"] == b"ORIGINAL"


@pytest.mark.parametrize("path", ["outputs/decision/ledger.json", m.PREFIX+"20260911/20260916/manifest.json",
    m.PREFIX+"20260914/20260913/manifest.json", m.PREFIX+"20260914/20260916/../manifest.json",
    m.PREFIX+"20260914/20260916/extra.json", m.PREFIX+"20260915/20260916/manifest.json"])
def test_path_and_cohort_scope_before_network(path):
    values, git = files(), FakeGit(); values[path] = values.pop(next(iter(values)))
    with pytest.raises(ValueError): run(git, values)
    assert not git.calls


@pytest.mark.parametrize("fault", ["body", "files", "bytes", "total", "calls"])
def test_budgets_and_hash_guard(fault, monkeypatch):
    values, git = files(), FakeGit()
    if fault == "body": values[next(p for p in values if "/blobs/" in p)] += b"x"
    elif fault == "files": monkeypatch.setattr(m, "MAX_FILES", 2)
    elif fault == "bytes": monkeypatch.setattr(m, "MAX_FILE_BYTES", 1)
    elif fault == "total": monkeypatch.setattr(m, "MAX_TOTAL_BYTES", 1)
    else: monkeypatch.setattr(m, "MAX_CALLS", 9)
    with pytest.raises(ValueError): run(git, values)
    assert not any(x[0] in ("POST", "PATCH") for x in git.calls)


@pytest.mark.parametrize("when", ["pre", "post"])
def test_source_guard_cannot_change_any_payload(when):
    values, git, seen = files(), FakeGit(), []
    def guard():
        seen.append(True)
        if len(seen) == (1 if when == "pre" else 2): values[next(iter(values))] = b"changed"
    with pytest.raises(ValueError, match="PAYLOAD_CHANGED"): run(git, values, pre_cas_guard=guard)
    assert len(git.patches) == (0 if when == "pre" else 1)


def test_uncertain_cas_not_retried_or_deleted():
    git = FakeGit("uncertain")
    with pytest.raises(ValueError, match="RECONCILIATION"): run(git)
    assert len(git.patches) == 1


@pytest.mark.parametrize("when", ["first", "final"])
def test_entire_operation_deadline(when):
    git = FakeGit(); seen = []
    def guard(): seen.append(True)
    def clock(): return NOW if when == "final" and len(seen) < 2 else datetime(2026, 9, 16, 9, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="DEADLINE"):
        run(git, deadline=datetime(2026, 9, 16, 8, 30, tzinfo=timezone.utc), clock=clock, pre_cas_guard=guard)
    assert len(git.patches) == (0 if when == "first" else 1)


@pytest.mark.parametrize("method,path,payload", [("GET", "/user", None), ("POST", "/issues", {}),
    ("PATCH", "/git/refs/heads/other", {}), ("PATCH", "/git/refs/heads/main", {"sha": HEAD, "force": True}),
    ("GET", "/git/trees/"+HEAD+"/../other", None)])
def test_transport_routes_and_nonforce_before_call(method, path, payload):
    client = m.JournalGitWriter("SYNTHETIC_TOKEN")
    with pytest.raises(ValueError): client.call(method, path, payload)
    assert client.calls == 0


@pytest.mark.parametrize("kind", ["raw", "escaped", "base64_raw", "base64_escaped"])
def test_credentials_not_hidden_by_json_or_blob_encoding(kind):
    token = "SYNTHETIC_ONLY_TOKEN"
    client = m.JournalGitWriter(token)
    raw = token.encode() if kind.endswith("raw") else ('{"x":"'+"".join("\\u%04x"%ord(c) for c in token)+'"}').encode()
    payload = {"x": token} if kind == "raw" else {"encoding": "base64", "content": base64.b64encode(raw).decode()}
    with pytest.raises(ValueError, match="CREDENTIAL"): client.call("POST", "/git/blobs", payload)
    assert client.calls == 0


@pytest.mark.parametrize("kind", ["calls", "time"])
def test_transport_cannot_exceed_fixed_budget(kind, monkeypatch):
    client = m.JournalGitWriter("SYNTHETIC_TOKEN")
    if kind == "calls": client.calls = 600
    else: monkeypatch.setattr(m.time, "monotonic", lambda: client.started+590)
    with pytest.raises(ValueError, match="BUDGET"): client.call("GET", "/git/ref/heads/main")


@pytest.mark.parametrize("kind", ["success", "redirect", "secret", "duplicate", "overflow"])
def test_transport_fixed_api_domain_status_and_no_redirect(kind, monkeypatch):
    client = m.JournalGitWriter("SYNTHETIC_TOKEN"); seen = []
    class Reply:
        status = 200 if kind != "redirect" else 302
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, limit):
            assert limit == m.MAX_HTTP_BYTES+1
            return {"success": b'{"object":{}}', "secret": b'{"x":"SYNTHETIC_TOKEN"}', "duplicate": b'{"x":1,"x":2}',
                "overflow": b"x"*(m.MAX_HTTP_BYTES+1), "redirect": b"{}"}[kind]
    class Opener:
        def open(self, req, timeout):
            seen.append(req)
            assert req.full_url == "https://api.github.com/repos/njedu2023-prog/DC20/git/ref/heads/main"
            assert timeout == 20 and req.get_method() == "GET"
            return Reply()
    client.opener = Opener()
    if kind == "success": assert client.call("GET", "/git/ref/heads/main") == {"object": {}}
    else:
        with pytest.raises(ValueError): client.call("GET", "/git/ref/heads/main")
    assert len(seen) == client.calls == 1


def test_git_tree_empty_directory_hash_is_not_opaque():
    empty = hashlib.sha1(b"tree 0\0").hexdigest()
    body = b"40000 empty\0"+bytes.fromhex(empty)
    root = hashlib.sha1(b"tree "+str(len(body)).encode()+b"\0"+body).hexdigest()
    document = {"sha": root, "truncated": False, "tree": [{"path": "empty", "mode": "040000", "type": "tree", "sha": empty}]}
    assert "empty" in m._tree(document, root)
    document["tree"][0]["sha"] = "0"*40
    with pytest.raises(ValueError): m._tree(document, root)


@pytest.mark.parametrize("kind", ["duplicate", "malformed"])
def test_escaped_token_in_invalid_json_blob_still_rejected(kind):
    token = "SYNTHETIC_TOKEN"
    escaped = "".join("\\u%04x"%ord(c) for c in token)
    raw = ('{"x":"'+escaped+'","x":"clean"'+('}' if kind == 'duplicate' else '')).encode()
    client = m.JournalGitWriter(token)
    with pytest.raises(ValueError, match="CREDENTIAL"):
        client.call("POST", "/git/blobs", {"encoding": "base64", "content": base64.b64encode(raw).decode()})
    assert client.calls == 0


def test_final_elapsed_guard_cannot_return_ack(monkeypatch):
    git, ticks, calls = FakeGit(), [0.0], []
    monkeypatch.setattr(m.time, "monotonic", lambda: ticks[0])
    def guard():
        calls.append(True)
        if len(calls) == 2: ticks[0] = 601
    with pytest.raises(ValueError, match="TOTAL_BUDGET"): run(git, pre_cas_guard=guard)
    assert len(git.patches) == 1


def test_all_existing_objects_need_no_new_blob_posts():
    value = files(); git = FakeGit(old={f"old/{i}.bin": raw for i, raw in enumerate(value.values())})
    result = run(git, value)
    assert result["created_blob_objects"] == 0 and result["reused_existing_blob_objects"] == 3
    assert result["git_api_calls"] == 8
    assert not any(x[:2] == ("POST", "/git/blobs") for x in git.calls)
