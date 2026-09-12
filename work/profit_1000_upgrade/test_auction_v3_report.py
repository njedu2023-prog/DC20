"""Read-only offline report contracts; synthetic ZIPs are not cloud evidence.

Most tests deliberately replace the two pinned ZIP digests and mock full v2
archive acceptance so that tiny fixtures can exercise orchestration guards.
Only the optional integration test uses both unmodified original ZIP pins.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import zipfile

import pytest
import yaml

from work.profit_1000_upgrade import auction_v3_report as report


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _forbidden(*args, **kwargs):
    raise AssertionError("qualification report must not write, fit, settle or network")


def _snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _csv(rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


@pytest.fixture
def cohort():
    manifest, saved, daily, docs = {"rows": []}, {"rows": []}, {}, {}
    for day, signal, count in zip(report.DATES, ("20250115", "20260814"), (10, 9)):
        daily[day] = {}
        docs[day + ".original_data.json"] = {"fields": list(report.auction_truth.FIELDS),
                                                "items": [], "count": 0, "has_more": False}
        for rank in range(1, count + 1):
            code = f"{600000 + rank:06}.SH"
            item = {"signal_date": signal, "exec_date": day, "ts_code": code, "promotion_rank": rank}
            manifest["rows"].append(item)
            saved["rows"].append({**item, "label_status": "PENDING_INVALID_CANONICAL_AUCTION_SOURCE"})
            daily[day][code] = {"ts_code": code, "trade_date": day, "open": "10.00"}
            docs[day + ".original_data.json"]["items"].append([code, day, 10, 100, 1000, 9.5])
    return manifest, saved, daily, docs


def _diagnostic_documents(docs):
    files = {key: _json(value) for key, value in docs.items()}
    files["request_contract.json"] = _json({"unit_test_fixture": True})
    receipt = {"run_id": report.DIAGNOSTIC_RUN, "run_commit": report.DIAGNOSTIC_COMMIT,
               "api_calls": 2, "retries": 0, "status": "DIAGNOSTIC_REQUESTS_COMPLETE",
               "diagnostic_only": True, "execution_file_bindings": dict(report.DIAGNOSTIC_CODE),
               "source_files": [{"path": name, "sha256": _sha(raw), "bytes": len(raw)}
                                for name, raw in files.items()],
               "requests": [{"trade_date": day, "request": report.auction_truth.request_contract(day),
                             "network_request_performed": True, "api_code": 0,
                             "original_table_retained": True,
                             "original_table_sha256": _sha(files[day + ".original_data.json"])}
                            for day in report.DATES]}
    receipt.update({key: False for key in ("source_import_allowed", "entry_source_eligible", "label_source_eligible",
                    "training_performed", "settlement_performed", "production_writes", "source_values_modified", "fallback_generated")})
    files["auction_diagnostic.json"] = _json(receipt)
    return files


def _pack(path, files, *, symlink=None):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        for name, raw in files.items():
            if name == symlink:
                item = zipfile.ZipInfo(name)
                item.create_system = 3
                item.external_attr = (stat.S_IFLNK | 0o777) << 16
                handle.writestr(item, raw)
            else:
                handle.writestr(name, raw)


@pytest.fixture
def archives(tmp_path, monkeypatch, cohort):
    """Synthetic byte-bound fixtures with explicitly mocked original audit."""
    manifest, saved, daily, docs = cohort
    files = {}
    saved["source_files"] = []
    for day, rows in daily.items():
        name = f"data/market/raw/{day[:4]}/{day}/daily.csv"
        files[name] = _csv(list(rows.values()))
        saved["source_files"].append({"path": name, "sha256": _sha(files[name])})
    files["research_inputs/manifest.json"] = _json(manifest)
    files["research_results/labels.json"] = _json(saved)
    diagnostic = _diagnostic_documents(docs)
    v2_path, diagnostic_path = tmp_path / "synthetic-v2.zip", tmp_path / "synthetic-diagnostic.zip"
    _pack(v2_path, files)
    _pack(diagnostic_path, diagnostic)
    monkeypatch.setattr(report, "V2_SHA", _sha(v2_path.read_bytes()))
    monkeypatch.setattr(report, "DIAGNOSTIC_SHA", _sha(diagnostic_path.read_bytes()))
    calls = []
    def audit(*args, **kwargs):
        calls.append((args, kwargs))
        return {"synthetic_unit_test_audit_mock": True, "integrity_verified": True}
    monkeypatch.setattr(report.acceptance, "audit_archive", audit)
    return {"v2": v2_path, "diagnostic": diagnostic_path, "v2_files": files,
            "diagnostic_files": diagnostic, "calls": calls, "cohort": cohort}


def _regenerate(fixture, monkeypatch, kind, files=None, **kwargs):
    target = fixture[kind]
    _pack(target, files if files is not None else fixture[kind + "_files"], **kwargs)
    monkeypatch.setattr(report, "V2_SHA" if kind == "v2" else "DIAGNOSTIC_SHA", _sha(target.read_bytes()))


def _generate(fixture):
    return report.generate_report(fixture["v2"], fixture["diagnostic"])


def test_pins_are_fixed_original_artifacts_and_old_code():
    assert report.V2_SHA == "58467518002c587349587eebb32681b1d81c3c8850ca5595b342818157ebfc64"
    assert report.DIAGNOSTIC_SHA == "2131372749a7063ac5976a5a46a46fbfe151d47b922de11ba51eb4861acb3de4"
    assert report.V2_RUN == "34676871475" and report.DIAGNOSTIC_RUN == "34679018528"
    assert report.DATES == ("20250116", "20260817")
    assert all(report._file_sha(report.CHECKOUT / path) == digest for path, digest in report.DIAGNOSTIC_CODE.items())
    assert {path.name for path in report._IMPORTED_HASHES} == {"auction_v3_report.py", "auction_truth_v3.py", "acceptance.py"}


def test_report_retains_all_frozen_candidates_and_original_labels(archives):
    before = _snapshot(archives["v2"].parent)
    result = _generate(archives)
    assert _snapshot(archives["v2"].parent) == before
    assert result["status"] == "OFFLINE_QUALIFICATION_COMPARISON_COMPLETE"
    assert result["source_archive_acceptance"]["synthetic_unit_test_audit_mock"]
    assert result["summary"]["candidate_rows"] == len(result["rows"]) == 19
    assert result["summary"]["T_dates"] == 2
    assert result["summary"]["old_single_row_accepted"] == 19
    assert result["summary"]["actual_fills_proven"] == 0
    assert result["summary"]["returns_computed"] is False
    assert result["summary"]["price_proxy_qualified"] == result["summary"]["capacity_arithmetic_consistent"] == 19
    manifest = archives["cohort"][0]
    assert [(r["signal_date"], r["ts_code"], r["promotion_rank"]) for r in result["rows"]] == [
        (r["signal_date"], r["ts_code"], r["promotion_rank"]) for r in manifest["rows"]]
    assert all(r["old_label_status_unchanged"] == "PENDING_INVALID_CANONICAL_AUCTION_SOURCE" for r in result["rows"])
    for row in result["rows"]:
        assert row["candidate_source_values_unchanged"] and row["price_is_original_reported_value"]
        assert all(row[key] == value for key, value in report.FLAGS.items())
    assert all(result[key] == value for key, value in report.FLAGS.items())
    assert result["price_evidence_basis"] == "POSTHOC_ENTRY_PRICE_CHECK_NOT_PRE0925_FEATURE"
    assert not any(key in result for key in ("returns", "net_return", "NAV", "model", "new_labels"))
    assert archives["calls"] == [((archives["v2"],), {"expected_zip_sha256": report.V2_SHA,
        "expected_run_id": report.V2_RUN, "expected_commit": report.V2_COMMIT})]
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("kind", ["v2", "diagnostic"])
def test_modified_original_zip_digest_cannot_be_overridden_by_caller(archives, kind):
    path = archives[kind]
    path.write_bytes(path.read_bytes() + b"modified_after_pinning")
    with pytest.raises(ValueError, match="V2_ZIP_SHA_MISMATCH|DIAGNOSTIC_ZIP_SHA_MISMATCH"):
        _generate(archives)


@pytest.mark.parametrize("kind", ["v2", "diagnostic"])
def test_input_archive_aliases_are_rejected(archives, kind):
    original = archives[kind]
    alias = original.with_name("alias.zip")
    alias.symlink_to(original)
    archives[kind] = alias
    with pytest.raises(ValueError, match="REGULAR_UNALIASED_FILE_REQUIRED"):
        _generate(archives)


def test_full_v2_acceptance_must_succeed_before_diagnostic_use(archives, monkeypatch):
    def rejected(*args, **kwargs):
        raise ValueError("SYNTHETIC_ORIGINAL_AUDIT_REJECTED")
    monkeypatch.setattr(report.acceptance, "audit_archive", rejected)
    monkeypatch.setattr(report, "_diagnostic", _forbidden)
    with pytest.raises(ValueError, match="SYNTHETIC_ORIGINAL_AUDIT_REJECTED"):
        _generate(archives)


@pytest.mark.parametrize("change", ["extra", "missing", "directory", "symlink", "oversized"])
def test_diagnostic_exact_member_set_and_safe_types(archives, monkeypatch, change):
    files = dict(archives["diagnostic_files"])
    key = report.DATES[0] + ".original_data.json"
    kwargs = {}
    if change == "extra":
        files["unexpected.json"] = b"{}"
    elif change == "missing":
        files.pop(key)
    elif change == "directory":
        files[key + "/"] = files.pop(key)
    elif change == "symlink":
        kwargs["symlink"] = key
    else:
        files[key] = b"x" * 4_000_001
    _regenerate(archives, monkeypatch, "diagnostic", files, **kwargs)
    with pytest.raises(ValueError, match="DIAGNOSTIC_MEMBER_SET_MISMATCH|UNSAFE_DIAGNOSTIC_MEMBER"):
        _generate(archives)


def test_duplicate_diagnostic_member_is_rejected(archives, monkeypatch):
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(archives["diagnostic"], "a") as handle:
            handle.writestr("request_contract.json", b"{}")
    monkeypatch.setattr(report, "DIAGNOSTIC_SHA", _sha(archives["diagnostic"].read_bytes()))
    with pytest.raises(ValueError, match="DIAGNOSTIC_MEMBER_SET_MISMATCH"):
        _generate(archives)


@pytest.mark.parametrize("field,value,reason", [
    ("run_id", "wrong", "DIAGNOSTIC_IDENTITY_MISMATCH"),
    ("run_commit", "0" * 40, "DIAGNOSTIC_IDENTITY_MISMATCH"),
    ("api_calls", 3, "DIAGNOSTIC_IDENTITY_MISMATCH"),
    ("retries", 1, "DIAGNOSTIC_IDENTITY_MISMATCH"),
    ("status", "FAILED", "DIAGNOSTIC_IDENTITY_MISMATCH"),
    ("source_import_allowed", True, "DIAGNOSTIC_NOT_READ_ONLY"),
    ("entry_source_eligible", True, "DIAGNOSTIC_NOT_READ_ONLY"),
    ("production_writes", True, "DIAGNOSTIC_NOT_READ_ONLY"),
    ("source_values_modified", True, "DIAGNOSTIC_NOT_READ_ONLY"),
    ("diagnostic_only", False, "DIAGNOSTIC_CODE_OR_KIND_MISMATCH"),
    ("execution_file_bindings", {}, "DIAGNOSTIC_CODE_OR_KIND_MISMATCH"),
    ("source_files", [], "DIAGNOSTIC_BINDING_SET_MISMATCH"),
])
def test_diagnostic_receipt_pins_cannot_drift(archives, monkeypatch, field, value, reason):
    files = dict(archives["diagnostic_files"])
    receipt = json.loads(files["auction_diagnostic.json"])
    receipt[field] = value
    files["auction_diagnostic.json"] = _json(receipt)
    _regenerate(archives, monkeypatch, "diagnostic", files)
    with pytest.raises(ValueError, match=reason):
        _generate(archives)


@pytest.mark.parametrize("change", ["table_bytes", "binding_sha", "binding_size", "dates", "endpoint", "table_receipt_sha", "network_false"])
def test_diagnostic_request_date_and_original_data_binding(archives, monkeypatch, change):
    files = dict(archives["diagnostic_files"])
    receipt = json.loads(files["auction_diagnostic.json"])
    if change == "table_bytes":
        key = report.DATES[0] + ".original_data.json"
        files[key] += b"\n"
    elif change == "binding_sha":
        receipt["source_files"][0]["sha256"] = "0" * 64
    elif change == "binding_size":
        receipt["source_files"][0]["bytes"] += 1
    elif change == "dates":
        receipt["requests"][0]["trade_date"] = "20250117"
    elif change == "endpoint":
        receipt["requests"][0]["request"]["api_name"] = "stk_auction_o"
    elif change == "table_receipt_sha":
        receipt["requests"][0]["original_table_sha256"] = "0" * 64
    else:
        receipt["requests"][0]["network_request_performed"] = False
    files["auction_diagnostic.json"] = _json(receipt)
    _regenerate(archives, monkeypatch, "diagnostic", files)
    with pytest.raises(ValueError, match="DIAGNOSTIC_SOURCE_BINDING_MISMATCH|DIAGNOSTIC_DATES_CHANGED|DIAGNOSTIC_REQUEST_OR_TABLE_CHANGED"):
        _generate(archives)


@pytest.mark.parametrize("body", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e9999}'])
def test_diagnostic_strict_json_rejects_ambiguity_and_nonfinite(archives, monkeypatch, body):
    files = dict(archives["diagnostic_files"], **{"request_contract.json": body})
    _regenerate(archives, monkeypatch, "diagnostic", files)
    with pytest.raises(ValueError):
        _generate(archives)


@pytest.mark.parametrize("raw", [
    b"ts_code,trade_date,open,open\n600001.SH,20250116,10,10\n",
    b"ts_code,trade_date\n600001.SH,20250116\n",
    b"ts_code,trade_date,open\n600001.SH,20250117,10\n",
    b"ts_code,trade_date,open\n600001.SH,20250116\n",
    b"ts_code,trade_date,open\n600001.SH,20250116,10,11\n",
    b"ts_code,trade_date,open\n600001.SH,20250116,10\n600001.SH,20250116,10\n",
    b"ts_code,trade_date,open\n", b"ts_code,trade_date,open\nBAD,20250116,10\n",
])
def test_daily_csv_shape_dates_and_duplicates_fail_closed(raw):
    with pytest.raises(ValueError):
        report._daily_rows(raw, report.DATES[0])


def test_daily_price_string_not_rounded_or_coerced():
    rows = report._daily_rows(b"ts_code,trade_date,open\n600001.SH,20250116,10.0000\n", report.DATES[0])
    assert rows["600001.SH"]["open"] == "10.0000"


@pytest.mark.parametrize("change", ["price", "binding_missing", "binding_digest"])
def test_daily_values_require_original_v2_label_source_hash(archives, monkeypatch, change):
    files = dict(archives["v2_files"])
    saved = json.loads(files["research_results/labels.json"])
    if change == "price":
        key = saved["source_files"][0]["path"]
        files[key] = files[key].replace(b"10.00", b"99.00")
    elif change == "binding_missing":
        saved["source_files"] = []
    else:
        saved["source_files"][0]["sha256"] = "0" * 64
    files["research_results/labels.json"] = _json(saved)
    _regenerate(archives, monkeypatch, "v2", files)
    with pytest.raises(ValueError, match="DAILY_NOT_BOUND_TO_V2_LABELS"):
        _generate(archives)


@pytest.mark.parametrize("change", ["count", "signal_date", "prior_exec_date", "prior_rank", "daily_missing"])
def test_comparison_preserves_pinned_cohort_and_label_identity(cohort, change):
    manifest, saved, daily, docs = cohort
    if change == "count":
        manifest["rows"].pop()
    elif change == "signal_date":
        manifest["rows"][0]["signal_date"] = "20250114"
    elif change == "prior_exec_date":
        saved["rows"][0]["exec_date"] = "20250117"
    elif change == "prior_rank":
        saved["rows"][0]["promotion_rank"] = 99
    else:
        daily[report.DATES[0]].pop("600001.SH")
    with pytest.raises(ValueError, match="PINNED_DIAGNOSTIC_COHORT_CHANGED|FROZEN_LABEL_CANDIDATE_MISMATCH|CANDIDATE_DAILY_ROW_MISSING"):
        report._compare(manifest, saved, daily, docs)


@pytest.mark.parametrize("values,status,capacity", [
    (["10.001", 100, "1000.100", 9.5], "CANONICAL_PRICE_OBSERVED", True),
    ([10, 100, 999, 9.5], "CANONICAL_PRICE_OBSERVED", False),
    ([None, 100, 1000, 9.5], "PENDING_CANONICAL_INVALID_PRICE", False),
    ([None, 0, 0, 9.5], "OBSERVED_NO_AUCTION_TRADE", False),
    ([10, None, 1000, 9.5], "PENDING_CANONICAL_INVALID_SHARE_VOLUME", False),
])
def test_candidate_local_unknown_does_not_drop_rows_or_become_zero(cohort, values, status, capacity):
    manifest, saved, daily, docs = cohort
    table = docs[report.DATES[0] + ".original_data.json"]
    table["items"][0][2:] = values
    before = copy.deepcopy(cohort)
    output = report._compare(manifest, saved, daily, docs)
    assert cohort == before and len(output) == 19
    row = output[0]
    qualification = row["qualification_v3"]
    assert qualification["status"] == status and qualification["capacity_proxy_verified"] is capacity
    assert qualification["raw_values"] == dict(zip(table["fields"], table["items"][0]))
    assert row["price_is_original_reported_value"] and row["candidate_source_values_unchanged"]
    assert qualification["price"] == (values[0] if status == "CANONICAL_PRICE_OBSERVED" else None)
    assert "net_return" not in qualification and "slot_net_return" not in qualification
    assert "proxy_fill" not in qualification
    assert all(r["qualification_v3"]["status"] == "CANONICAL_PRICE_OBSERVED" for r in output[1:])


def test_absent_candidate_remains_pending_without_fabricated_zero(cohort):
    manifest, saved, daily, docs = cohort
    docs[report.DATES[0] + ".original_data.json"]["items"].pop(0)
    output = report._compare(manifest, saved, daily, docs)
    assert len(output) == 19
    item = output[0]
    assert item["old_single_row_check"] == {"accepted": False, "reason": "ROW_ABSENT"}
    qualified = item["qualification_v3"]
    assert qualified["status"].startswith("PENDING_")
    assert qualified["price"] is None and qualified["capacity_proxy_verified"] is False
    assert qualified["raw_values"] is None
    assert "net_return" not in qualified and "proxy_fill" not in qualified


@pytest.mark.parametrize("change", ["price", "raw_values"])
def test_report_rejects_qualifier_that_modifies_provider_values(cohort, monkeypatch, change):
    original = report.auction_truth_v3.qualify_row
    def corrupted(*args):
        value = original(*args)
        if change == "price":
            value["price"] = 99
        else:
            value["raw_values"]["amount"] = 999
        return value
    monkeypatch.setattr(report.auction_truth_v3, "qualify_row", corrupted)
    with pytest.raises(ValueError, match="REPORT_CHANGED_ORIGINAL_PRICE"):
        report._compare(*cohort)


@pytest.mark.parametrize("when", ["before", "during"])
@pytest.mark.parametrize("target_kind", ["new_code", "old_code", "v2_zip", "diagnostic_zip"])
def test_all_input_and_implementation_hashes_checked_before_and_after(archives, monkeypatch, when, target_kind):
    target = {"new_code": report.HERE / "auction_truth_v3.py", "old_code": report.HERE / "auction_truth.py",
              "v2_zip": archives["v2"], "diagnostic_zip": archives["diagnostic"]}[target_kind]
    changed = [when == "before"]
    original = report._file_sha
    def read_hash(path):
        return "0" * 64 if changed[0] and Path(path) == target else original(path)
    monkeypatch.setattr(report, "_file_sha", read_hash)
    if when == "during":
        compare = report._compare
        def then_change(*args):
            result = compare(*args)
            changed[0] = True
            return result
        monkeypatch.setattr(report, "_compare", then_change)
    with pytest.raises(ValueError, match="CHANGED|MISMATCH"):
        _generate(archives)


def test_generation_never_imports_truth_relabels_settles_fits_or_networks(archives, monkeypatch):
    from work.profit_1000_upgrade import candidate, labels, run
    before = _snapshot(archives["v2"].parent)
    monkeypatch.setattr(Path, "write_text", _forbidden)
    monkeypatch.setattr(Path, "write_bytes", _forbidden)
    monkeypatch.setattr(run, "write_json", _forbidden)
    monkeypatch.setattr(labels, "build_labels", _forbidden)
    monkeypatch.setattr(candidate, "run_candidate", _forbidden)
    monkeypatch.setattr(candidate, "_fit_ridge", _forbidden)
    monkeypatch.setattr(report.auction_truth, "source_bytes", _forbidden)
    monkeypatch.setattr(report.auction_truth_v3, "source_bytes", _forbidden)
    monkeypatch.setattr(socket.socket, "connect", _forbidden)
    result = _generate(archives)
    assert _snapshot(archives["v2"].parent) == before
    assert result["network_requests"] == 0 and result["training_performed"] is False
    assert result["settlement_performed"] is False and result["source_import_allowed"] is False


def test_cli_has_no_pin_override_or_output_mutation_option_and_no_project_pycache(tmp_path):
    cache = tmp_path / "isolated-bytecode-prefix"
    env = dict(os.environ, PYTHONPYCACHEPREFIX=str(cache))
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, "-c", "import runpy,sys; sys.dont_write_bytecode=False; "
                             "sys.argv=[sys.argv[1],'--help']; runpy.run_path(sys.argv[0],run_name='__main__')",
                             str(Path(report.__file__).resolve())], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "--v2-archive" in result.stdout and "--diagnostic-archive" in result.stdout
    assert not any(option in result.stdout for option in ("--sha", "--expected", "--output", "--activate", "--token"))
    assert not any("/work/profit_1000_upgrade/" in p.as_posix() or "/src/top10decision/" in p.as_posix()
                   for p in cache.rglob("*.pyc"))


def _workflow():
    path = report.CHECKOUT / ".github/workflows/research_profit_auction_v3_check.yml"
    return yaml.load(path.read_text(), Loader=yaml.BaseLoader)


def test_workflow_is_exact_main_read_only_and_keeps_existing_runtime():
    workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read", "actions": "read"}
    assert set(workflow["on"]) == {"push"}
    assert workflow["on"]["push"]["branches"] == ["main"]
    assert set(workflow["on"]["push"]["paths"]) == {
        "work/profit_1000_upgrade/auction_truth_v3.py",
        "work/profit_1000_upgrade/test_auction_truth_v3.py",
        "work/profit_1000_upgrade/auction_v3_report.py",
        "work/profit_1000_upgrade/test_auction_v3_report.py",
        ".github/workflows/research_profit_auction_v3_check.yml"}
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    assert workflow["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    job = workflow["jobs"]["qualification"]
    assert "github.repository == 'njedu2023-prog/DC20'" in job["if"]
    assert "github.ref == 'refs/heads/main'" in job["if"]
    steps = job["steps"]
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"] == {"ref": "${{ github.sha }}", "persist-credentials": "false"}
    assert all(len(step["uses"].rsplit("@", 1)[1]) == 40 for step in steps if "uses" in step)
    commands = "\n".join(step.get("run", "") for step in steps)
    assert "--require-hashes -r requirements-dev.lock" in commands
    assert "python -m pytest -q work/profit_1000_upgrade -p no:cacheprovider" in commands
    assert "validate_decision_model_freeze.py --root ." in commands
    assert "git status --porcelain=v1" in commands


def test_workflow_reads_only_two_retained_artifacts_and_retains_only_report():
    steps = _workflow()["jobs"]["qualification"]["steps"]
    downloads = [step for step in steps if "curl " in step.get("run", "")]
    assert len(downloads) == 1
    text = downloads[0]["run"]
    assert text.count("curl ") == 2
    assert "actions/artifacts/10292829634/zip" in text
    assert "actions/artifacts/10293161278/zip" in text
    assert downloads[0]["env"] == {"GH_TOKEN": "${{ github.token }}"}
    commands = "\n".join(step.get("run", "") for step in steps)
    assert "auction_v3_report.py" in commands
    assert not any(term in commands for term in ("collect.py", "auction_diagnostic.py", "train_candidate", "workflow_dispatch", "git push"))
    assert not any("secrets." in str(step) for step in steps)
    retained = [step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@")]
    assert len(retained) == 1
    assert retained[0]["with"]["path"] == "${{ runner.temp }}/auction-v3-comparison.json"
    assert retained[0]["if"] == "success()"
    assert retained[0]["with"]["if-no-files-found"] == "error"


def test_real_original_archives_optional_integration():
    v2 = os.environ.get("DC20_AUCTION_V3_V2_ZIP")
    diagnostic = os.environ.get("DC20_AUCTION_V3_DIAGNOSTIC_ZIP")
    if not v2 and not diagnostic:
        pytest.skip("set both original ZIP paths to run actual source-bound archive integration")
    assert v2 and diagnostic, "provide both DC20_AUCTION_V3_V2_ZIP and DC20_AUCTION_V3_DIAGNOSTIC_ZIP"
    paths = [Path(v2), Path(diagnostic)]
    before = [report._file_sha(path) for path in paths]
    result = report.generate_report(*paths)
    assert [report._file_sha(path) for path in paths] == before
    assert before == [report.V2_SHA, report.DIAGNOSTIC_SHA]
    assert result["source_archive_acceptance"].get("synthetic_unit_test_audit_mock") is None
    assert len(result["rows"]) == result["summary"]["candidate_rows"] == 19
    assert all(row["candidate_source_values_unchanged"] and row["price_is_original_reported_value"] for row in result["rows"])
    assert result["network_requests"] == 0 and result["summary"]["returns_computed"] is False
    assert result["production_activation_allowed"] is False
