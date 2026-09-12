"""Offline scope tests; synthetic ZIPs explicitly replace pins only in tests.

The optional real-artifact test does not monkeypatch production pins. No fake
fixture is evidence of provider data or authorization for network collection.
"""
from __future__ import annotations

import copy
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import zipfile

import pytest

from work.profit_1000_upgrade import stocks_scope_inputs as scope


def _raw(value):
    return json.dumps(value, sort_keys=True, allow_nan=False).encode()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _binding(path, raw):
    return {"path": path, "sha256": _sha(raw)}


def _pin_fake(path, monkeypatch):
    monkeypatch.setattr(scope, "BASE_SHA256", _sha(path.read_bytes()))
    monkeypatch.setattr(scope, "BASE_BYTES", path.stat().st_size)
    return path


def _zip(path, files, monkeypatch):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as bundle:
        for name, raw in files:
            bundle.writestr(name, raw)
    return _pin_fake(path, monkeypatch)


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    """Five fictional cohorts, not a replacement for the real 6753-row cohort."""
    dates = [("20241230", "20241231"), ("20250101", "20250102"), ("20250318", "20250319"),
             ("20250825", "20250826"), ("20260202", "20260203")]
    manifest = {"rows": [{"signal_date": d, "exec_date": t, "ts_code": "600001.SH", "promotion_rank": 1,
                           "stage_transition": "2_to_3"} for d, t in dates],
                "auction_source_policy_id": "synthetic_policy", "expected_candidate_codes": {d: ["600001.SH"] for d, _ in dates}}
    files = {"old-source.bin": b"synthetic original source only"}
    manifest["source_bindings"] = [_binding("old-source.bin", files["old-source.bin"])]
    code_root = tmp_path / "fictional-code-root"
    code_root.mkdir()
    code = {}
    for name in ("old-runtime.py", scope.PLAN_PATH, scope.CONTRACT_PATH, scope.ADAPTER_PATH):
        target = code_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(("# synthetic fixture, never imported: " + name).encode())
        code[name] = _sha(target.read_bytes())
    manifest["plan_sha256"] = code[scope.PLAN_PATH]
    monkeypatch.setattr(scope, "CHECKOUT", code_root)
    monkeypatch.setattr(scope, "CODE_PATHS", set(code))
    monkeypatch.setattr(scope, "EXECUTION_PATHS", set(code))
    monkeypatch.setattr(scope, "COUNTS", {"candidate_rows": 5, "T_dates": 5, "reused_dates": 1,
                                         "precoverage_dates": 1, "gap_dates": 3, "gap_candidate_pairs": 3})
    common = {"plan_sha256": code[scope.PLAN_PATH], "collection_contract_sha256": code[scope.CONTRACT_PATH],
              "http_envelope_adapter_id": "synthetic_adapter", "http_envelope_adapter_sha256": code[scope.ADAPTER_PATH]}
    requests, sources = [], []
    for index, (_, day) in enumerate(dates):
        row = {**common, "trade_date": day, "candidate_codes": ["600001.SH"], "endpoint": "stk_auction",
               "ts_code": None, "source_policy_id": "synthetic_policy", "network_request_performed": index > 0,
               "new_source_files": []}
        if index == 0:
            row.update(status="HISTORY_BEFORE_CANONICAL_COVERAGE")
        else:
            row.update(request={"api_name": "stk_auction", "params": {"trade_date": day}, "fields": scope.FIELDS},
                       http_response_sha256="4" * 64, http_response_bytes=120)
            if index == 1:
                paths = [f"{scope.SOURCE_ROOT}/{day[:4]}/{day}/stk_auction.{kind}.json" for kind in ("data", "meta")]
                row.update(status="EXACT_TRUTH_WRITTEN", source_rows=0, source_status="CANONICAL_TABLE_EMPTY", api_code=0)
                meta = {"request": row["request"], "http_response_sha256": row["http_response_sha256"],
                        "http_response_bytes": 120, "rows": 0, "status": row["source_status"], "api_code": 0}
                for path, body in zip(paths, [b'{"fields":[],"items":[]}', _raw(meta)]):
                    files[path] = body
                    sources.append(_binding(path, body))
                row["new_source_files"] = copy.deepcopy(sources)
            else:
                row.update(status="PENDING_INVALID_SOURCE_NOT_IMPUTED", reason=scope.GAP_REASON)
        requests.append(row)
    files[scope.MANIFEST] = _raw(manifest)
    receipt = {**common, "request_receipts": requests, "api_calls": 4,
               "execution_file_bindings": code, "new_source_files": sources, "source_files": copy.deepcopy(sources),
               "imported_source_files": [_binding(p, raw) for p, raw in files.items() if not p.startswith(scope.SOURCE_ROOT)],
               "request_status_counts": {"HISTORY_BEFORE_CANONICAL_COVERAGE": 1, "EXACT_TRUTH_WRITTEN": 1,
                                         "PENDING_INVALID_SOURCE_NOT_IMPUTED": 3}}
    labels = {"candidate_manifest_sha256": scope._manifest_sha(manifest), "source_files": copy.deepcopy(sources),
              "rows": [{"signal_date": d, "ts_code": "600001.SH", "net_return": -1234} for d, _ in dates]}
    summary = {"code_bindings": [{"path": p, "sha256": s} for p, s in code.items()], "candidate_rows": 5,
               "D_dates": 5, "plan_sha256": code[scope.PLAN_PATH]}
    serial = [0]
    def build(change=None, member_change=None):
        m, r, l, s, f = copy.deepcopy((manifest, receipt, labels, summary, files))
        if change:
            change(m, r, l, s)
        f[scope.MANIFEST] = _raw(m)
        # Rebind only document bytes to let tests target deeper semantic guards.
        r["imported_source_files"] = [_binding(p, f[p]) for p in ["old-source.bin", scope.MANIFEST]]
        l["candidate_manifest_sha256"] = scope._manifest_sha(m)
        f[scope.JOURNAL] = b"\n".join(_raw(v) for v in r["request_receipts"]) + b"\n"
        r["journal_binding"] = _binding(scope.JOURNAL, f[scope.JOURNAL])
        f[scope.COLLECTION], f[scope.LABELS] = _raw(r), _raw(l)
        s["labels_binding"], s["collection_receipt_binding"] = _binding(scope.LABELS, f[scope.LABELS]), _binding(scope.COLLECTION, f[scope.COLLECTION])
        f[scope.SUMMARY] = _raw(s)
        if member_change:
            member_change(f)
        serial[0] += 1
        return _zip(tmp_path / f"synthetic-{serial[0]}.zip", list(f.items()), monkeypatch)
    return build, code_root, manifest


def test_synthetic_scope_uses_only_frozen_manifest_and_receipts(synthetic, monkeypatch):
    build, code_root, _ = synthetic
    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    archive = build()
    before = {p: p.read_bytes() for p in archive.parent.rglob("*") if p.is_file()}
    result = scope.read_base_archive(archive)
    assert result["gap_dates"] == ["20250319", "20250826", "20260203"]
    assert result["gap_candidate_codes"] == {d: ["600001.SH"] for d in result["gap_dates"]}
    assert result["reused_dates"] == ["20250102"] and result["precoverage_dates"] == ["20241231"]
    assert result["old_outcomes_used_for_scope"] is False and result["files_written"] == result["network_requests"] == 0
    assert result["training_performed"] is False and "labels" not in result and "summary" not in result
    assert json.loads(json.dumps(result)) == result
    assert {p: p.read_bytes() for p in archive.parent.rglob("*") if p.is_file()} == before


def test_old_return_values_do_not_select_or_filter_scope(synthetic):
    build, _, _ = synthetic
    first = scope.read_base_archive(build())
    def change(m, r, labels, s):
        for row in labels["rows"]:
            row["net_return"] = 9999
    second = scope.read_base_archive(build(change))
    assert first["gap_dates"] == second["gap_dates"] and first["gap_candidate_codes"] == second["gap_candidate_codes"]
    assert first["frozen_manifest"] == second["frozen_manifest"]


@pytest.mark.parametrize("kind", ["candidate", "endpoint", "request_code", "reason", "pre_http", "plan", "http_sha", "duplicate_date", "request_order", "calls"])
def test_changed_receipt_scope_or_provenance_fails(synthetic, kind):
    build, _, _ = synthetic
    def change(m, r, l, s):
        gap = r["request_receipts"][2]
        if kind == "candidate": gap["candidate_codes"] = ["600002.SH"]
        elif kind == "endpoint": gap["endpoint"] = "daily"
        elif kind == "request_code": gap["request"]["params"]["ts_code"] = "600001.SH"
        elif kind == "reason": gap["reason"] = "SOME_OTHER_FAILURE"
        elif kind == "pre_http": r["request_receipts"][0]["network_request_performed"] = True
        elif kind == "plan": gap["plan_sha256"] = "f" * 64
        elif kind == "http_sha": gap["http_response_sha256"] = "not a digest"
        elif kind == "duplicate_date": gap["trade_date"] = "20250102"
        elif kind == "request_order": r["request_receipts"].reverse()
        else: r["api_calls"] = 3
    with pytest.raises(ValueError):
        scope.read_base_archive(build(change))


@pytest.mark.parametrize("kind", ["duplicate", "future", "invalid_date", "reverse", "rank", "stage", "expected_codes", "missing", "labels_identity"])
def test_changed_frozen_identity_and_date_order_fail(synthetic, kind):
    build, _, _ = synthetic
    def change(m, r, l, s):
        row = m["rows"][2]
        if kind == "duplicate": m["rows"][2] = copy.deepcopy(m["rows"][1])
        elif kind == "future": row["exec_date"] = "20260914"
        elif kind == "invalid_date": row["exec_date"] = "20250230"
        elif kind == "reverse": row["exec_date"] = row["signal_date"]
        elif kind == "rank": row["promotion_rank"] = True
        elif kind == "stage": row["stage_transition"] = "4_to_5"
        elif kind == "expected_codes": m["expected_candidate_codes"][row["signal_date"]] = []
        elif kind == "missing": m["rows"].pop()
        else: l["rows"][0]["ts_code"] = "600009.SH"
    with pytest.raises(ValueError):
        scope.read_base_archive(build(change))


@pytest.mark.parametrize("kind", ["source_bytes", "source_hash", "pair_path", "orphan", "missing", "journal", "meta", "code_hash", "code_set"])
def test_bound_archive_sources_code_and_journal_tampering_fail(synthetic, kind):
    build, _, _ = synthetic
    def change(m, r, l, s):
        if kind == "source_hash": r["new_source_files"][0]["sha256"] = "0" * 64
        elif kind == "pair_path": r["request_receipts"][1]["new_source_files"][0]["path"] = "old-source.bin"
        elif kind == "code_hash": s["code_bindings"][0]["sha256"] = "0" * 64
        elif kind == "code_set": r["execution_file_bindings"]["unexpected.py"] = "0" * 64
    def members(f):
        if kind == "source_bytes": f["old-source.bin"] += b"changed"
        elif kind == "orphan": f["orphan.bin"] = b"unbound"
        elif kind == "missing": del f["old-source.bin"]
        elif kind == "journal": f[scope.JOURNAL] += f[scope.JOURNAL].splitlines()[0] + b"\n"
        elif kind == "meta":
            path = next(p for p in f if p.endswith(".meta.json"))
            f[path] += b" "
    with pytest.raises(ValueError):
        scope.read_base_archive(build(change, members))


def test_current_code_change_is_rejected(synthetic):
    build, root, _ = synthetic
    archive = build()
    (root / "old-runtime.py").write_bytes(b"changed")
    with pytest.raises(ValueError, match="SOURCE_BINDING_MISMATCH"):
        scope.read_base_archive(archive)


def test_code_is_rechecked_at_end_without_editing_real_code(synthetic, monkeypatch):
    build, root, _ = synthetic
    archive = build()
    original, calls = scope._file_sha, []
    target = root / "old-runtime.py"
    def changed(path):
        if Path(path) == target:
            calls.append(path)
            if len(calls) > 1:
                return "0" * 64
        return original(path)
    monkeypatch.setattr(scope, "_file_sha", changed)
    with pytest.raises(ValueError, match="OLD_CODE_CHANGED_DURING_READ"):
        scope.read_base_archive(archive)
    assert len(calls) == 2


def test_archive_is_rechecked_after_semantic_audit(synthetic, monkeypatch):
    build, _, _ = synthetic
    archive = build()
    original, calls = scope.verify_base_unchanged, []
    def changed(path):
        calls.append(path)
        if len(calls) == 3:
            raise ValueError("PINNED_BASE_ARCHIVE_CHANGED")
        return original(path)
    monkeypatch.setattr(scope, "verify_base_unchanged", changed)
    with pytest.raises(ValueError, match="PINNED_BASE_ARCHIVE_CHANGED"):
        scope.read_base_archive(archive)
    assert len(calls) == 3


def test_registered_preflight_must_remain_inside_receipt_derived_gaps(synthetic, monkeypatch):
    build, _, _ = synthetic
    archive = build()
    monkeypatch.setattr(scope, "PREFLIGHT_T_DATES", ("20250102", "20250826", "20260203"))
    with pytest.raises(ValueError, match="FIXED_GAP_SCOPE_CHANGED"):
        scope.read_base_archive(archive)


@pytest.mark.parametrize("name", ["../bad", "/bad", "x/../bad", "x//bad", "x/./bad", "x\\bad", "x:bad", "bad\x00tail", "e\u0301.txt"])
def test_unsafe_paths_fail(name):
    with pytest.raises(ValueError):
        scope._path(name)


@pytest.mark.parametrize("entries", [[("a", b"1"), ("a", b"2")], [("A", b"1"), ("a", b"2")],
                                    [("A", b"1"), ("a/child", b"2")]])
def test_duplicate_and_file_directory_casefold_aliases_fail(tmp_path, monkeypatch, entries):
    with pytest.warns(UserWarning) if entries[0][0] == entries[1][0] else nullcontext():
        path = _zip(tmp_path / "synthetic-alias.zip", entries, monkeypatch)
    with pytest.raises(ValueError, match="DUPLICATE_OR_ALIASED_MEMBER|FILE_DIRECTORY_ALIAS"):
        scope._audit_zip(path)


@pytest.mark.parametrize("kind", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFCHR, stat.S_IFDIR])
def test_nonregular_members_fail(tmp_path, monkeypatch, kind):
    info = zipfile.ZipInfo("not-regular")
    info.create_system, info.external_attr = 3, (kind | 0o644) << 16
    path = _zip(tmp_path / "synthetic-special.zip", [(info, b"x")], monkeypatch)
    with pytest.raises(ValueError, match="NONREGULAR_ZIP_MEMBER"):
        scope._audit_zip(path)


@pytest.mark.parametrize("cap,value", [("MAX_MEMBERS", 1), ("MAX_MEMBER_BYTES", 1), ("MAX_TOTAL_BYTES", 2)])
def test_zip_caps_fail_without_extracting(tmp_path, monkeypatch, cap, value):
    path = _zip(tmp_path / "synthetic-cap.zip", [("one", b"xx"), ("two", b"xx")], monkeypatch)
    monkeypatch.setattr(scope, cap, value)
    with pytest.raises(ValueError, match="ZIP_.*LIMIT"):
        scope._audit_zip(path)
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("kind", ["encrypted", "unsupported", "truncated_name"])
def test_zip_metadata_encryption_compression_and_hidden_name_fail(tmp_path, monkeypatch, kind):
    path = _zip(tmp_path / "synthetic-header.zip", [("source", b"x")], monkeypatch)
    original = zipfile.ZipFile
    class ChangedHeaders(original):
        def infolist(self):
            infos = super().infolist()
            if kind == "encrypted": infos[0].flag_bits |= 1
            elif kind == "unsupported": infos[0].compress_type = zipfile.ZIP_BZIP2
            else: infos[0].orig_filename += "\x00hidden"
            return infos
    monkeypatch.setattr(scope.zipfile, "ZipFile", ChangedHeaders)
    with pytest.raises(ValueError, match="ENCRYPTED_OR_UNSUPPORTED_MEMBER|TRUNCATED_ARCHIVE_NAME"):
        scope._audit_zip(path)


def test_crc_fails_even_when_synthetic_outer_pin_recomputed(tmp_path, monkeypatch):
    path = _zip(tmp_path / "synthetic-crc.zip", [("source", b"unique_crc_marker")], monkeypatch)
    raw = path.read_bytes().replace(b"unique_crc_marker", b"broken_crc_marker", 1)
    path.write_bytes(raw)
    _pin_fake(path, monkeypatch)
    with pytest.raises(ValueError, match="ZIP_CRC_OR_CONTAINER_INVALID"):
        scope._audit_zip(path)


def test_archive_hash_size_and_symlink_guards(synthetic, monkeypatch):
    build, _, _ = synthetic
    path = build()
    monkeypatch.setattr(scope, "BASE_BYTES", path.stat().st_size + 1)
    with pytest.raises(ValueError, match="PINNED_BASE_ARCHIVE_CHANGED"):
        scope.verify_base_unchanged(path)
    _pin_fake(path, monkeypatch)
    link = path.parent / "alias.zip"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="REGULAR_UNALIASED_FILE_REQUIRED"):
        scope.verify_base_unchanged(link)
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="PINNED_BASE_ARCHIVE_CHANGED"):
        scope.verify_base_unchanged(path)


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}', b'\xff', b'not json'])
def test_strict_json_rejects_duplicates_nonfinite_and_invalid_bytes(raw):
    with pytest.raises(ValueError):
        scope._json(raw)


def test_real_accepted_artifact_only_when_explicitly_supplied():
    path = os.environ.get("DC20_STOCKS_SCOPE_BASE_ZIP")
    if not path:
        pytest.skip("Set DC20_STOCKS_SCOPE_BASE_ZIP for the real pinned artifact; synthetic tests are not provider evidence")
    result = scope.read_base_archive(Path(path))
    assert result["base_archive"]["sha256"] == "f35140230a777b58276e58e861ec63076f375a27b0f11d32bd170174658d44ad"
    assert len(result["gap_dates"]) == 212 and len(result["reused_dates"]) == 181 and len(result["precoverage_dates"]) == 517
    assert sum(map(len, result["gap_candidate_codes"].values())) == 1860
    assert len(result["frozen_manifest"]["rows"]) == 6753 and len(result["archive_file_bindings"]) == 7676
    assert result["archive_audit"]["CRC_verified"] and result["training_performed"] is False
    assert set(scope.PREFLIGHT_T_DATES) <= set(result["gap_dates"])
