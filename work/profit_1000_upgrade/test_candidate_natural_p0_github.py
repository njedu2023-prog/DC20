"""Stub GitHub contracts; no HTTP, actual future source, fit or publication.

Full import fixtures use the explicitly synthetic P0 fixture/checker from the
source adapter tests. They are NOT evidence of a real source/run acceptance.
"""
import base64
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import stat
import tarfile
import urllib.error
import zipfile

import pytest

from work.profit_1000_upgrade import candidate_natural_p0_github as m
from work.profit_1000_upgrade.test_candidate_d_source_adapter import make_case, json_bytes, write


RUN_ID = "43212345678"
PUBLISHED = "b" * 40
BASE = "c" * 40


def pages_zip(revision=None, *, entries=None, zip_name="artifact.tar", zip_mode=None):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as tar:
        for name, body, kind in entries or [("./revision.json", json_bytes(revision), tarfile.REGTYPE),
            ("./index.html", b"synthetic only", tarfile.REGTYPE)]:
            item = tarfile.TarInfo(name)
            item.type = kind
            item.size = len(body) if kind == tarfile.REGTYPE else 0
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE): item.linkname = "outside"
            tar.addfile(item, io.BytesIO(body))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        item = zipfile.ZipInfo(zip_name)
        if zip_mode is not None: item.external_attr = zip_mode << 16
        archive.writestr(item, stream.getvalue())
    return out.getvalue()


def git_tree(sources):
    nodes = {name: {"path": name, "mode": "100644", "type": "blob", "sha": m.git_blob(body), "size": len(body)}
        for name, body in sources.items()}
    dirs = {str(parent) for name in sources for parent in Path(name).parents if str(parent) != "."}
    for directory in sorted(dirs, key=lambda x: (-x.count("/"), x)):
        children = [v for p, v in nodes.items() if str(Path(p).parent) == directory]
        children.sort(key=lambda x: (Path(x["path"]).name + ("/" if x["type"] == "tree" else "")).encode())
        body = b"".join(x["mode"].lstrip("0").encode() + b" " + Path(x["path"]).name.encode()
            + b"\0" + bytes.fromhex(x["sha"]) for x in children)
        digest = hashlib.sha1(b"tree " + str(len(body)).encode() + b"\0" + body).hexdigest()
        nodes[directory] = {"path": directory, "mode": "040000", "type": "tree", "sha": digest}
    children = [v for p, v in nodes.items() if str(Path(p).parent) == "."]
    children.sort(key=lambda x: (Path(x["path"]).name + ("/" if x["type"] == "tree" else "")).encode())
    body = b"".join(x["mode"].lstrip("0").encode() + b" " + Path(x["path"]).name.encode()
        + b"\0" + bytes.fromhex(x["sha"]) for x in children)
    root = hashlib.sha1(b"tree " + str(len(body)).encode() + b"\0" + body).hexdigest()
    return {"sha": root, "truncated": False, "tree": sorted(nodes.values(), key=lambda x: x["path"])}


class StubClient:
    def __init__(self, responses, archive):
        self.responses, self.archive, self.requests = responses, archive, []
    def get_json(self, api_path):
        self.requests.append(api_path)
        return deepcopy(self.responses[api_path])
    def get_artifact_zip(self, artifact_id):
        self.requests.append(("artifact", artifact_id))
        assert artifact_id == 12345
        return self.archive


def fixture(tmp_path, monkeypatch, *, size=2):
    original_checker = m.adapter.publisher.build_primary_d_runtime_index
    case = make_case(tmp_path, monkeypatch, size=size)
    synthetic_checker = m.adapter.publisher.build_primary_d_runtime_index
    monkeypatch.setattr(m.adapter.publisher, "build_primary_d_runtime_index", original_checker)
    # Stub the importer's explicit four-file check boundary, not its frozen
    # imported publisher function. This remains a synthetic source fixture.
    monkeypatch.setattr(m, "_index", lambda root, day: synthetic_checker(root))
    day, contract = case["day"], case["contract"]
    receipt = case["receipt"]
    receipt.update(generation_mode="NATURAL", primary_status="READY", prospective=True, forward_eligible=True,
        not_forward_generated=False, future_market_data_consumed=False, latest_fallback_used=False,
        action_authorized=False, action_input_consumed=False, formal_trade_count=0,
        exec_date=contract["exec_date"], exit_date=contract["exit_date"])
    receipt["inputs"]["git_head"] = BASE
    calendar = receipt["inputs"]["calendar"]
    calendar.update(git_blob_sha1=m.git_blob((case["root"] / calendar["path"]).read_bytes()), git_mode="100644")
    write(case["root"] / m.adapter._p0_paths(day)["receipt"], json_bytes(receipt))
    sources = {p.relative_to(case["root"]).as_posix(): p.read_bytes() for p in case["root"].rglob("*") if p.is_file()}
    tree = git_tree(sources)
    run = {"id": int(RUN_ID), "workflow_id": m.WORKFLOW_ID, "path": m.WORKFLOW_PATH, "head_branch": "main",
        "head_sha": "d" * 40, "run_attempt": 1, "status": "completed", "conclusion": "success", "event": "schedule",
        "name": m.WORKFLOW_NAME, "repository": {"full_name": m.REPOSITORY}, "head_repository": {"full_name": m.REPOSITORY},
        "created_at": "2026-09-14T11:00:00Z", "run_started_at": "2026-09-14T11:00:01Z", "updated_at": "2026-09-14T11:10:00Z"}
    jobs = [{"id": 101+i, "run_id": int(RUN_ID), "name": name,
        "status": "completed", "conclusion": "success", "started_at": f"2026-09-14T11:0{1+i*2}:00Z",
        "completed_at": f"2026-09-14T11:0{2+i*2}:00Z"} for i, name in enumerate(m.CRITICAL_JOBS)]
    revision = {"schema_version": "decision_pages_revision_v4_primary_first", "repository": m.REPOSITORY, "branch": "main",
        "head_sha": PUBLISHED, "run_id": int(RUN_ID), "run_attempt": 1, "workflow": m.WORKFLOW_NAME,
        "event_name": "schedule", "signal_date": day, "primary_d_signal_date": day,
        "primary_d_exec_date": contract["exec_date"], "primary_d_exit_date": contract["exit_date"],
        "primary_d_status": "READY", "primary_d_generation_mode": "NATURAL", "primary_d_bundle_sha256": contract["bundle_sha256"],
        "primary_d_top10_count": contract["top10_count"], "primary_d_receipt_url": m.adapter._p0_paths(day)["receipt"],
        "published_at_utc": "NOT_A_TIMELY_CANDIDATE_FREEZE", "unrelated_future_performance": "NEVER_RETURN_THIS"}
    archive = pages_zip(revision)
    artifact = {"id": 12345, "name": "github-pages", "size_in_bytes": len(archive), "digest": "sha256:" + m.sha256(archive),
        "expired": False, "created_at": "2026-09-14T11:05:00Z", "updated_at": "2026-09-14T11:05:01Z",
        "workflow_run": {"id": int(RUN_ID), "head_branch": "main", "head_sha": run["head_sha"]}}
    base = m.API_PREFIX + "/actions/runs/" + RUN_ID
    responses = {base: run, base + "/attempts/1/jobs?per_page=100": {"total_count": 3, "jobs": jobs},
        base + "/artifacts?per_page=100": {"total_count": 1, "artifacts": [artifact]},
        f"{m.API_PREFIX}/git/commits/{PUBLISHED}": {"sha": PUBLISHED, "tree": {"sha": tree["sha"]}, "parents": [{"sha": BASE}]},
        f"{m.API_PREFIX}/git/trees/{tree['sha']}?recursive=1": tree}
    for name, body in sources.items():
        digest = m.git_blob(body)
        responses[f"{m.API_PREFIX}/git/blobs/{digest}"] = {"sha": digest, "size": len(body), "encoding": "base64",
            "content": base64.encodebytes(body).decode()}
    output = tmp_path.resolve() / "imported"
    output.mkdir()
    case.update(output=output, sources=sources, tree=tree, run=run, jobs=jobs, revision=revision, artifact=artifact,
        client=StubClient(responses, archive), api_base=base)
    return case


def execute(case):
    return m.import_p0_sources(case["output"], expected_run_id=RUN_ID, github_client=case["client"])


@pytest.mark.parametrize("size", [0, 1, 2, 10, 13])
def test_synthetic_same_run_original_28_sources_only(tmp_path, monkeypatch, size):
    c = fixture(tmp_path, monkeypatch, size=size)
    monkeypatch.setattr(socket, "socket", lambda *a, **kw: pytest.fail("REAL_NETWORK"))
    report = execute(c)
    assert report["status"] == "IMPORTED_ORIGINAL_P0_BYTES_REQUIRES_D_ADAPTER"
    assert report["injected_client_for_test"] is True and report["network_calls_performed"] is None
    assert report["original_file_count"] == len(report["source_file_bindings"]) == 28
    assert report["published_git_sha"] == PUBLISHED != report["run"]["head_sha"]
    assert report["published_parent_sha"] == BASE
    assert report["published_tree_sha"] == c["tree"]["sha"]
    assert {p.relative_to(c["output"]).as_posix(): p.read_bytes() for p in c["output"].rglob("*") if p.is_file()} == c["sources"]
    assert report["expected_p0_sha256"] == {k: m.sha256(c["sources"][v]) for k, v in m.adapter._p0_paths(c["day"]).items()}
    for record in report["source_file_bindings"]:
        assert set(record) == {"path", "sha256", "git_blob_sha1", "git_mode", "bytes"}
        assert record["git_mode"] == "100644"
    assert "NEVER_RETURN_THIS" not in json.dumps(report)
    assert "NOT_A_TIMELY_CANDIDATE_FREEZE" not in json.dumps(report)
    assert report["observed_p0_cas_job_completed_at"] == c["jobs"][1]["completed_at"]
    assert report["run"]["updated_at"] != report["observed_p0_cas_job_completed_at"]
    for flag in ("source_authority_issued", "complete_d_feature_admission_performed", "new_prediction_freeze_verified",
        "production_activation_allowed", "model_training_performed", "model_predictions_computed", "future_outcomes_read"):
        assert report[flag] is False
    assert report["research_only"] is True and report["remote_writes_performed"] == 0
    assert len([p for p in c["client"].requests if type(p) is str and "/git/blobs/" in p]) == 28
    assert all(type(p) is tuple or p.startswith(m.API_PREFIX + "/") for p in c["client"].requests)


@pytest.mark.parametrize("key,value", [("id", True), ("workflow_id", 1), ("path", "other.yml"), ("head_branch", "feature"),
    ("head_sha", "x"*40), ("run_attempt", 2), ("run_attempt", True), ("status", "in_progress"),
    ("conclusion", "failure"), ("event", "push"), ("name", "wrong"), ("repository", {"full_name": "other/DC20"}),
    ("head_repository", {"full_name": "attacker/DC20"}), ("updated_at", "2026-09-14T10:00:00Z")])
def test_run_identity_failclosed_before_archive(tmp_path, monkeypatch, key, value):
    c = fixture(tmp_path, monkeypatch); c["run"][key] = value
    with pytest.raises(m.ImportBlocked): execute(c)
    assert not any(c["output"].iterdir()) and not any(type(p) is tuple for p in c["client"].requests)


@pytest.mark.parametrize("which", ["missing", "duplicate", "skipped_cas", "foreign_run", "out_of_order", "partial_list", "failed_deploy"])
def test_critical_jobs_all_success_no_cas_skip(tmp_path, monkeypatch, which):
    c = fixture(tmp_path, monkeypatch)
    document = c["client"].responses[c["api_base"] + "/attempts/1/jobs?per_page=100"]
    if which == "missing": document["jobs"].pop(); document["total_count"] -= 1
    elif which == "duplicate": document["jobs"].append(deepcopy(c["jobs"][0])); document["total_count"] += 1
    elif which == "skipped_cas": c["jobs"][1]["conclusion"] = "skipped"
    elif which == "foreign_run": c["jobs"][0]["run_id"] += 1
    elif which == "out_of_order": c["jobs"][1]["started_at"] = c["jobs"][0]["started_at"]
    elif which == "partial_list": document["total_count"] += 1
    else: c["jobs"][2]["conclusion"] = "failure"
    with pytest.raises(m.ImportBlocked): execute(c)
    assert not any(c["output"].iterdir())


@pytest.mark.parametrize("which", ["missing", "duplicate", "expired", "foreign_run", "wrong_head", "no_digest", "wrong_digest", "wrong_bytes", "partial_list"])
def test_same_run_artifact_required(tmp_path, monkeypatch, which):
    c = fixture(tmp_path, monkeypatch)
    doc = c["client"].responses[c["api_base"] + "/artifacts?per_page=100"]
    if which == "missing": doc.update(total_count=0, artifacts=[])
    elif which == "duplicate": doc["artifacts"].append(deepcopy(c["artifact"])); doc["total_count"] += 1
    elif which == "expired": c["artifact"]["expired"] = True
    elif which == "foreign_run": c["artifact"]["workflow_run"]["id"] += 1
    elif which == "wrong_head": c["artifact"]["workflow_run"]["head_sha"] = BASE
    elif which == "no_digest": c["artifact"].pop("digest")
    elif which == "wrong_digest": c["artifact"]["digest"] = "sha256:" + "0"*64
    elif which == "wrong_bytes": c["artifact"]["size_in_bytes"] += 1
    else: doc["total_count"] += 1
    with pytest.raises(m.ImportBlocked): execute(c)
    assert not any(c["output"].iterdir())


@pytest.mark.parametrize("key,value", [("run_id", 5), ("run_attempt", 2), ("repository", "other/repo"), ("branch", "feature"),
    ("workflow", "wrong"), ("event_name", "push"), ("primary_d_generation_mode", "RECOVERED"),
    ("primary_d_status", "BLOCKED"), ("primary_d_signal_date", "20260911"), ("signal_date", "20260915"),
    ("primary_d_exec_date", "20260914"), ("primary_d_receipt_url", "latest.json"), ("primary_d_top10_count", True),
    ("primary_d_top10_count", 11), ("head_sha", "../bad")])
def test_revision_exact_natural_date_and_run(tmp_path, monkeypatch, key, value):
    c = fixture(tmp_path, monkeypatch); c["revision"][key] = value
    with pytest.raises(m.ImportBlocked): m.validate_revision(c["revision"], c["run"], RUN_ID)


def test_controlled_dispatch_exact_title(tmp_path, monkeypatch):
    c = fixture(tmp_path, monkeypatch)
    c["run"].update(event="workflow_dispatch", name=f"DC20 controlled daily NATURAL | D={c['day']}",
        display_title=f"DC20 controlled daily NATURAL | D={c['day']}")
    c["revision"]["event_name"] = "workflow_dispatch"
    assert m.validate_run(c["run"], RUN_ID)
    assert m.validate_revision(c["revision"], c["run"], RUN_ID)[0] == c["day"]
    c["run"]["display_title"] = m.WORKFLOW_NAME
    with pytest.raises(m.ImportBlocked): m.validate_revision(c["revision"], c["run"], RUN_ID)


def test_controlled_dispatch_title_in_final_run_binding(tmp_path, monkeypatch):
    c = fixture(tmp_path, monkeypatch)
    title = f"DC20 controlled daily NATURAL | D={c['day']}"
    c["run"].update(event="workflow_dispatch", name=title, display_title=title)
    assert m.validate_run(c["run"], RUN_ID)["display_title"] == title
    c["run"]["display_title"] = title + " changed"
    with pytest.raises(m.ImportBlocked): m.validate_run(c["run"], RUN_ID)


def test_bare_reusable_deploy_job_name_not_accepted(tmp_path, monkeypatch):
    c = fixture(tmp_path, monkeypatch)
    c["jobs"][2]["name"] = "Deploy exact primary D revision"
    with pytest.raises(m.ImportBlocked, match="CRITICAL_JOB_MISSING"): execute(c)


@pytest.mark.parametrize("name,kind", [("../revision.json", tarfile.REGTYPE), ("/revision.json", tarfile.REGTYPE),
    ("a/../revision.json", tarfile.REGTYPE), ("a\\revision.json", tarfile.REGTYPE), ("revision.json", tarfile.SYMTYPE),
    ("revision.json", tarfile.LNKTYPE), ("revision.json", tarfile.FIFOTYPE), ("revision.json", tarfile.CHRTYPE)])
def test_tar_unsafe_members_rejected(name, kind):
    body = pages_zip(entries=[(name, b"{}", kind)])
    with pytest.raises(m.ImportBlocked): m.revision_from_zip(body)


@pytest.mark.parametrize("which", ["duplicate", "missing", "zip_symlink", "zip_name", "zip_bomb", "tar_bomb", "revision_bomb", "entry_bomb", "crc"])
def test_archive_limits_and_crc(monkeypatch, which):
    body = pages_zip({"safe": True})
    if which == "duplicate": body = pages_zip(entries=[("revision.json", b"{}", tarfile.REGTYPE)]*2)
    elif which == "missing": body = pages_zip(entries=[("index.html", b"anything", tarfile.REGTYPE)])
    elif which == "zip_symlink": body = pages_zip({}, zip_mode=stat.S_IFLNK | 0o777)
    elif which == "zip_name": body = pages_zip({}, zip_name="../artifact.tar")
    elif which == "zip_bomb": monkeypatch.setattr(m, "MAX_ZIP_BYTES", 1)
    elif which == "tar_bomb": monkeypatch.setattr(m, "MAX_TAR_BYTES", 1)
    elif which == "revision_bomb": monkeypatch.setattr(m, "MAX_REVISION_BYTES", 1)
    elif which == "entry_bomb": monkeypatch.setattr(m, "MAX_ENTRIES", 1)
    else:
        # Stored ZIP member payload is corrupted without fixing the CRC.
        body = body[:100] + bytes([body[100] ^ 1]) + body[101:]
    with pytest.raises(m.ImportBlocked): m.revision_from_zip(body)


@pytest.mark.parametrize("reverse", [False, True])
def test_tar_file_parent_collision_rejected(reverse):
    entries = [("a", b"x", tarfile.REGTYPE), ("a/revision.json", b"{}", tarfile.REGTYPE)]
    if reverse: entries.reverse()
    with pytest.raises(m.ImportBlocked): m.revision_from_zip(pages_zip(entries=entries))


@pytest.mark.parametrize("which", ["truncated", "root_sha", "child_sha", "duplicate", "missing_parent", "unsafe", "bad_mode"])
def test_complete_recursive_tree_recomputed(which):
    tree = git_tree({"a/b.csv": b"x", "c.txt": b"y"}); expected = tree["sha"]
    if which == "truncated": tree["truncated"] = True
    elif which == "root_sha": tree["sha"] = "0"*40
    elif which == "child_sha": tree["tree"][0]["sha"] = "0"*40
    elif which == "duplicate": tree["tree"].append(deepcopy(tree["tree"][0]))
    elif which == "missing_parent": tree["tree"] = [x for x in tree["tree"] if x["type"] != "tree"]
    elif which == "unsafe": tree["tree"][0]["path"] = "../bad"
    else: tree["tree"][0]["mode"] = "100666"
    with pytest.raises(m.ImportBlocked): m.validate_tree(tree, expected)


@pytest.mark.parametrize("which", ["size", "sha", "encoding", "content", "mode", "missing"])
def test_original_blob_values_checked(tmp_path, monkeypatch, which):
    c = fixture(tmp_path, monkeypatch); entries = m.validate_tree(c["tree"], c["tree"]["sha"])
    logical = m.adapter.CALENDAR_PATH
    response = c["client"].responses[f"{m.API_PREFIX}/git/blobs/{entries[logical]['sha']}"]
    if which == "size": response["size"] += 1
    elif which == "sha": response["sha"] = "0"*40
    elif which == "encoding": response["encoding"] = "utf8"
    elif which == "content": response["content"] = "not_base64!"
    elif which == "mode": entries[logical]["mode"] = "120000"
    else: entries.pop(logical)
    with pytest.raises(m.ImportBlocked): m.load_blob(c["client"], entries, logical)


@pytest.mark.parametrize("which", ["nonempty", "relative", "symlink", "file"])
def test_source_root_safety(tmp_path, monkeypatch, which):
    c = fixture(tmp_path, monkeypatch); root = c["output"]
    if which == "nonempty": (root / "sentinel").write_bytes(b"preserve")
    elif which == "relative": root = Path("relative")
    elif which == "symlink": root = tmp_path / "alias"; root.symlink_to(c["output"], target_is_directory=True)
    else: root = tmp_path / "file"; root.write_bytes(b"preserve")
    with pytest.raises(m.ImportBlocked): m.import_p0_sources(root, expected_run_id=RUN_ID, github_client=c["client"])
    assert c["client"].requests == []


@pytest.mark.parametrize("value", [None, 1234, True, "latest", "0", "01", "1/2", "1?token=x"])
def test_explicit_id_required(value):
    with pytest.raises(m.ImportBlocked): m.exact_id(value)


@pytest.mark.parametrize("which", ["run", "jobs", "artifact", "source", "extra", "code"])
def test_late_source_or_evidence_mutation_fails(tmp_path, monkeypatch, which):
    c = fixture(tmp_path, monkeypatch)
    original = c["client"].get_json
    counts = {}
    def get(api):
        counts[api] = counts.get(api, 0) + 1
        if counts[api] == 2 and api == c["api_base"]:
            if which == "run": c["run"]["updated_at"] = "2026-09-14T11:11:00Z"
            elif which == "jobs": c["jobs"][0]["id"] += 100
            elif which == "artifact": c["artifact"]["updated_at"] = "2026-09-14T11:05:02Z"
            elif which == "source": (c["output"] / m.adapter.CALENDAR_PATH).write_bytes(b"changed")
            elif which == "extra": (c["output"] / "extra").write_bytes(b"extra")
            else: monkeypatch.setattr(m, "ADAPTER_SHA", "0"*64)
        return original(api)
    c["client"].get_json = get
    with pytest.raises((m.ImportBlocked, m.adapter.DSourceBlocked)): execute(c)


class Response:
    def __init__(self, body, status=200): self.body, self.status = body, status
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, size): return self.body[:size]


def test_api_redirect_token_is_never_forwarded(monkeypatch):
    token = "synthetic-only-token"
    requests = []
    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            assert timeout == 20 and request.get_method() == "GET"
            if len(requests) == 1:
                raise urllib.error.HTTPError(request.full_url, 302, "redirect", {"Location": "https://artifact.example.blob.core.windows.net/x?signature=secret"}, None)
            return Response(b"zipbytes")
    monkeypatch.setattr(m.urllib.request, "build_opener", lambda *args: Opener())
    client = m.GitHubReadClient(token)
    assert client.get_artifact_zip(9) == b"zipbytes"
    assert requests[0].get_header("Authorization") == "Bearer " + token
    assert requests[1].get_header("Authorization") is None
    assert client.calls == 2


@pytest.mark.parametrize("url", ["http://a.blob.core.windows.net/x", "https://api.github.com/x", "https://evil.test/x",
    "https://blob.core.windows.net/x", "https://a.blob.core.windows.net@evil.test/x", "https://a.blob.core.windows.net:444/x",
    "https://a.blob.core.windows.net/x#fragment"])
def test_signed_artifact_url_allowlist_rejects(url):
    client = m.GitHubReadClient("synthetic-token")
    with pytest.raises(m.ImportBlocked): client._open(url, authorization=False, limit=100)


@pytest.mark.parametrize("which", ["second_redirect", "oversize", "token", "error", "budget"])
def test_transport_fails_closed_without_server_text(monkeypatch, which):
    token = "synthetic-sensitive-token"
    class Opener:
        def open(self, request, timeout):
            if which in ("second_redirect", "error"):
                raise urllib.error.HTTPError(request.full_url, 302 if which == "second_redirect" else 403,
                    "SERVER SECRET " + token, {"Location": "https://evil.test"}, None)
            return Response(token.encode() if which == "token" else b"x"*101)
    monkeypatch.setattr(m.urllib.request, "build_opener", lambda *args: Opener())
    client = m.GitHubReadClient(token)
    if which == "budget": client.calls = m.MAX_API_CALLS
    with pytest.raises(m.ImportBlocked) as exc:
        client._open("https://a.blob.core.windows.net/x?private", authorization=False, limit=100)
    assert token not in str(exc.value) and "private" not in str(exc.value)


@pytest.mark.parametrize("body", [b'{"a":1,"a":2}', b'{"a":NaN}', b'[]', b'invalid'])
def test_strict_json(body):
    with pytest.raises(m.ImportBlocked): m.parse_json(body)


def test_cli_receipt_outside_source_no_secret_persistence(tmp_path, monkeypatch, capsys):
    c = fixture(tmp_path, monkeypatch)
    monkeypatch.setenv(m.TOKEN_ENV, "synthetic-private")
    monkeypatch.setattr(m, "GitHubReadClient", lambda token: c["client"])
    receipt_path = tmp_path.resolve() / "import_receipt.json"
    assert m.main(["--output", str(c["output"]), "--expected-run-id", RUN_ID, "--receipt", str(receipt_path)]) == 0
    report = json.loads(receipt_path.read_bytes())
    assert report["injected_client_for_test"] is True
    assert "synthetic-private" not in receipt_path.read_text() + capsys.readouterr().out
    assert len([p for p in c["output"].rglob("*") if p.is_file()]) == 28
    with pytest.raises(m.ImportBlocked): m.main(["--output", str(c["output"]), "--expected-run-id", RUN_ID, "--receipt", str(receipt_path)])


def test_receipt_inside_source_rejected_before_token(tmp_path, monkeypatch):
    output = tmp_path.resolve() / "source"; output.mkdir()
    monkeypatch.delenv(m.TOKEN_ENV, raising=False)
    with pytest.raises(m.ImportBlocked, match="OUTSIDE_SOURCE"):
        m.main(["--output", str(output), "--expected-run-id", RUN_ID, "--receipt", str(output / "receipt.json")])


@pytest.mark.parametrize("which", ["existing", "symlink", "parent_symlink", "traversal"])
def test_exclusive_nofollow_writer_never_overwrites(tmp_path, which):
    root = tmp_path.resolve() / "root"; root.mkdir()
    outside = tmp_path.resolve() / "outside"; outside.mkdir()
    sentinel = outside / "sentinel"; sentinel.write_bytes(b"preserve")
    logical = "file"
    if which == "existing": (root / logical).write_bytes(b"preserve")
    elif which == "symlink": (root / logical).symlink_to(sentinel)
    elif which == "parent_symlink": (root / "parent").symlink_to(outside, target_is_directory=True); logical = "parent/sentinel"
    else: logical = "../outside/sentinel"
    with pytest.raises((m.ImportBlocked, OSError)): m.write_new(root, logical, b"new")
    assert sentinel.read_bytes() == b"preserve"
    if which == "existing": assert (root / logical).read_bytes() == b"preserve"


def test_actual_publisher_function_alias_is_guarded(monkeypatch):
    monkeypatch.setattr(m.adapter.publisher, "build_primary_d_runtime_index", lambda *a, **kw: {})
    with pytest.raises(m.ImportBlocked, match="P0_CHECKER_IMPORT_ORIGIN"): m.code_guard()


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_unknown_future_file_is_never_read_or_recursed(tmp_path, monkeypatch, kind):
    c = fixture(tmp_path, monkeypatch)
    report = execute(c)
    unknown = c["output"] / ("future_outcomes_20260914.json" if kind == "file" else "future_results")
    if kind == "directory":
        unknown.mkdir()
        (unknown / "future_outcomes_20260914.json").write_bytes(b"MUST_NOT_READ")
    else:
        unknown.write_bytes(b"MUST_NOT_READ")
    original_read, original_scan = m.read, m.os.scandir
    def read_guard(p, *args, **kwargs):
        assert Path(p) != unknown and unknown not in Path(p).parents, "FUTURE_OUTCOME_CONTENT_WAS_READ"
        return original_read(p, *args, **kwargs)
    def scan_guard(p):
        assert Path(p) != unknown and unknown not in Path(p).parents, "UNREGISTERED_DIRECTORY_WAS_SCANNED"
        return original_scan(p)
    monkeypatch.setattr(m, "read", read_guard)
    monkeypatch.setattr(m.os, "scandir", scan_guard)
    with pytest.raises(m.ImportBlocked, match="UNREGISTERED_SOURCE"):
        m.inventory(c["output"], {b["path"]: b for b in report["source_file_bindings"]})


def test_late_unknown_future_file_after_code_guard_not_read(tmp_path, monkeypatch):
    c = fixture(tmp_path, monkeypatch)
    unknown = c["output"] / "future_outcomes_20260914.json"
    original_code, original_read = m.code_guard, m.read
    calls = 0
    def guard():
        nonlocal calls
        calls += 1
        value = original_code()
        if calls == 2: unknown.write_bytes(b"MUST_NOT_READ")
        return value
    def read_guard(p, *args, **kwargs):
        assert Path(p) != unknown, "FUTURE_OUTCOME_CONTENT_WAS_READ"
        return original_read(p, *args, **kwargs)
    monkeypatch.setattr(m, "code_guard", guard)
    monkeypatch.setattr(m, "read", read_guard)
    with pytest.raises(m.ImportBlocked, match="UNREGISTERED_SOURCE_FILE"): execute(c)
