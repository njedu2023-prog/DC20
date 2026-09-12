"""Small fabricated ZIPs test audit rejection; they are not financial evidence."""
from __future__ import annotations

import copy
import csv
import gzip
import io
import json
import stat
import zipfile

import pytest

from work.profit_1000_upgrade import acceptance as audit, policy_v2
from work.profit_1000_upgrade import capital as capital_kernel

RUN = "34676871475"
COMMIT = "a" * 40


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode()


@pytest.fixture
def case(tmp_path, monkeypatch):
    plan = json.loads((audit.HERE / "PLAN_V2.json").read_bytes())
    raw = {}
    original = []
    for d, t, t1 in (("20240102", "20240103", "20240104"), ("20260102", "20260105", "20260106")):
        for rank in (1, 2):
            original.append({"signal_date": d, "ts_code": f"60000{rank}.SH", "promotion_rank": rank,
                             "exec_date": t, "scheduled_exit_date": t1, "stage_transition": "2_to_3"})
    csv_text = io.StringIO()
    writer = csv.DictWriter(csv_text, fieldnames=list(original[0]))
    writer.writeheader()
    writer.writerows(original)
    for name, spec in plan["source_inputs"].items():
        raw[spec["path"]] = gzip.compress(csv_text.getvalue().encode()) if name == "ledger" else b"{}"
        spec["sha256"] = audit._sha(raw[spec["path"]])
    plan.update(historical_rows=4, historical_D_dates=2, historical_D_start="20240102", historical_D_end="20260102")
    plan["base_archive"].update(daily_partitions=1, limit_partitions=1)
    local = tmp_path / "registered"
    local.mkdir()
    (local / "PLAN_V2.json").write_bytes(encoded(plan))
    (local / "COLLECTION_V2.json").write_bytes(b'{"unit":"fixture-only"}')
    monkeypatch.setattr(audit, "HERE", local)
    plan_sha = audit._sha((local / "PLAN_V2.json").read_bytes())
    def provenance(paths):
        return {"run_id": RUN, "run_commit": COMMIT, "source_files": [
            {"path": p, "sha256": audit._sha((audit.CHECKOUT / p).read_bytes())} for p in sorted(paths)]}
    marker = dict(policy_v2.CONTRACT, schema_version="dc20_profit_1000_research_mirror_v2",
                  plan_version="v2", production_writes=False, plan_sha256=plan_sha)
    manifest = dict(policy_v2.CONTRACT, rows=original, source_bindings=list(plan["source_inputs"].values()),
                    expected_candidate_codes={d: [r["ts_code"] for r in original if r["signal_date"] == d]
                                              for d in ("20240102", "20260102")}, plan_sha256=plan_sha)
    base_sources = []
    for name in ("daily", "stk_limit"):
        path = f"data/market/raw/2026/20260105/{name}.csv"
        raw[path] = b"unit,fixture\n1,2\n"
        base_sources.append({"path": path, "sha256": audit._sha(raw[path])})
    base = dict(plan_sha256=plan_sha, base_archive=plan["base_archive"], source_files=base_sources,
                old_auction_and_minute_sources_imported=False, old_outcome_labels_imported=False, production_writes=False)
    rows = [dict(r, **policy_v2.CONTRACT, label_status="PENDING_EXIT_MISSING_MINUTES", minute_source_observed=False,
                 entry_price_source="DAILY_OPEN_PROXY", proxy_fill=1, net_return=None, conditional_net_return=None,
                 slot_net_return=None, cohort_complete=False, label_available_date=None, actual_exit_date=None,
                 shadow_max_price=None, round_trip_cost_rate=0.0045, missing_evidence_kind="research_exit_1000_1m_0931",
                 missing_evidence_date=r["scheduled_exit_date"]) for r in original]
    labels = dict(policy_v2.CONTRACT, rows=rows, source_files=list(plan["source_inputs"].values()) + base_sources,
                  candidate_manifest_sha256=audit._canonical_sha(manifest), as_of_date=plan["as_of_date"],
                  cohorts_by_date=audit._cohorts(rows))
    counts = {"PENDING_EXIT_MISSING_MINUTES": 4}
    collection = dict(policy_v2.CONTRACT, schema_version="dc20_profit_1000_collection_receipt_v2", plan_sha256=plan_sha,
                      as_of_date=plan["as_of_date"], collection_request_sha256=audit._sha((local / "COLLECTION_V2.json").read_bytes()),
                      candidate_rows=4, candidate_source_bindings=manifest["source_bindings"], production_writes=False,
                      existing_truth_overwritten=False, credential_persisted=False, cohorts=labels["cohorts_by_date"],
                      status="PENDING_RESEARCH_TRUTH", auction_evidence_complete=True, api_calls=4,
                      label_status_counts=counts, new_source_files=[], existing_source_files=[], request_receipts=[],
                      base_archive_import_binding={"path": audit.REQUIRED["base"], "sha256": audit._sha(encoded(base))})
    coverage, failures, _ = audit._quality(rows, plan)
    report = dict(policy_v2.CONTRACT, training_performed=False, production_activation_allowed=False,
                  coverage=coverage, quality_gates=plan["data_gates"], failed_quality_gates=failures)
    candidate = dict(policy_v2.CONTRACT, status="BLOCKED_DATA_QUALITY", plan_sha256=plan_sha,
                     report=report, predictions=[], candidate_model=None, label_status_counts=counts,
                     execution_provenance=provenance(audit.COMMON_CODE))
    replay = dict(status="BLOCKED_DATA_QUALITY", plan_sha256=plan_sha, report=copy.deepcopy(report),
                  predictions=[], candidate_model=None, capital_report_status="BLOCKED_CANDIDATE_DATA",
                  execution_provenance=provenance(audit.CAPITAL_CODE))
    capital = dict(policy_v2.CONTRACT, plan_sha256=plan_sha, as_of_date=plan["as_of_date"], status="BLOCKED_CANDIDATE_DATA",
                   capital_comparisons=None, data_source_files=[], production_activation_allowed=False,
                   actual_execution_claimed=False, profitability_improvement_proven=False,
                   label_status_counts=counts, candidate_replay_sha256=audit._canonical_sha(replay),
                   execution_provenance=provenance(audit.CAPITAL_CODE))
    docs = dict(marker=marker, manifest=manifest, base=base, collection=collection, labels=labels,
                candidate=candidate, replay=replay, capital=capital)
    return {"root": tmp_path, "raw": raw, "docs": docs}


def bundle(case, *, extra=(), omit=()):
    path = case["root"] / "artifact.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in case["raw"].items():
            archive.writestr(name, raw)
        for key, value in case["docs"].items():
            if key not in omit:
                archive.writestr(audit.REQUIRED[key], encoded(value))
        for name, raw in extra:
            archive.writestr(name, raw)
    return path


def check(case, **kwargs):
    path = bundle(case, **kwargs)
    return audit.audit_archive(path, expected_zip_sha256=audit._sha(path.read_bytes()), expected_run_id=RUN, expected_commit=COMMIT)


def test_complete_blocked_archive_is_integrity_evidence_not_activation(case):
    result = check(case)
    assert result["artifact_integrity_verified"]
    assert result["candidate"]["status"] == "BLOCKED_DATA_QUALITY"
    assert not result["candidate"]["training_performed"]
    assert "INCOMPLETE_VALIDATION_COHORTS_CANNOT_BE_DROPPED" in result["candidate"]["recomputed_failed_quality_gates"]
    assert result["label_status_counts"] == {"PENDING_EXIT_MISSING_MINUTES": 4}
    assert sum(v["rows"] for v in result["pending_evidence"]) == 4
    assert all(result[k] is False for k in ("production_activation_allowed", "actual_execution_claimed",
                                           "profitability_improvement_proven", "independent_price_replay_performed"))
    assert sorted(p.name for p in case["root"].iterdir()) == ["artifact.zip", "registered"]


@pytest.mark.parametrize("field,value,error", [("expected_zip_sha256", "0" * 64, "ZIP_SHA_MISMATCH"),
                                                ("expected_run_id", "1", "RUN_ID_MISMATCH"),
                                                ("expected_commit", "0" * 40, "RUN_COMMIT_MISMATCH")])
def test_wrong_expected_identity_rejected(case, field, value, error):
    path = bundle(case)
    args = dict(expected_zip_sha256=audit._sha(path.read_bytes()), expected_run_id=RUN, expected_commit=COMMIT)
    args[field] = value
    with pytest.raises(audit.AcceptanceError, match=error):
        audit.audit_archive(path, **args)


@pytest.mark.parametrize("missing", list(audit.REQUIRED))
def test_missing_required_output_is_incomplete(case, missing):
    with pytest.raises(audit.AcceptanceError, match="INCOMPLETE_ARTIFACT"):
        check(case, omit=(missing,))


@pytest.mark.parametrize("path", ["../escape", "/absolute", "x/./item", "x//item", "x/../item", "x\\item", "x:item"])
def test_unsafe_zip_member_rejected(case, path):
    with pytest.raises(audit.AcceptanceError, match="UNSAFE_MEMBER_PATH"):
        check(case, extra=[(path, b"unsafe")])


def test_duplicate_zip_member_rejected(case):
    with pytest.warns(UserWarning), pytest.raises(audit.AcceptanceError, match="DUPLICATE_MEMBER_PATH"):
        check(case, extra=[(audit.REQUIRED["labels"], b"{}")])


def test_file_directory_alias_rejected(case):
    with pytest.raises(audit.AcceptanceError, match="ZIP_FILE_DIRECTORY_ALIAS"):
        check(case, extra=[("collision", b"x"), ("collision/child", b"x")])


def test_symlink_member_rejected(case):
    info = zipfile.ZipInfo("link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(audit.AcceptanceError, match="NONREGULAR_ZIP_MEMBER"):
        check(case, extra=[(info, b"../../target")])


@pytest.mark.parametrize("raw,error", [(b'{"x":NaN}', "NONFINITE_JSON"), (b'{"x":Infinity}', "NONFINITE_JSON"),
                                        (b'{"x":1e999}', "NONFINITE_JSON"), (b'{"x":1,"x":2}', "DUPLICATE_JSON_KEY")])
def test_all_json_sources_are_strict(case, raw, error):
    with pytest.raises(audit.AcceptanceError, match=error):
        check(case, extra=[("source.json", raw)])


def test_archive_source_sha_changed(case):
    path = case["docs"]["labels"]["source_files"][-1]["path"]
    case["raw"][path] = b"tampered"
    with pytest.raises(audit.AcceptanceError, match="ARCHIVE_SOURCE_SHA_MISMATCH"):
        check(case)


def test_code_provenance_sha_changed(case):
    case["docs"]["candidate"]["execution_provenance"]["source_files"][0]["sha256"] = "0" * 64
    with pytest.raises(audit.AcceptanceError, match="CODE_SOURCE_SHA_MISMATCH"):
        check(case)


@pytest.mark.parametrize("target", ["marker", "manifest", "collection", "candidate", "capital", "replay", "base"])
def test_wrong_plan_sha(case, target):
    case["docs"][target]["plan_sha256"] = "0" * 64
    with pytest.raises(audit.AcceptanceError, match="PLAN_SHA_MISMATCH"):
        check(case)


@pytest.mark.parametrize("field", list(policy_v2.CONTRACT))
def test_missing_policy_field(case, field):
    case["docs"]["labels"].pop(field)
    with pytest.raises(ValueError, match="policy mismatch"):
        check(case)


def test_legacy_policy_not_accepted(case):
    case["docs"]["labels"]["rows"][0]["entry_policy_id"] = "research_auction_or_open_no_cap_v1"
    with pytest.raises(ValueError, match="policy mismatch"):
        check(case)


def test_missing_pending_value_not_silently_treated_as_null(case):
    case["docs"]["labels"]["rows"][0].pop("slot_net_return")
    with pytest.raises(audit.AcceptanceError, match="PENDING_LABEL_FALSE_ZERO"):
        check(case)


def test_pending_cannot_become_zero_return(case):
    case["docs"]["labels"]["rows"][0]["slot_net_return"] = 0.0
    with pytest.raises(audit.AcceptanceError, match="PENDING_LABEL_FALSE_ZERO"):
        check(case)


def test_return_counts_recomputed(case):
    case["docs"]["candidate"]["label_status_counts"] = {"SETTLED_1000_LIMIT_HOLD_MINUTE_PROXY": 4}
    with pytest.raises(audit.AcceptanceError, match="LABEL_STATUS_COUNTS_MISMATCH"):
        check(case)


def test_replay_json_hash_recomputed(case):
    case["docs"]["capital"]["candidate_replay_sha256"] = "0" * 64
    with pytest.raises(audit.AcceptanceError, match="CAPITAL_CANDIDATE_REPLAY_SHA_MISMATCH"):
        check(case)


def test_quality_gate_count_recomputed(case):
    case["docs"]["candidate"]["report"]["coverage"]["training_complete_dates"] = 999
    with pytest.raises(audit.AcceptanceError, match="CANDIDATE_QUALITY_GATE_MISMATCH"):
        check(case)


def test_model_does_not_hide_data_gate_failure(case):
    case["docs"]["candidate"]["report"]["training_performed"] = True
    with pytest.raises(audit.AcceptanceError, match="FITTED_CANDIDATE_GATE_VIOLATION"):
        check(case)


def test_proof_flags_cannot_be_true(case):
    case["docs"]["capital"]["profitability_improvement_proven"] = True
    with pytest.raises(audit.AcceptanceError, match="UNAUTHORIZED_PROOF_CLAIM"):
        check(case)


@pytest.mark.parametrize("name,value,error", [("MAX_MEMBERS", 1, "ZIP_MEMBER_COUNT_BUDGET"),
                                             ("MAX_MEMBER_BYTES", 5, "ZIP_MEMBER_SIZE_BUDGET"),
                                             ("MAX_TOTAL_BYTES", 1, "ZIP_COMPRESSED_SIZE_BUDGET")])
def test_zip_resource_budgets(case, monkeypatch, name, value, error):
    monkeypatch.setattr(audit, name, value)
    with pytest.raises(audit.AcceptanceError, match=error):
        check(case)


@pytest.fixture
def complete_case(case):
    """Real capital kernel results over tiny fabricated, fully settled labels.

    This is contract-shape testing only. The wrapper's source-replay declaration
    is simulated; no actual source-price replay or model fit is claimed here.
    """
    docs = case["docs"]
    plan_path = audit.HERE / "PLAN_V2.json"
    plan = json.loads(plan_path.read_bytes())
    plan["data_gates"] = {key: 1.0 if key.endswith("fraction") else 1 for key in plan["data_gates"]}
    plan_path.write_bytes(encoded(plan))
    plan_sha = audit._sha(plan_path.read_bytes())
    for key in ("marker", "manifest", "collection", "candidate", "capital", "replay", "base"):
        docs[key]["plan_sha256"] = plan_sha
    docs["collection"]["base_archive_import_binding"]["sha256"] = audit._sha(encoded(docs["base"]))
    rows = docs["labels"]["rows"]
    for row in rows:
        day = row["scheduled_exit_date"]
        stamp = f"{day[:4]}-{day[4:6]}-{day[6:]}T10:00:00+08:00"
        net = -0.0245 if row["promotion_rank"] == 1 else 0.01
        row.update(label_status=audit.SETTLED, entry_price=10, minute_source_observed=True,
                   net_return=net, conditional_net_return=net, slot_net_return=net, cohort_complete=True,
                   label_available_date=day, actual_exit_date=day, actual_exit_time=stamp,
                   label_policy_id=capital_kernel.EXIT_POLICY_ID,
                   exit_evidence={"exit_policy_id": capital_kernel.EXIT_POLICY_ID, "gross_return": net + 0.0045,
                                  "actual_exit_date": day, "actual_exit_time": stamp})
    docs["labels"]["cohorts_by_date"] = audit._cohorts(rows)
    docs["labels"]["candidate_manifest_sha256"] = audit._canonical_sha(docs["manifest"])
    docs["collection"].update(cohorts=docs["labels"]["cohorts_by_date"], status="RESEARCH_LABEL_COHORTS_COMPLETE")
    coverage, failures, valid = audit._quality(rows, plan)
    assert failures == []
    predictions = [dict(policy_v2.CONTRACT, signal_date=r["signal_date"], ts_code=r["ts_code"],
                        promotion_rank=r["promotion_rank"], candidate_rank=r["promotion_rank"],
                        candidate_score=-0.05 * r["promotion_rank"], selected_shadow_slot=r["promotion_rank"],
                        slot_net_return=r["slot_net_return"], proxy_fill=1)
                   for r in rows if audit._identity(r) in valid]
    for key in ("candidate", "replay"):
        docs[key].update(status="DEVELOPMENT_ONLY_FITTED", predictions=copy.deepcopy(predictions),
                         candidate_model=dict(policy_v2.CONTRACT, model_fixture="not-an-actual-fit"))
        docs[key]["report"].update(coverage=coverage, quality_gates=plan["data_gates"], failed_quality_gates=[], training_performed=True)
    counts = {audit.SETTLED: len(rows)}
    for key in ("candidate", "collection", "capital"):
        docs[key]["label_status_counts"] = counts
    dates = sorted({d for r in rows for d in (r["signal_date"], r["exec_date"], r["scheduled_exit_date"])} | {plan["as_of_date"]})
    daily_source = docs["base"]["source_files"][0]
    def daily(day, code):
        return {"trade_date": day, "ts_code": code, "close": 10, "pre_close": 10, "source_files": [daily_source]}
    comparisons = {}
    for name, rank_field in (("candidate", "candidate_rank"), ("frozen_promotion", "promotion_rank")):
        value = capital_kernel.replay_capital(predictions, rows, open_dates=dates, as_of_date=plan["as_of_date"],
                                             load_daily=daily, rank_field=rank_field, entry_policy_id=policy_v2.ENTRY_POLICY_ID)
        value["label_verification"] = "REBUILT_FROM_BOUND_REPOSITORY_PRICE_SOURCES"
        comparisons[name] = value
    docs["capital"].update(status="CAPITAL_REPLAY_COMPLETE", capital_comparisons=comparisons)
    docs["replay"]["capital_report_status"] = "CAPITAL_REPLAY_COMPLETE"
    docs["capital"]["candidate_replay_sha256"] = audit._canonical_sha(docs["replay"])
    return case


def test_actual_capital_kernel_shape_accepts_per_record_entry_id_and_losses(complete_case):
    result = check(complete_case)
    assert result["artifact_integrity_verified"] and result["capital"]["status"] == "CAPITAL_REPLAY_COMPLETE"
    assert result["candidate"]["training_performed"]
    account = complete_case["docs"]["capital"]["capital_comparisons"]["candidate"]["accounts"]["top1"]
    assert account["equity"] == 997550
    assert account["records"][0]["net_profit"] == -2450
    assert set(account["records"][0]) & set(policy_v2.CONTRACT) == {"entry_policy_id"}
    assert not result["profitability_improvement_proven"]


@pytest.mark.parametrize("change,error", [("empty", "COMPLETE_CAPITAL_REPORT_MISSING_REPLAYS"),
                                         ("one_comparison", "COMPLETE_CAPITAL_REPORT_MISSING_REPLAYS"),
                                         ("one_account", "CAPITAL_TWO_ACCOUNTS_REQUIRED"),
                                         ("no_records", "CAPITAL_ACCOUNT_RECORDS_REQUIRED")])
def test_falsely_complete_capital_fails_closed(complete_case, change, error):
    value = complete_case["docs"]["capital"]
    if change == "empty": value["capital_comparisons"] = None
    elif change == "one_comparison": value["capital_comparisons"].pop("candidate")
    elif change == "one_account": value["capital_comparisons"]["candidate"]["accounts"].pop("top2")
    else: value["capital_comparisons"]["candidate"]["accounts"]["top1"]["records"] = []
    with pytest.raises(audit.AcceptanceError, match=error):
        check(complete_case)


def test_trained_candidate_requires_coverage(complete_case):
    complete_case["docs"]["candidate"]["report"].pop("coverage")
    with pytest.raises(audit.AcceptanceError, match="FITTED_CANDIDATE_GATE_VIOLATION"):
        check(complete_case)


def test_legacy_entry_id_in_actual_capital_record_rejected(complete_case):
    record = complete_case["docs"]["capital"]["capital_comparisons"]["candidate"]["accounts"]["top1"]["records"][0]
    record["entry_policy_id"] = "research_auction_or_open_no_cap_v1"
    with pytest.raises(audit.AcceptanceError, match="ACCOUNT_RECORD_ENTRY_POLICY_MISMATCH"):
        check(complete_case)


def test_partial_policy_elsewhere_not_covered_by_capital_record_exception(complete_case):
    complete_case["docs"]["capital"]["capital_comparisons"]["candidate"]["accounts"]["top1"]["entry_policy_id"] = policy_v2.ENTRY_POLICY_ID
    with pytest.raises(ValueError, match="policy mismatch"):
        check(complete_case)


def test_quality_gate_order_does_not_change_computed_observations(case):
    plan = json.loads((audit.HERE / "PLAN_V2.json").read_bytes())
    before = audit._quality(case["docs"]["labels"]["rows"], plan)
    plan["data_gates"] = dict(reversed(list(plan["data_gates"].items())))
    assert audit._quality(case["docs"]["labels"]["rows"], plan) == before


def test_metadata_data_sha_checked(case):
    with pytest.raises(audit.AcceptanceError, match="SOURCE_METADATA_DATA_SHA_MISMATCH"):
        check(case, extra=[("data/example.data.json", b"{}"),
                           ("data/example.meta.json", encoded({"data_sha256": "0" * 64}))])
