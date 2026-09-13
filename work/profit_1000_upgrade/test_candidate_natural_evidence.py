"""Synthetic HTTP preservation only. No real run, token, or source authority."""
import base64
from copy import deepcopy
import io
import json
from pathlib import Path
import socket
import urllib.error
import urllib.parse

import pytest

from work.profit_1000_upgrade import candidate_natural_evidence as m
from work.profit_1000_upgrade import test_candidate_natural_publication as fixture

TOKEN = "synthetic-only-no-credential"


class Response:
    status = 200
    def __init__(self, raw): self.stream = io.BytesIO(raw)
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, size): return self.stream.read(size)


class Opener:
    def __init__(self, case):
        self.case, self.requests = case, []
    def open(self, request, *, timeout):
        assert request.get_method() == "GET" and timeout == 20
        self.requests.append(request)
        url = urllib.parse.urlsplit(request.full_url)
        if url.hostname == "api.github.com":
            assert request.has_header("Authorization")
            path = url.path + ("?" + url.query if url.query else "")
            if path.endswith("/zip"):
                identity = int(path.rsplit("/", 2)[1])
                raise urllib.error.HTTPError(request.full_url, 302, "Found", {
                    "Location": f"https://artifact.actions.githubusercontent.com/{identity}?sig=DO_NOT_PERSIST"}, None)
            return Response(m.gh.json_bytes(self.case["client"].responses[path]))
        assert url.hostname == "artifact.actions.githubusercontent.com" and not request.has_header("Authorization")
        return Response(self.case["client"].archives[int(url.path.strip("/"))])


@pytest.fixture
def case(tmp_path, monkeypatch):
    value = fixture.make_case(tmp_path, monkeypatch)
    monkeypatch.setattr(socket, "socket", lambda *a, **kw: pytest.fail("NETWORK_FORBIDDEN"))
    value["opener"] = Opener(value)
    value["destination"] = tmp_path.resolve() / "capsule"
    return value


def capture(case):
    return m.capture_evidence(case["destination"], freeze_run_id=fixture.RUN_ID, token=TOKEN, transport=case["opener"])


def materials(case):
    result = capture(case)
    root = Path(result["output_root"])
    raw = (root / "manifest.json").read_bytes()
    manifest = m.gh.parse_json(raw)
    bodies = {b["path"]: (root / b["path"]).read_bytes() for b in manifest["files"]}
    return result, raw, manifest, bodies


def test_complete_original_transport_preservation_test_only(case):
    result, raw, manifest, bodies = materials(case)
    assert result["status"] == "LOCAL_UNPUBLISHED_OBSERVATION_TEST_ONLY"
    assert result["test_transport_injected"] is True
    assert len(result["files"]) <= 64 and sum(b["bytes"] for b in result["files"]) <= 64 * 1024 * 1024
    assert result["files"][-1]["path"] == "manifest.json"
    assert m.verify_materials(raw, bodies, expected_manifest_sha256=m.sha(raw)) == manifest
    checked = m.verify_local_evidence(case["destination"], expected_manifest_sha256=m.sha(raw))
    assert checked["status"] == "LOCAL_BYTE_INTEGRITY_ONLY_NOT_PUBLICATION_ADMISSION"
    assert len(manifest["http_observations"]) == len(case["opener"].requests) <= 50
    assert len(manifest["original_source_bindings"]) == 28
    assert manifest["large_zip_bodies_persisted"] is False
    assert all(not v for k, v in result.items() if k.endswith("_issued") or k.endswith("_allowed"))
    assert all(not b.startswith(b"PK") for b in bodies.values())
    joined = raw + b"".join(bodies.values())
    assert TOKEN.encode() not in joined and b"DO_NOT_PERSIST" not in joined and b"Authorization" not in joined
    assert b"artifact.actions.githubusercontent.com" not in joined
    for r in manifest["http_observations"]:
        if r["kind"] == "API_JSON":
            expected = m.gh.json_bytes(case["client"].responses[r["api_path"]])
            assert bodies[r["retained_body"]["path"]] == expected
        else: assert r["retained_body"] is None
    assert bodies[manifest["pages_revision"]["path"]] == m._revision(case["client"].archives[12345])
    assert len(manifest["files"]) < len([r for r in manifest["http_observations"] if r["kind"] == "API_JSON"]) + 2


@pytest.mark.parametrize("field,value", [("conclusion", "failure"), ("event", "push"), ("run_attempt", 2), ("head_branch", "other")])
def test_failed_original_verifier_never_creates_capsule(case, field, value):
    case["run"][field] = value
    with pytest.raises(ValueError): capture(case)
    assert not case["destination"].exists()


@pytest.mark.parametrize("secret", [TOKEN, "ghp_" + "a" * 40, "https://x.actions.githubusercontent.com/data", "https://example.com/a?sig=secret", "https://example.com/a?X-Amz-Credential=secret"])
def test_safe_json_secret_guard(secret):
    with pytest.raises(ValueError): m._safe_body(m.gh.json_bytes({"irrelevant": secret}), TOKEN)


def test_json_escaped_token_not_persisted(case):
    case["run"]["ignored"] = TOKEN
    original = m.gh.json_bytes
    def encoded(value):
        raw = original(value)
        return raw.replace(TOKEN.encode(), b"".join(("\\u%04x" % ord(c)).encode() for c in TOKEN))
    # Byte-preserving transport sees JSON escapes, frozen verifier ignores extra field.
    monkey = pytest.MonkeyPatch(); monkey.setattr(m.gh, "json_bytes", encoded)
    try:
        with pytest.raises(ValueError, match="CREDENTIAL"): capture(case)
    finally: monkey.undo()
    assert not case["destination"].exists()


@pytest.mark.parametrize("inner", [TOKEN.encode(), m.gh.json_bytes({"x": TOKEN}), m.gh.json_bytes({"x": "ghp_" + "z"*30})])
def test_blob_encoded_secret_guard(inner):
    raw = m.gh.json_bytes({"encoding": "base64", "content": base64.b64encode(inner).decode()})
    with pytest.raises(ValueError, match="CREDENTIAL"): m._safe_body(raw, TOKEN, blob_response=True)


@pytest.mark.parametrize("path", ["/user", m.gh.API_PREFIX + "/compare/abc...main", m.gh.API_PREFIX + "/issues", m.gh.API_PREFIX + "/contents/future_outcomes.json"])
def test_unregistered_routes_rejected(path):
    with pytest.raises(ValueError): m._route(path)


@pytest.mark.parametrize("kind", ["raw", "sha", "size", "unknown", "date", "grant", "revision", "source", "archive", "ordinal", "code"])
def test_materials_tampering_rejected(case, kind):
    _, raw, manifest, bodies = materials(case)
    if kind == "raw": bodies[next(iter(bodies))] += b" "
    elif kind == "sha": manifest["files"][0]["sha256"] = "0"*64
    elif kind == "size": manifest["files"][0]["bytes"] = True
    elif kind == "unknown": bodies["future_outcomes.json"] = b"FUTURE"
    elif kind == "date": manifest["signal_date"] = "20260911"
    elif kind == "grant": manifest["natural_forward_admission_issued"] = True
    elif kind == "revision": manifest["pages_revision"] = manifest["native_observation"]
    elif kind == "source": manifest["original_source_bindings"].pop()
    elif kind == "archive": manifest["pages_archive_evidence"]["digest"] = "sha256:"+"0"*64
    elif kind == "ordinal": manifest["http_observations"][0]["ordinal"] = True
    elif kind == "code": manifest["publication_verifier_sha256"] = "0"*64
    raw = m.gh.json_bytes(manifest)
    with pytest.raises((ValueError, KeyError)): m.verify_materials(raw, bodies, expected_manifest_sha256=m.sha(raw))


@pytest.mark.parametrize("kind", ["existing", "inside_code", "symlink", "empty_token"])
def test_output_scope_before_network(case, kind, monkeypatch, tmp_path):
    if kind == "existing": case["destination"].mkdir()
    elif kind == "inside_code": case["destination"] = m.ROOT / "forbidden-capsule"
    elif kind == "symlink":
        alias = tmp_path / "alias"; alias.symlink_to(tmp_path, target_is_directory=True)
        case["destination"] = alias / "capsule"
    if kind == "empty_token":
        with pytest.raises(ValueError): m.capture_evidence(case["destination"], freeze_run_id=fixture.RUN_ID, token="", transport=case["opener"])
    else:
        with pytest.raises(ValueError): capture(case)
    assert not case["opener"].requests


@pytest.mark.parametrize("kind", ["unknown", "directory", "symlink", "hardlink", "body", "manifest"])
def test_local_inventory_and_immutable_bytes(case, monkeypatch, kind):
    result, _, manifest, _ = materials(case); root = case["destination"]
    if kind == "unknown": (root / "future_outcomes_20260915.json").write_bytes(b"DO_NOT_READ")
    elif kind == "directory": (root / "future").mkdir()
    elif kind == "symlink": (root / "future").symlink_to(root / "manifest.json")
    elif kind == "hardlink": (root.parent / "alias-body").hardlink_to(root / manifest["files"][0]["path"])
    elif kind == "body": (root / manifest["files"][0]["path"]).write_bytes(b"{}")
    elif kind == "manifest": (root / "manifest.json").write_bytes(b"{}")
    original = m.gh.read
    def guarded(path, *a, **kw):
        assert "future" not in str(path), "FUTURE_BODY_READ"
        return original(path, *a, **kw)
    monkeypatch.setattr(m.gh, "read", guarded)
    with pytest.raises(ValueError): m.verify_local_evidence(root, expected_manifest_sha256=result["manifest_sha256"])


@pytest.mark.parametrize("kind", ["body_limit", "total_limit", "count", "capture_limit"])
def test_budgets_fail_before_write(case, monkeypatch, kind):
    monkeypatch.setattr(m, {"body_limit": "MAX_FILE_BYTES", "total_limit": "MAX_TOTAL_BYTES", "count": "MAX_FILES", "capture_limit": "MAX_CAPTURE_BYTES"}[kind], 1)
    with pytest.raises(ValueError): capture(case)
    assert not case["destination"].exists()


def test_code_guard_before_any_network(case, monkeypatch):
    monkeypatch.setattr(m, "SELF_SHA", "0"*64)
    with pytest.raises(ValueError, match="CODE_CHANGED"): capture(case)
    assert not case["opener"].requests


def test_late_unknown_inventory_change_fails(case, monkeypatch):
    result = capture(case); original = m.code_guard; count = 0
    def changed():
        nonlocal count
        count += 1
        value = original()
        if count == 2: (case["destination"] / "future_outcomes.json").write_bytes(b"NOT_READ")
        return value
    monkeypatch.setattr(m, "code_guard", changed)
    with pytest.raises(ValueError, match="UNKNOWN_EVIDENCE_FILE_NOT_READ"):
        m.verify_local_evidence(case["destination"], expected_manifest_sha256=result["manifest_sha256"])


def test_local_integrity_does_not_reopen_expired_archives(case, monkeypatch):
    result = capture(case)
    monkeypatch.setattr(m.zipfile, "ZipFile", lambda *a, **kw: pytest.fail("ARCHIVE_REOPENED"))
    monkeypatch.setattr(m.gh.GitHubReadClient, "get_json", lambda *a, **kw: pytest.fail("NETWORK"))
    verified = m.verify_local_evidence(case["destination"], expected_manifest_sha256=result["manifest_sha256"])
    assert verified["natural_forward_admission_issued"] is False


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'not-json', b''])
def test_original_json_strict_safety(raw):
    with pytest.raises(ValueError): m._safe_body(raw)


def test_native_observation_is_preserved_not_relabelled(case):
    _, _, manifest, bodies = materials(case)
    observation = m.gh.parse_json(bodies[manifest["native_observation"]["path"]])
    assert observation["research_prospective_publication_observed"] is True
    assert observation["injected_client_for_test"] is False
    # This is an untouched original verifier result from an explicit fake transport.
    # The enclosing TEST flag is mandatory; extracting this body is not authority.
    assert manifest["test_transport_injected"] is True and manifest["natural_forward_admission_issued"] is False
