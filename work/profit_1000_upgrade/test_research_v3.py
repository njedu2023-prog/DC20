"""Independent v3 import/rebuild orchestration tests; no network or fitting.

Fast tests use the pinned local three D-only inputs. The optional original-ZIP
test uses DC20_AUCTION_V3_V2_ZIP and performs a complete unchanged archive audit.
Mocked label tests exercise handoff guards, not financial/model correctness.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import zipfile

import pytest

from work.profit_1000_upgrade import research_v3 as research
from work.profit_1000_upgrade.policy_v3 import CONTRACT


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def forbidden(*args, **kwargs):
    raise AssertionError("this stage must not network, fit, or activate")


@pytest.fixture
def mirror(tmp_path):
    root = tmp_path / "isolated-research-v3"
    root.mkdir()
    plan = research.load_plan()
    for binding in plan["source_inputs"].values():
        source = research.safe_input(research.ROOT, binding)
        research._write(root / binding["path"], source.read_bytes())
    research.write_json(root / research.MARKER, {
        "schema_version": "dc20_profit_1000_research_mirror_v3", "plan_version": "v3",
        "plan_sha256": research.sha(research.plan_path()), "production_writes": False, **dict(CONTRACT)})
    return root


def test_registered_plan_preserves_prior_scope_and_never_authorizes_training():
    plan = research.load_plan()
    assert plan["historical_rows"] == 6753 and plan["historical_D_dates"] == 910
    assert plan["historical_D_start"] == "20221111" and plan["historical_D_end"] == "20260814"
    assert plan["as_of_date"] == "20260911" and plan["future_holdout_start_date"] == "20260914"
    assert plan["phase"] == "CANONICAL_AUCTION_ONLY"
    assert plan["training_performed"] is False and plan["production_release_allowed"] is False
    assert research.sha(research.HERE / "PLAN_V2.json") == research.V2_PLAN_SHA
    assert plan["base_archive"]["zip_sha256"] == research.BASE_SHA
    assert plan["base_archive"]["run_id"] == research.BASE_RUN
    assert plan["source_commit"] == "784ff93b638919929b176ae588f00986bbb66946"
    assert plan["revises_v3_plan_sha256"] == "b39d0fcf7ce3bca3af31a338cea32b27fcb72c7be124c91a3de6edfe8fb7f6e5"
    assert plan["http_envelope_adapter_id"] == "dc20_canonical_http_placeholder_detail_v2"
    assert {"src/top10decision/decision/executable_profit_shadow_settlement.py",
            "src/top10decision/decision/shadow_exit_1000.py",
            "work/profit_1000_upgrade/minute_truth.py"} <= {b["path"] for b in plan["adapter_sources"]}


@pytest.mark.parametrize("key,value", [
    ("historical_rows", 6752), ("historical_D_dates", 909), ("as_of_date", "20260914"),
    ("future_holdout_start_date", "20260915"), ("cost_rate", 0), ("stress_cost_rate", .0045),
    ("promotion_model_changed", True), ("training_performed", True), ("production_release_allowed", True),
    ("entry_policy_id", "research_auction_first_open_proxy_no_cap_v2"),
    ("phase", "TRAIN_AND_ACTIVATE"), ("collection_contract_sha256", "0" * 64),
    ("revises_v3_plan_sha256", "0" * 64), ("http_envelope_adapter_id", "ALLOW_ANY_DETAIL"),
])
def test_plan_contract_mutations_fail_closed(tmp_path, monkeypatch, key, value):
    plan = research.load_plan()
    plan[key] = value
    path = tmp_path / "PLAN_V3.json"
    path.write_bytes(encode(plan))
    monkeypatch.setattr(research, "plan_path", lambda: path)
    with pytest.raises(ValueError):
        research.load_plan()


def _preflight_fixture(failed_at=None, failure_status="PENDING_INVALID_SOURCE_NOT_IMPUTED"):
    dates = ["20250102", "20250116", "20260817"]
    attempted = dates if failed_at is None else dates[:failed_at + 1]
    qualified = dates if failed_at is None else dates[:failed_at]
    pre = {"trade_dates": dates, "attempted_T_dates": attempted, "qualified_T_dates": qualified,
           "status": "PASS" if failed_at is None else "BLOCKED",
           "aborted_at_T_date": None if failed_at is None else dates[failed_at]}
    journal = [{"trade_date": "20241231", "status": "HISTORY_BEFORE_CANONICAL_COVERAGE"}]
    for day in attempted:
        status = failure_status if day == pre["aborted_at_T_date"] else "EXACT_TRUTH_WRITTEN"
        journal.append({"trade_date": day, "status": status})
    remaining = [day for day in dates if day not in attempted] + ["20250103"]
    for day in remaining:
        journal.append({"trade_date": day, "status": "EXACT_TRUTH_WRITTEN" if failed_at is None else "PENDING_PREFLIGHT_ABORTED",
                        "network_request_performed": failed_at is None, "new_source_files": []})
    return {"preflight": pre}, journal, {"preflight_T_dates": dates}


@pytest.mark.parametrize("failed_at", [None, 0, 1, 2])
def test_preflight_acceptance_reconstructs_actual_sequential_barrier(failed_at):
    receipt, journal, contract = _preflight_fixture(failed_at)
    assert research._validate_preflight(receipt, journal, contract) == receipt["preflight"]


@pytest.mark.parametrize("failure_status", ["PENDING_CREDENTIAL_ABSENT", "PENDING_BUDGET_EXHAUSTED", "PENDING_HTTP_ERROR"])
def test_preflight_non_http_attempt_or_http_error_still_aborts(failure_status):
    receipt, journal, contract = _preflight_fixture(0, failure_status)
    assert research._validate_preflight(receipt, journal, contract)["attempted_T_dates"] == ["20250102"]


@pytest.mark.parametrize("mutation", ["wrong_first", "wrong_second", "fake_pass", "fake_failure_day",
    "fake_qualified", "drop_attempt", "late_call", "late_source", "late_request", "late_hash", "skip_first"])
def test_preflight_rejects_changed_sequence_or_network_after_failure(mutation):
    receipt, journal, contract = _preflight_fixture(1)
    if mutation == "wrong_first":
        journal[1]["trade_date"] = "20250103"
    elif mutation == "wrong_second":
        journal[2]["trade_date"] = "20260817"
    elif mutation == "fake_pass":
        receipt["preflight"]["status"] = "PASS"
    elif mutation == "fake_failure_day":
        receipt["preflight"]["aborted_at_T_date"] = "20260817"
    elif mutation == "fake_qualified":
        receipt["preflight"]["qualified_T_dates"].append("20250116")
    elif mutation == "drop_attempt":
        receipt["preflight"]["attempted_T_dates"].pop()
    elif mutation == "late_call":
        journal[-1]["network_request_performed"] = True
    elif mutation == "late_source":
        journal[-1]["new_source_files"] = [{"path": "fabricated"}]
    elif mutation == "late_request":
        journal[-1]["request"] = {"fabricated": True}
    elif mutation == "late_hash":
        journal[-1]["http_response_sha256"] = "0" * 64
    else:
        journal[1]["status"] = "PENDING_PREFLIGHT_ABORTED"
    with pytest.raises(ValueError):
        research._validate_preflight(receipt, journal, contract)


def test_passed_preflight_cannot_hide_aborted_unrequested_date():
    receipt, journal, contract = _preflight_fixture()
    journal[-1]["status"] = "PENDING_PREFLIGHT_ABORTED"
    with pytest.raises(ValueError):
        research._validate_preflight(receipt, journal, contract)


@pytest.mark.parametrize("field", research._RESPONSE_FIELDS)
def test_aborted_date_cannot_carry_any_provider_response_claim(field):
    receipt, journal, contract = _preflight_fixture(0)
    journal[-1][field] = None
    with pytest.raises(ValueError):
        research._validate_preflight(receipt, journal, contract)


def _response_metadata_fixture():
    meta = {"http_response_sha256": "a" * 64, "http_response_bytes": 200,
            "api_code": 0, "rows": 1, "fetched_at_utc": "2026-09-12T08:30:00+00:00"}
    item = {**meta, "source_rows": meta["rows"]}
    del item["rows"]
    return item, meta


def test_response_receipt_matches_exact_original_metadata():
    item, meta = _response_metadata_fixture()
    research._validate_source_response_receipt(item, meta)


@pytest.mark.parametrize("field,value", [
    ("http_response_sha256", "b" * 64), ("http_response_bytes", 201), ("http_response_bytes", "200"),
    ("api_code", False), ("source_rows", True), ("source_rows", 1.0),
    ("fetched_at_utc", "2026-09-12T08:30:01+00:00"), ("source_rows", None),
])
def test_response_receipt_rejects_type_coercion_or_inconsistent_value(field, value):
    item, meta = _response_metadata_fixture()
    item[field] = value
    with pytest.raises(ValueError):
        research._validate_source_response_receipt(item, meta)


def test_missing_or_changed_adapter_binding_rejected(tmp_path, monkeypatch):
    plan = research.load_plan()
    plan["adapter_sources"][0]["sha256"] = "0" * 64
    path = tmp_path / "PLAN_V3.json"
    path.write_bytes(encode(plan))
    monkeypatch.setattr(research, "plan_path", lambda: path)
    with pytest.raises(ValueError, match="SHA"):
        research.load_plan()


@pytest.mark.parametrize("relative", ["../escape", "/tmp/escape", "a/../../escape", "a\\b", "a//b", "./a", ""])
def test_unsafe_source_paths_fail_before_read(tmp_path, relative):
    with pytest.raises(ValueError):
        research.safe_input(tmp_path, {"path": relative, "sha256": "0" * 64})


def test_source_sha_and_symlink_cannot_be_accepted(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"original")
    binding = {"path": "source", "sha256": digest(b"original")}
    assert research.safe_input(tmp_path, binding) == source
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA"):
        research.safe_input(tmp_path, binding)
    (tmp_path / "alias").symlink_to(source)
    with pytest.raises(ValueError):
        research.safe_input(tmp_path, {"path": "alias", "sha256": digest(b"changed")})


def test_outputs_are_append_only_and_cannot_traverse_symlink(tmp_path):
    output = tmp_path / "new.json"
    research.write_json(output, {"fixed": True})
    with pytest.raises(ValueError, match="immutable"):
        research.write_json(output, {"fixed": False})
    assert json.loads(output.read_bytes()) == {"fixed": True}
    (tmp_path / "alias").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        research.write_json(tmp_path / "alias" / "other.json", {})
    assert not (tmp_path / "other.json").exists()


def test_research_root_must_not_be_checkout_or_ancestor():
    for path in (research.ROOT, research.ROOT.parent, research.HERE):
        with pytest.raises(ValueError):
            research.require_research_mirror(path)


@pytest.mark.parametrize("key,value", [
    ("plan_version", "v2"), ("production_writes", True), ("plan_sha256", "0" * 64),
    ("schema_version", "dc20_profit_1000_research_mirror_v2"),
    ("minute_time_semantics", "PROVIDER_CONFIRMED_BAR_END"),
])
def test_changed_mirror_marker_rejected(mirror, key, value):
    path = mirror / research.MARKER
    marker = json.loads(path.read_bytes())
    marker[key] = value
    path.write_bytes(encode(marker))
    with pytest.raises(ValueError):
        research.require_research_mirror(mirror)


def test_alias_root_rejected(mirror):
    alias = mirror.parent / "alias"
    alias.symlink_to(mirror, target_is_directory=True)
    with pytest.raises(ValueError):
        research.require_research_mirror(alias)


def test_d_only_reconstruction_preserves_all_candidates_without_legacy_outcomes(mirror):
    manifest = research.prepare_history(mirror)
    assert len(manifest["rows"]) == 6753 and len(manifest["expected_candidate_codes"]) == 910
    assert manifest["evidence_kind"] == "RETROSPECTIVE_D_ONLY_RECONSTRUCTION"
    assert manifest["feature_availability_is_natural_freeze_evidence"] is False
    expected_keys = {"signal_date", "ts_code", "stage_transition", "exec_date", "scheduled_exit_date",
                     "promotion_rank", "features", "feature_as_of_date", "feature_available_at"}
    identities = set()
    for row in manifest["rows"]:
        assert set(row) == expected_keys
        assert set(row["features"]) == set(manifest["feature_columns"])
        assert row["signal_date"] == row["feature_as_of_date"] < row["exec_date"] < row["scheduled_exit_date"]
        identity = (row["signal_date"], row["ts_code"])
        assert identity not in identities
        identities.add(identity)
    for key, value in CONTRACT.items():
        assert manifest[key] == value
    assert not any(p.name in {"research_results", "research_capital"} for p in mirror.iterdir())


def test_original_feature_source_mutation_rejected(mirror):
    binding = research.load_plan()["source_inputs"]["ledger"]
    path = mirror / binding["path"]
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="SHA"):
        research.prepare_history(mirror)


def test_initialization_rejects_existing_output_without_touching_it(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    keep = output / "keep"
    keep.write_bytes(b"user data")
    with pytest.raises(ValueError, match="fresh"):
        research.initialize(output, tmp_path / "absent.zip")
    assert keep.read_bytes() == b"user data"


def test_initialization_requires_original_zip_digest_before_creating_output(tmp_path):
    archive = tmp_path / "wrong.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("untrusted.json", "{}")
    output = tmp_path / "not-created"
    with pytest.raises(ValueError):
        research.initialize(output, archive)
    assert not output.exists()


@pytest.fixture
def import_binding_fixture(tmp_path, monkeypatch):
    """Tiny synthetic import lists; does not stand in for original ZIP audit."""
    root = tmp_path / "import-bindings"
    root.mkdir()
    (root / "research_inputs").mkdir()
    source = root / "original-source"
    source.write_bytes(b"original")
    source_files = [{"path": "original-source", "sha256": digest(b"original")}]
    minute_requests = [{"synthetic_test_only": True, "source": "original-source"}]
    receipt = {"source_files": source_files, "reused_minute_request_receipts": minute_requests}
    path = root / "research_inputs/base_archive_import_v3.json"
    path.write_bytes(encode(receipt))
    def list_digest(value):
        return digest(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
    monkeypatch.setattr(research, "IMPORTED_BINDINGS_SHA", list_digest(source_files))
    monkeypatch.setattr(research, "IMPORTED_MINUTE_REQUESTS_SHA", list_digest(minute_requests))
    monkeypatch.setattr(research, "require_research_mirror", lambda value: root)
    return root, path, receipt


def test_import_verification_checks_original_source_bytes(import_binding_fixture):
    root, _, original = import_binding_fixture
    assert research.verify_import(root) == original
    (root / "original-source").write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA"):
        research.verify_import(root)


@pytest.mark.parametrize("mutation", ["rebaseline_source", "drop_source", "drop_request", "change_request"])
def test_import_original_lists_cannot_be_rebaselined(import_binding_fixture, mutation):
    root, path, original = import_binding_fixture
    value = copy.deepcopy(original)
    if mutation == "rebaseline_source":
        (root / "original-source").write_bytes(b"changed")
        value["source_files"][0]["sha256"] = digest(b"changed")
    elif mutation == "drop_source":
        value["source_files"] = []
    elif mutation == "drop_request":
        value["reused_minute_request_receipts"] = []
    else:
        value["reused_minute_request_receipts"][0]["synthetic_test_only"] = False
    path.write_bytes(encode(value))
    with pytest.raises(ValueError, match="binding list changed"):
        research.verify_import(root)


def test_import_receipt_file_alias_rejected(import_binding_fixture):
    root, path, _ = import_binding_fixture
    original = path.with_name("original-receipt.json")
    path.rename(original)
    path.symlink_to(original)
    with pytest.raises(ValueError, match="unaliased"):
        research.verify_import(root)


@pytest.fixture
def replay_fixture(tmp_path, monkeypatch):
    """Mock only label computation and source acceptance for handoff testing."""
    from work.profit_1000_upgrade import labels_v3
    root = tmp_path / "replay"
    root.mkdir()
    (root / "research_inputs").mkdir()
    manifest = {"frozen": True}
    (root / "research_inputs/manifest.json").write_bytes(encode(manifest))
    (root / "collection_receipt.json").write_bytes(encode({"accepted": True}))
    (root / "evidence.txt").write_bytes(b"fixed")
    bindings = {"evidence.txt": {"path": "evidence.txt", "sha256": digest(b"fixed")}}
    source_summary = {"auction_evidence_complete": False}
    rows = [{"label_status": "PENDING_T_MISSING_CANONICAL_AUCTION", "entry_qualification_status": None,
             "net_return": None, "conditional_net_return": None, "slot_net_return": None,
             "missing_evidence_kind": "canonical_auction_0925", "missing_evidence_date": "20250116",
             "missing_evidence_code": "600001.SH"} for _ in range(6753)]
    labels = {"rows": rows, "cohorts_by_date": {str(i): {"complete": False} for i in range(910)},
              "source_files": list(bindings.values())}
    bindings["collection_receipt.json"] = {"path": "collection_receipt.json",
        "sha256": research.sha(root / "collection_receipt.json")}
    monkeypatch.setattr(research, "require_research_mirror", lambda value: root)
    monkeypatch.setattr(research, "prepare_history", lambda value: manifest)
    monkeypatch.setattr(research, "validate_collection", lambda *args: (bindings, source_summary))
    monkeypatch.setattr(labels_v3, "build_labels", lambda *args, **kwargs: labels)
    return root, labels, bindings, source_summary


def test_full_pending_cohort_is_retained_and_not_claimed_as_performance(replay_fixture, monkeypatch):
    from work.profit_1000_upgrade import candidate
    monkeypatch.setattr(candidate, "run_candidate", forbidden)
    monkeypatch.setattr(candidate, "_fit_ridge", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    root, _, _, _ = replay_fixture
    summary = research.evaluate(root)
    assert summary["status"] == "BLOCKED_CANONICAL_SOURCE"
    assert summary["candidate_rows"] == 6753 and summary["complete_D_dates"] == 0
    assert summary["label_status_counts"] == {"PENDING_T_MISSING_CANONICAL_AUCTION": 6753}
    saved = json.loads((root / "research_results/labels.json").read_bytes())
    assert len(saved["rows"]) == 6753 and all(row["slot_net_return"] is None for row in saved["rows"])
    persisted_summary = json.loads((root / "research_results/summary.json").read_bytes())
    assert {"work/profit_1000_upgrade/labels_v3.py", "work/profit_1000_upgrade/policy_v3.py",
            "work/profit_1000_upgrade/research_v3.py", "work/profit_1000_upgrade/collect_v3.py",
            "src/top10decision/decision/executable_profit_shadow_settlement.py"} <= {
                b["path"] for b in persisted_summary["code_bindings"]}
    for key in ("training_performed", "production_writes", "actual_execution_claimed", "actual_capacity_verified",
                "profitability_improvement_proven", "forward_holdout_evaluated", "production_activation_allowed"):
        assert summary[key] is False


@pytest.mark.parametrize("mutation", ["drop_row", "drop_day", "pending_zero", "unbound_source", "changed_source"])
def test_replay_rejects_lost_candidates_false_zeros_or_unbound_truth(replay_fixture, mutation):
    root, labels, _, _ = replay_fixture
    if mutation == "drop_row":
        labels["rows"].pop()
    elif mutation == "drop_day":
        labels["cohorts_by_date"].pop("0")
    elif mutation == "pending_zero":
        labels["rows"][0]["slot_net_return"] = 0
    elif mutation == "unbound_source":
        labels["source_files"].append({"path": "not-qualified", "sha256": "0" * 64})
    else:
        (root / "evidence.txt").write_bytes(b"changed")
    with pytest.raises(ValueError):
        research.evaluate(root)
    assert not (root / "research_results/labels.json").exists()


def test_replay_receipt_cannot_change_during_label_build(replay_fixture, monkeypatch):
    from work.profit_1000_upgrade import labels_v3
    root, labels, _, _ = replay_fixture
    def change(*args, **kwargs):
        (root / "collection_receipt.json").write_bytes(encode({"accepted": False}))
        return labels
    monkeypatch.setattr(labels_v3, "build_labels", change)
    with pytest.raises(ValueError):
        research.evaluate(root)
    assert not (root / "research_results/labels.json").exists()


def test_replay_cannot_use_implementation_changed_since_import(tmp_path, monkeypatch):
    original_sha = research.sha
    def changed(path):
        return "0" * 64 if Path(path) == Path(research.__file__) else original_sha(path)
    monkeypatch.setattr(research, "sha", changed)
    with pytest.raises(ValueError, match="implementation changed since import"):
        research.evaluate(tmp_path / "never-initialized")


def test_main_exposes_only_import_and_rebuild(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(research, "initialize", lambda *args: calls.append(("initialize", args)) or {"ok": True})
    monkeypatch.setattr(research, "evaluate", lambda *args: calls.append(("rebuild", args)) or {"ok": True})
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(sys, "argv", ["research_v3.py", "initialize", "--root", "/new", "--v2-archive", "/original.zip"])
    research.main()
    monkeypatch.setattr(sys, "argv", ["research_v3.py", "rebuild", "--root", "/new"])
    research.main()
    assert calls == [("initialize", ("/new", "/original.zip")), ("rebuild", ("/new",))]
    assert len(capsys.readouterr().out.splitlines()) == 2
    monkeypatch.setattr(sys, "argv", ["research_v3.py", "train", "--root", "/new"])
    with pytest.raises(SystemExit):
        research.main()


@pytest.mark.skipif(not os.environ.get("DC20_AUCTION_V3_V2_ZIP"), reason="optional pinned original v2 artifact")
def test_original_v2_zip_import_has_only_exact_whitelisted_sources(tmp_path, monkeypatch):
    archive = Path(os.environ["DC20_AUCTION_V3_V2_ZIP"])
    before = research.sha(archive)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    output = tmp_path / "original-import-v3"
    result = research.initialize(output, archive)
    assert before == research.BASE_SHA == research.sha(archive)
    assert result["candidate_rows"] == 6753 and result["D_dates"] == 910
    assert result["imported_files"] == 7307 and result["reused_minute_pairs"] == 2726
    receipt = json.loads((output / "research_inputs/base_archive_import_v3.json").read_bytes())
    assert receipt["current_network_requests"] == 0
    assert receipt["old_auction_sources_imported"] is False and receipt["old_outcome_labels_imported"] is False
    assert len(receipt["reused_minute_request_receipts"]) == 2726
    expected_paths = {binding["path"] for binding in receipt["source_files"]}
    expected_paths |= {research.MARKER, "research_inputs/base_archive_import_v3.json", "research_inputs/manifest.json"}
    actual = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
    assert actual == expected_paths
    assert not any(p.startswith(("research_results/", "research_capital/", "data/research/canonical_auction")) for p in actual)
    for binding in receipt["source_files"]:
        research.safe_input(output, binding)
    for item in receipt["reused_minute_request_receipts"]:
        assert item["provenance"] == "REUSED_FROM_PINNED_V2_NOT_CURRENT_NETWORK_REQUEST"
        assert item["status"] == "EXACT_TRUTH_WRITTEN"
    manifest = research.prepare_history(output)
    assert len(manifest["rows"]) == 6753 and len(manifest["expected_candidate_codes"]) == 910
