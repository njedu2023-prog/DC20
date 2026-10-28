"""Synthetic dual-run publication/capsule wiring, never live admission."""
from copy import deepcopy
from pathlib import Path
import io
import gzip
import base64
import socket
import urllib.error
import urllib.parse

import pytest

from work.profit_1000_upgrade import candidate_natural_evidence as capture
from work.profit_1000_upgrade import candidate_natural_evidence_publication as issuer
from work.profit_1000_upgrade import test_candidate_natural_p0_continuation as pair
from work.profit_1000_upgrade import test_candidate_natural_publication as publication_fixture
from work.profit_1000_upgrade import test_candidate_natural_evidence as old_capture_tests
from work.profit_1000_upgrade import test_candidate_natural_evidence_publication as observer_fixture

publication = capture.publication
gh = capture.gh
TOKEN = "synthetic-continuation-only"


def make_case(tmp_path, monkeypatch, *, suspension=False):
    c = pair.fixture(tmp_path, monkeypatch, suspension=suspension)
    local, _ = publication.code_guard()
    parent_files = {**c["all_new"], **local, "protected.txt": b"unchanged formal evidence"}
    parent_tree = pair.git_tree(parent_files)
    responses = c["client"].responses
    responses[gh.API_PREFIX + "/git/commits/" + publication_fixture.CODE_HEAD] = {
        "sha": publication_fixture.CODE_HEAD, "tree": {"sha": parent_tree["sha"]}, "parents": [{"sha": pair.HEAD}]}
    responses[gh.API_PREFIX + "/git/trees/" + parent_tree["sha"] + "?recursive=1"] = parent_tree
    responses[gh.API_PREFIX + "/git/ref/heads/main"]["object"]["sha"] = publication_fixture.CODE_HEAD
    imported = gh.import_redeployed_p0_sources(c["output"], expected_run_id=pair.R1,
        original_generation_run_id=pair.R0, original_cas_commit=pair.CAS, github_client=c["client"], clock=lambda: pair.NOW)
    imported.update(injected_client_for_test=False, network_calls_performed=100)
    c["client"].archive = c["client"].archives[12345]
    # Reuse the established scoring fixture but replace only its explicitly
    # synthetic source API seam. Production validators remain active.
    with monkeypatch.context() as patch:
        patch.setattr(publication_fixture.p0, "fixture", lambda *args, **kw: c)
        patch.setattr(publication_fixture.p0, "execute", lambda source: deepcopy(imported))
        case = publication_fixture.make_case(tmp_path, monkeypatch)
    tree = pair.git_tree({**parent_files, **case["files"]})
    case["ack"]["tree_sha"] = tree["sha"]
    case["archive_files"]["publication.json"] = gh.json_bytes(case["ack"])
    archive = publication_fixture.zip_bytes(case["archive_files"])
    case["artifact"].update(size_in_bytes=len(archive), digest="sha256:" + gh.sha256(archive))
    res = case["client"].responses
    res[gh.API_PREFIX + "/git/commits/" + publication_fixture.CODE_HEAD] = {
        "sha": publication_fixture.CODE_HEAD, "tree": {"sha": parent_tree["sha"]}, "parents": [{"sha": pair.HEAD}]}
    res[gh.API_PREFIX + "/git/trees/" + parent_tree["sha"] + "?recursive=1"] = parent_tree
    res[gh.API_PREFIX + "/git/commits/" + publication_fixture.PUBLISHED]["tree"]["sha"] = tree["sha"]
    res[gh.API_PREFIX + "/git/trees/" + tree["sha"] + "?recursive=1"] = tree
    client = pair.Client(res, {**c["client"].archives, 900: archive})
    case.update(client=client, source_pair=c, imported=imported, parent_tree=parent_tree, tree=tree, archive=archive)
    return case


class Opener:
    def __init__(self, case): self.case, self.requests = case, []
    def open(self, request, *, timeout):
        assert request.get_method() == "GET" and timeout == 20
        self.requests.append(request)
        url = urllib.parse.urlsplit(request.full_url)
        if url.hostname == "api.github.com":
            assert request.has_header("Authorization")
            path = url.path + ("?" + url.query if url.query else "")
            if path.endswith("/zip"):
                artifact_id = int(path.rsplit("/", 2)[1])
                raise urllib.error.HTTPError(request.full_url, 302, "Found",
                    {"Location": f"https://artifact.actions.githubusercontent.com/{artifact_id}?sig=NOT_RETAINED"}, None)
            return old_capture_tests.Response(gh.json_bytes(self.case["client"].responses[path]))
        assert url.hostname == "artifact.actions.githubusercontent.com" and not request.has_header("Authorization")
        return old_capture_tests.Response(self.case["client"].archives[int(url.path.strip("/"))])


def captured_case(tmp_path, monkeypatch, *, suspension=False):
    case = make_case(tmp_path, monkeypatch, suspension=suspension)
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: pytest.fail("NETWORK_FORBIDDEN"))
    output = tmp_path / "dual-capsule"
    result = capture.capture_evidence(output, freeze_run_id=publication_fixture.RUN_ID,
        token=TOKEN, transport=Opener(case))
    raw = (output / "manifest.json").read_bytes()
    manifest = gh.parse_json(raw)
    bodies = {item["path"]: (output / item["path"]).read_bytes() for item in manifest["files"]}
    return case, result, raw, manifest, bodies


@pytest.mark.parametrize("suspension", [False, True])
def test_real_validators_replay_two_synthetic_runs_and_three_original_archives(tmp_path, monkeypatch, suspension):
    case, result, raw, manifest, bodies = captured_case(tmp_path, monkeypatch, suspension=suspension)
    assert result["test_transport_injected"] is True
    assert manifest["p0_provenance_kind"] == capture.CONTINUATION_KIND
    assert len([r for r in manifest["http_observations"] if r["kind"] == "ARTIFACT_ZIP"]) == 3
    assert manifest["original_generation_archive_bytes_persisted"] is True
    assert len(manifest["files"]) < 160
    assert len(manifest["original_source_bindings"]) == 28 + int(suspension)
    assert capture.verify_materials(raw, bodies, expected_manifest_sha256=capture.sha(raw)) == manifest
    observation = gh.parse_json(bodies[manifest["native_observation"]["path"]])
    assert observation["p0_continuation"]["original_generation_run"]["conclusion"] == "failure"
    assert observation["p0_continuation"]["successful_reuse_run"]["conclusion"] == "success"
    assert observation["original_p0_commit_sha"] == pair.CAS
    joined = raw + b"".join(bodies.values())
    assert TOKEN.encode() not in joined and b"NOT_RETAINED" not in joined


@pytest.mark.parametrize("mutation", ["wrong_kind", "missing_generation_body", "two_archives", "replaced_generation_archive",
    "wrong_reuse_run", "changed_source_main", "wrong_source_binding", "source_future_commit", "generation_blob_changed", "publication_blob_changed"])
def test_resealed_capsule_does_not_allow_provenance_substitution(tmp_path, monkeypatch, mutation):
    _, _, _, manifest, bodies = captured_case(tmp_path, monkeypatch)
    manifest = deepcopy(manifest)
    bodies = dict(bodies)
    def replace_body(binding, value):
        old = binding["path"]
        raw = gh.json_bytes(value)
        new = capture._ref(raw)
        bodies.pop(old)
        bodies[new["path"]] = raw
        manifest["files"] = [new if x["path"] == old else x for x in manifest["files"]]
        # All duplicate references must move, so failure tests provenance rather
        # than relying only on a stale outer content address.
        for record in manifest["http_observations"]:
            if (record.get("retained_body") or {}).get("path") == old:
                record.update(retained_body=new, body_sha256=capture.sha(raw), body_bytes=len(raw))
        binding.clear(); binding.update(new)
    if mutation == "wrong_kind": manifest["p0_provenance_kind"] = "SINGLE_RUN"
    elif mutation == "missing_generation_body": del manifest["generation_archive_body"]
    elif mutation == "two_archives":
        generation_id = manifest["generation_archive_evidence"]["id"]
        manifest["http_observations"] = [r for r in manifest["http_observations"] if r["artifact_id"] != generation_id]
        for i, record in enumerate(manifest["http_observations"], 1): record["ordinal"] = i
    elif mutation == "replaced_generation_archive":
        value = gh.parse_json(bodies[manifest["generation_archive_body"]["path"]])
        value["body"] = "Y2hhbmdlZA=="
        replace_body(manifest["generation_archive_body"], value)
    elif mutation in ("wrong_reuse_run", "changed_source_main", "wrong_source_binding"):
        value = gh.parse_json(bodies[manifest["native_observation"]["path"]])
        proof = value["p0_continuation"]
        if mutation == "wrong_reuse_run": proof["successful_reuse_run"]["id"] = int(pair.R0)
        elif mutation == "changed_source_main": proof["source_main_sha"] = pair.CAS
        else: proof["source_file_bindings"][0]["sha256"] = "0" * 64
        replace_body(manifest["native_observation"], value)
    else:
        route = (f"/repos/njedu2023-prog/a-top10/git/commits/{pair.PRED}" if mutation == "source_future_commit"
                 else next(r["api_path"] for r in manifest["http_observations"]
                     if r["kind"] == "API_JSON" and "/git/blobs/" in r["api_path"]) if mutation == "publication_blob_changed"
                 else gh.API_PREFIX + "/git/blobs/" + manifest["original_source_bindings"][0]["git_blob_sha1"])
        record = next(r for r in manifest["http_observations"] if r["api_path"] == route)
        binding = deepcopy(record["retained_body"])
        value = gh.parse_json(bodies[binding["path"]])
        if mutation == "source_future_commit": value["committer"]["date"] = "2099-01-01T00:00:00Z"
        else: value["content"] = "Y2hhbmdlZA=="
        replace_body(binding, value)
    raw = gh.json_bytes(manifest)
    with pytest.raises((ValueError, KeyError)):
        capture.verify_materials(raw, bodies, expected_manifest_sha256=capture.sha(raw))


@pytest.mark.parametrize("path", [
    "/repos/njedu2023-prog/other/git/commits/" + "a" * 40,
    "/repos/njedu2023-prog/a-top10/git/refs/heads/main",
    "/repos/njedu2023-prog/a-top10/git/commits/main",
    "/repos/njedu2023-prog/a-top10/git/commits/" + "a" * 40 + "?page=1",
])
def test_no_arbitrary_external_capsule_routes(path):
    with pytest.raises(ValueError):
        capture._route(path)



def test_only_continuation_scans_one_bounded_registered_gzip(monkeypatch):
    def envelope(raw):
        return gh.json_bytes({"encoding": "base64", "content": base64.b64encode(raw).decode(),
            "sha": gh.git_blob(raw), "size": len(raw)})
    raw = gzip.compress(b"signal_date,ts_code\n20260914,600100.SH\n", mtime=0)
    assert capture._safe_body(envelope(raw), blob_response=True, continuation_blob=True)
    with pytest.raises(ValueError): capture._safe_body(envelope(raw), blob_response=True)
    for bad in (raw + b"trailing", raw + raw, raw[:-1], b"\x1f\x8bbad"):
        with pytest.raises(ValueError):
            capture._safe_body(envelope(bad), blob_response=True, continuation_blob=True)
    with pytest.raises(ValueError, match="CREDENTIAL"):
        capture._safe_body(envelope(gzip.compress(TOKEN.encode(), mtime=0)), TOKEN,
            blob_response=True, continuation_blob=True)
    monkeypatch.setattr(capture, "MAX_FILE_BYTES", 512)
    with pytest.raises(ValueError, match="GZIP_BOUNDARY"):
        capture._safe_body(envelope(gzip.compress(b"x" * 513, mtime=0)),
            blob_response=True, continuation_blob=True)


def test_only_exact_pinned_model_bytes_are_retained_without_deserialization(monkeypatch):
    import pickle
    monkeypatch.setattr(pickle, "loads", lambda *a, **kw: pytest.fail("MODEL_DESERIALIZATION_FORBIDDEN"))
    raw = (capture.ROOT / "models/decision_three_engines/promotion.joblib").read_bytes()
    value = {"encoding": "base64", "content": base64.b64encode(raw).decode(),
        "sha": gh.git_blob(raw), "size": len(raw)}
    assert capture._safe_body(gh.json_bytes(value), blob_response=True, continuation_blob=True)
    with pytest.raises(ValueError): capture._safe_body(gh.json_bytes(value), blob_response=True)
    for key, changed in (("sha", "0" * 40), ("size", len(raw) + 1),
            ("content", base64.b64encode(raw + b"change").decode())):
        with pytest.raises(ValueError):
            capture._safe_body(gh.json_bytes({**value, key: changed}), blob_response=True, continuation_blob=True)


@pytest.mark.parametrize("suspension", [False, True])
def test_published_observer_replays_dual_run_capsule_without_live_authority(tmp_path, monkeypatch, suspension):
    case, result, raw, manifest, bodies = captured_case(tmp_path, monkeypatch, suspension=suspension)
    with monkeypatch.context() as patch:
        patch.setattr(observer_fixture.f, "make_case", lambda *a, **kw: case)
        patch.setattr(observer_fixture.capture_tests, "materials", lambda source: (result, raw, manifest, bodies))
        observer_case = observer_fixture.make_case(tmp_path, monkeypatch)
    observed = observer_fixture.run(observer_case)
    assert type(observed) is dict and observed["status"] == "SYNTHETIC_OBSERVER_CHECK_ONLY"
    assert observed["injected_client_for_test"] is True
    assert observed["research_prospective_publication_observed"] is False
    assert observed["p0_provenance_kind"] == capture.CONTINUATION_KIND
    assert observed["original_generation_run_id"] == int(pair.R0)
    assert observed["successful_reuse_run_id"] == int(pair.R1)
    assert observed["captured_continuation_pair_replayed"] is True
    assert all(not value for key, value in observed.items() if key.endswith("_allowed") or key.endswith("_issued"))


def test_real_registered_history_gzip_uses_its_exact_expansion_size(monkeypatch):
    raw = (capture.ROOT / gh.adapter.HISTORY_PATH).read_bytes()
    assert capture.sha(raw) == gh.adapter.HISTORY_SHA == capture.ORIGINAL_HISTORY_GZIP_SHA256
    assert len(raw) == 4_764_361
    assert len(gzip.decompress(raw)) == capture.ORIGINAL_HISTORY_EXPANDED_BYTES == 14_069_289
    def envelope(value):
        return gh.json_bytes({"encoding": "base64", "content": base64.b64encode(value).decode(),
            "sha": gh.git_blob(value), "size": len(value)})
    wrapped = envelope(raw)
    assert len(wrapped) < capture.MAX_FILE_BYTES
    assert capture._safe_body(wrapped, blob_response=True, continuation_blob=True)
    # An unregistered byte stream never receives the history-sized allowance.
    with monkeypatch.context() as m:
        m.setattr(gh.adapter, "HISTORY_SHA", "0" * 64)
        with pytest.raises(ValueError, match="GZIP_BOUNDARY"):
            capture._safe_body(wrapped, blob_response=True, continuation_blob=True)
    for changed in (raw + b"trailing", raw + raw, raw[:-1]):
        with pytest.raises(ValueError):
            capture._safe_body(envelope(changed), blob_response=True, continuation_blob=True)
    for size in (14_069_288, 14_069_290):
        with monkeypatch.context() as m:
            m.setattr(capture, "ORIGINAL_HISTORY_EXPANDED_BYTES", size)
            with pytest.raises(ValueError, match="GZIP_BOUNDARY"):
                capture._safe_body(wrapped, blob_response=True, continuation_blob=True)
