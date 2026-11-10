from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from top10decision.decision import suspension_evidence_registry as registry


DAY = "20261111"


def binding(path, body):
    return {"path": path, "sha256": registry.sha(body), "git_blob_sha1": hashlib.sha1(
        b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest(), "git_mode": "100644"}


def reader(files):
    return lambda path: (files[path], binding(path, files[path]))


def genesis():
    version = {"schema_version": registry.SCHEMA, "sequence": 0, "parent_sha256": None,
               "added_record_sha256": [], "total_records": 0}
    raw = registry.encode(version)
    return {registry.object_path("versions", registry.sha(raw)): raw,
            registry.INDEX_PATH: registry.encode({"schema_version": registry.SCHEMA,
               "active_from": registry.ACTIVE_FROM, "version_sha256": registry.sha(raw)})}


def fact(code="000001.SZ", start="20261102", resume="20261105", published="20261104"):
    pdf = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj <<>> endobj\n%%EOF\n"
    item = {"schema_version": "dc20_suspension_fact_v1", "ts_code": code, "start_date": start,
            "resume_date": resume, "sources": [{"url": "https://static.cninfo.com.cn/notice.pdf",
                "published_date": published, "pdf_sha256": registry.sha(pdf)}]}
    return item, pdf


def append(files, item, pdf):
    files = copy.deepcopy(files)
    index = json.loads(files[registry.INDEX_PATH]); old = index["version_sha256"]
    previous = json.loads(files[registry.object_path("versions", old)])
    raw = registry.encode(item); digest = registry.sha(raw)
    files[registry.object_path("records", digest)] = raw
    files[registry.object_path("pdfs", registry.sha(pdf))] = pdf
    version = {"schema_version": registry.SCHEMA, "sequence": previous["sequence"] + 1,
               "parent_sha256": old, "added_record_sha256": [digest], "total_records": previous["total_records"] + 1}
    raw = registry.encode(version); digest = registry.sha(raw)
    files[registry.object_path("versions", digest)] = raw
    index["version_sha256"] = digest; files[registry.INDEX_PATH] = registry.encode(index)
    return files


def test_empty_registry_and_append_are_bound_without_changing_legacy_baseline():
    empty = genesis(); item, pdf = fact(); files = append(empty, item, pdf)
    records, declaration = registry.load_registry(reader(files), signal_date=DAY)
    assert records == [{k: v for k, v in item.items() if k != "schema_version"}]
    assert set(registry.validate_declaration(declaration)) == set(files)
    transition = registry.validate_append_transition(reader(empty), reader(files), signal_date=DAY,
        expected_previous_index_sha256=registry.sha(empty[registry.INDEX_PATH]))
    assert transition["source_head_compare_and_swap_required"]
    assert registry.load_registry(reader(empty), signal_date=DAY)[0] == []


def test_rollback_and_stale_cas_are_rejected():
    empty = genesis(); files = append(empty, *fact())
    with pytest.raises(registry.SuspensionRegistryError, match="append directly"):
        registry.validate_append_transition(reader(files), reader(empty), signal_date=DAY,
            expected_previous_index_sha256=registry.sha(files[registry.INDEX_PATH]))
    with pytest.raises(registry.SuspensionRegistryError, match="CAS"):
        registry.validate_append_transition(reader(empty), reader(files), signal_date=DAY,
            expected_previous_index_sha256="0" * 64)


def test_more_than_32_legal_versions_remain_usable():
    files = genesis()
    for code in range(40):
        files = append(files, *fact(code=f"{code:06d}.SZ"))
    records, declaration = registry.load_registry(reader(files), signal_date=DAY)
    assert len(records) == 40
    assert len(declaration["files"]) == 83


@pytest.mark.parametrize("mutation", ["bad_host", "future", "invalid_pdf", "duplicate", "overlap", "wrong_record_hash"])
def test_rejects_false_or_conflicting_evidence(mutation):
    item, pdf = fact(); files = genesis()
    if mutation == "bad_host": item["sources"][0]["url"] = "https://official.example/notice.pdf"
    if mutation == "future": item["sources"][0]["published_date"] = "20261112"
    if mutation == "invalid_pdf": pdf = b"not an official PDF"; item["sources"][0]["pdf_sha256"] = registry.sha(pdf)
    files = append(files, item, pdf)
    if mutation == "duplicate": files = append(files, item, pdf)
    if mutation == "overlap":
        item = copy.deepcopy(item); item["start_date"] = "20261103"; files = append(files, item, pdf)
    if mutation == "wrong_record_hash":
        path = next(p for p in files if "/records/" in p); files[path] += b" "
    with pytest.raises(registry.SuspensionRegistryError):
        registry.load_registry(reader(files), signal_date=DAY)


def test_future_interval_is_not_used_but_cannot_hide_corruption():
    item, pdf = fact(start="20261112", resume="20261116", published="20261115")
    files = append(genesis(), item, pdf)
    assert registry.load_registry(reader(files), signal_date=DAY)[0] == []
    path = next(p for p in files if "/pdfs/" in p); files[path] = b"bad"
    with pytest.raises(registry.SuspensionRegistryError): registry.load_registry(reader(files), signal_date=DAY)


def test_incomplete_binding_wrong_blob_and_path_escape_rejected():
    files = genesis()
    with pytest.raises(KeyError): registry.load_registry(reader({}), signal_date=DAY)
    with pytest.raises(registry.SuspensionRegistryError, match="HEAD"):
        registry.load_registry(lambda path: (files[path], {**binding(path, files[path]), "git_blob_sha1": "0" * 40}), signal_date=DAY)
    for path in [registry.PREFIX + "../index.json", registry.PREFIX + "records/../x.json", registry.PREFIX + "pdfs/x.pdf"]:
        assert not registry.is_registry_path(path)
    with pytest.raises(registry.SuspensionRegistryError):
        registry.load_registry(reader(files), signal_date="20261110")


def test_new_record_cannot_overlap_frozen_legacy_fact():
    item, pdf = fact(); files = append(genesis(), item, pdf)
    legacy = [{k: v for k, v in item.items() if k != "schema_version"}]
    with pytest.raises(registry.SuspensionRegistryError, match="overlapping"):
        registry.load_registry(reader(files), signal_date=DAY, legacy_records=legacy)


def test_publisher_new_registry_binding_and_old_shape(tmp_path):
    from scripts import publish_primary_three_rank as p0
    files = genesis(); baseline = "data/market/confirmed_suspensions.json"
    files[baseline] = registry.encode({"schema_version": "dc20_confirmed_suspensions_v1", "suspensions": []})
    for relative, raw in files.items():
        path = tmp_path / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
    obj = p0.PrimaryDReadOnlyEngine.__new__(p0.PrimaryDReadOnlyEngine)
    obj.config = SimpleNamespace(root=tmp_path); obj._primary_signal_date = DAY
    obj._primary_consumed = {}; obj._confirmed_suspensions = None
    obj._primary_verifier = SimpleNamespace(bind=lambda path, **_: binding(path, files[path]))
    # No stock/streak computation has happened, as on an empty candidate day.
    declared = obj.consumed_bindings()
    assert len(declared) == 1
    assert obj._suspension_evidence() == []
    assert len(obj._primary_consumed["confirmed_suspensions"]["registry"]["files"]) == 2
    obj._primary_signal_date = "20261110"; obj._confirmed_suspensions = None; obj._primary_consumed = {}
    assert obj._suspension_evidence() == []
    assert "registry" not in obj._primary_consumed["confirmed_suspensions"]


def test_archive_path_and_pdf_capture_require_exact_bound_registry():
    from work.profit_1000_upgrade import candidate_natural_publication as publication
    from work.profit_1000_upgrade import candidate_natural_evidence as evidence
    item, pdf = fact(); path = registry.object_path("pdfs", registry.sha(pdf))
    publication._source_path(path, DAY)
    with pytest.raises(ValueError): publication._source_path(path, "20261110")
    b = binding(path, pdf)
    envelope = json.dumps({"encoding": "base64", "content": base64.b64encode(pdf).decode(),
        "size": len(pdf), "sha": b["git_blob_sha1"]}).encode()
    with pytest.raises(ValueError, match="REGISTERED_PDF"):
        evidence._safe_body(envelope, blob_response=True)
    assert evidence._safe_body(envelope, blob_response=True, registry_pdf_blobs={b["git_blob_sha1"]})["size"] == len(pdf)
    manifest = {"signal_date": DAY, "original_source_bindings": [b]}
    assert evidence._file_limit(manifest) == evidence.MAX_FILES + 3
    manifest["signal_date"] = "20261110"
    with pytest.raises(ValueError): evidence._file_limit(manifest)


def test_40_versions_flow_through_registration_source_inventory_and_archive(tmp_path, monkeypatch):
    """Synthetic date migration only; no historical production freeze is edited."""
    import io
    import zipfile
    from work.profit_1000_upgrade import candidate_d_source_adapter as adapter
    from work.profit_1000_upgrade import candidate_natural_p0_github as importer
    from work.profit_1000_upgrade import candidate_natural_publication as publication
    from work.profit_1000_upgrade import candidate_natural_evidence as evidence
    from work.profit_1000_upgrade.test_candidate_d_source_adapter import make_case, update_receipt, run
    case = make_case(tmp_path, monkeypatch)
    monkeypatch.setattr(registry, "ACTIVE_FROM", case["day"])
    files = genesis()
    for code in range(40):
        files = append(files, *fact(code=f"{900000 + code:06d}.SH", start="20260901", resume="20260903", published="20260902"))
    _, declaration = registry.load_registry(reader(files), signal_date=case["day"])
    baseline = registry.encode({"schema_version": "dc20_confirmed_suspensions_v1", "suspensions": []})
    files[adapter.SUSPENSION_PATH] = baseline
    item = {**binding(adapter.SUSPENSION_PATH, baseline), "table": "confirmed_suspensions",
            "used_intervals": [], "registry": declaration}
    case["receipt"]["inputs"]["runtime_consumed_market_files"].append(item)
    for relative, raw in files.items():
        path = case["root"] / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
    update_receipt(case)
    registered, _, _, _ = adapter._registration(case["receipt"], case["contract"], case["day"])
    assert len(registered) == 25 + 83
    sources = {p: (case["root"] / p).read_bytes() for p in set(registered) | set(adapter._p0_paths(case["day"]).values())}
    assert importer.validate_original_source_inventory(case["receipt"], case["contract"], sources, case["day"]) == 112
    assert len(run(case)["source_file_bindings"]) == 112
    bindings = [{**binding(p, raw), "bytes": len(raw)} for p, raw in sorted(sources.items())]
    manifest = {"signal_date": case["day"], "original_source_bindings": bindings}
    assert evidence._registry_allowance(manifest) == 85
    assert evidence._file_limit(manifest) == evidence.MAX_FILES + 85
    client = importer.GitHubReadClient.__new__(importer.GitHubReadClient)
    client.enable_registry_reads(case["receipt"], case["contract"], case["day"])
    assert client._registry_read_allowance == 85
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("publication.json", json.dumps({"signal_date": case["day"]}))
        archive.writestr("p0_source_receipt.json", json.dumps({"source_file_bindings": bindings}))
        archive.writestr("local_freeze_receipt.json", "{}")
        archive.writestr(f"candidate_natural_forward/day_{case['day']}.json", "{}")
        for p, raw in sources.items(): archive.writestr("sources/" + p, raw)
    assert publication.read_archive(buf.getvalue())["sources"] == sources
    broken = copy.deepcopy(sources); broken.pop(registry.INDEX_PATH)
    with pytest.raises(ValueError):
        importer.validate_original_source_inventory(case["receipt"], case["contract"], broken, case["day"])


def test_registry_must_exist_in_original_p0_source_head():
    from work.profit_1000_upgrade import candidate_natural_p0_github as importer
    files = genesis(); _, declaration = registry.load_registry(reader(files), signal_date=DAY)
    entries = {path: {"path": path, "type": "blob", "mode": "100644", "sha": binding(path, raw)["git_blob_sha1"], "size": len(raw)}
               for path, raw in files.items()}
    baseline = "data/market/confirmed_suspensions.json"; raw = b"{}"
    entries[baseline] = {"path": baseline, "type": "blob", "mode": "100644", "sha": binding(baseline, raw)["git_blob_sha1"], "size": 2}
    receipt = {"inputs": {"git_head": "a" * 40, "runtime_consumed_market_files": [
        {**binding(baseline, raw), "table": "confirmed_suspensions", "used_intervals": [], "registry": declaration}]}}
    from work.profit_1000_upgrade.test_candidate_natural_p0_github import git_tree
    class Source:
        def __init__(self, source_files): self.tree = git_tree(source_files)
        def get_json(self, route):
            if "/git/commits/" in route: return {"sha": "a" * 40, "tree": {"sha": self.tree["sha"]}}
            return self.tree
    source_files = {**files, baseline: raw}
    importer.validate_boundary_base_membership(receipt, entries, Source(source_files))
    missing = dict(source_files); missing.pop(registry.INDEX_PATH)
    with pytest.raises(ValueError, match="IMMUTABLE_BASE"):
        importer.validate_boundary_base_membership(receipt, entries, Source(missing))
