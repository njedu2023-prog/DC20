"""Fixed-gate synthetic research inputs; no market source authority is claimed."""
from __future__ import annotations

from copy import deepcopy
import csv
import gzip
from pathlib import Path

import pytest

from work.profit_1000_upgrade import candidate_v3 as v3, candidate as old
from work.profit_1000_upgrade import candidate_labels_overlay as overlay, policy_v3


def at(day, time):
    return f"{day[:4]}-{day[4:6]}-{day[6:]}T{time}+08:00"


def label(day, code, t, t1, rank):
    net = -.0245 if rank % 2 else -.0445
    evidence = {"source_policy_id": policy_v3.AUCTION_SOURCE_POLICY_ID,
        "status": "CANONICAL_PRICE_OBSERVED", "price": 10., "entry_price_source": "TUSHARE_STK_AUCTION",
        "capacity_proxy_verified": False, "capacity_amount": None, "capacity_reason": "AMOUNT_INVALID_OR_MISSING",
        "capacity_evidence": "UNKNOWN", "price_qualified": True, "amount": None,
        "loaded_source_qualification_claimed": True, "actual_capacity_verified": False,
        "actual_execution_claimed": False, "basis": policy_v3.BASIS}
    return {**dict(policy_v3.CONTRACT), "signal_date": day, "ts_code": code, "exec_date": t, "scheduled_exit_date": t1,
        "label_policy_id": old.POLICY_ID, "label_status": old.SETTLED, "entry_price": 10.,
        "entry_price_source": "TUSHARE_STK_AUCTION", "shadow_max_price": None, "round_trip_cost_rate": .0045,
        "research_only": True, "actual_execution_claimed": False, "actual_capacity_verified": False,
        "production_activation_allowed": False, "known_before_0925": False,
        "price_reporting_precision_confirmed": False, "reported_price_preserved": True,
        "basis": policy_v3.BASIS, "auction_request_receipt_observed": True, "auction_trade_observed": True,
        "minute_source_observed": True, "minute_source_observed_dates": [t1],
        "capacity_proxy_verified": False, "capacity_amount": None, "capacity_reason": "AMOUNT_INVALID_OR_MISSING",
        "capacity_evidence": "UNKNOWN", "price_qualified": True,
        "entry_price_evidence": evidence, "entry_qualification_status": "CANONICAL_PRICE_OBSERVED",
        "proxy_fill": 1, "net_return": net, "conditional_net_return": net, "slot_net_return": net,
        "actual_exit_date": t1, "actual_exit_time": at(t1, "10:00:00"),
        "label_available_date": t1, "label_available_at": at(t1, "15:00:00"), "label_maturity_at": at(t1, "10:00:00"),
        "exit_evidence": {"gross_return": net + .0045, "actual_exit_date": t1, "actual_exit_time": at(t1, "10:00:00"),
                          "exit_policy_id": old.POLICY_ID},
        "t_daily_volume": 2_000_000, "t_daily_ohlc_flat_at_cent": True, "t_opening_limit_up_observed": False}


def bind(case):
    rows, labels, manifest = case
    manifest["development_frozen_rows_sha256"] = v3.canonical_sha([r for r in rows if r.get("signal_date") < v3.HOLDOUT_START])
    manifest["development_label_rows_sha256"] = v3.canonical_sha([r for r in labels if r.get("signal_date") < v3.HOLDOUT_START])
    return case


@pytest.fixture(scope="module")
def population():
    root = Path(__file__).resolve().parents[2]
    with (root / "data/market/trade_cal_sse.csv").open(encoding="utf-8-sig", newline="") as handle:
        all_dates = [r["cal_date"] for r in csv.DictReader(handle) if r["is_open"] == "1"]
    # The registered cohort has 910 D dates, not every exchange-open date in
    # that span (912). Read only frozen D identities, never old outcome fields.
    with gzip.open(root / "data/decision_executable_profit/historical_oof_top10_ledger.csv.gz", "rt", encoding="utf-8-sig") as handle:
        dates = sorted({r["signal_date"] for r in csv.DictReader(handle)})
    assert len(dates) == 910
    counts = {d: 8 if i < 383 else 7 for i, d in enumerate(dates)}
    rows, labels = [], []
    for day in dates:
        index = all_dates.index(day)
        for rank in range(1, counts[day] + 1):
            code = f"{600000 + rank:06}.SH"
            rows.append({"signal_date": day, "ts_code": code, "promotion_rank": rank, "board_stage": 2 + rank % 2,
                         "feature_as_of_date": day, "promotion_oof_train_end": all_dates[index - 1],
                         "d_pct_change": rank / 100, "volume_ratio": float(rank), "path_change": None})
            labels.append(label(day, code, all_dates[index + 1], all_dates[index + 2], rank))
    manifest = {"source_sha256": v3.SOURCE_SHA, "source_provenance_verified": True,
        "feature_timestamp_semantics": "RETROSPECTIVE_D_ONLY_BOUND", "promotion_prediction_provenance": "HISTORICAL_OOF",
        "entry_policy_id": policy_v3.ENTRY_POLICY_ID, "cost_rate": .0045,
        "day_candidate_counts": counts, "source_overlay_contract": deepcopy(overlay.CONTRACT),
        "base_archive_sha256": v3.BASE_ARCHIVE_SHA, "label_report_sha256": "a" * 64,
        "candidate_collection_receipt_sha256": "b" * 64, "market_collection_receipt_sha256": None}
    assert len(rows) == len(labels) == 6753
    policy_v3.validate_label_contract(labels[0])
    return bind((rows, labels, manifest))


@pytest.fixture
def data(population):
    return deepcopy(population)


def run(case, **kwargs):
    rows, labels, manifest = case
    return v3.run_candidate_v3(rows, labels, frozen_manifest=manifest, **kwargs)


def pending(item):
    item.update(label_status="PENDING_EXIT_MISSING_MINUTES", net_return=None, conditional_net_return=None,
                slot_net_return=None, actual_exit_date=None, actual_exit_time=None, label_available_date=None,
                label_available_at=None, label_maturity_at=None, minute_source_observed=False, minute_source_observed_dates=[])
    item.pop("exit_evidence", None)


def no_fill(item):
    t = item["exec_date"]
    item.update(label_status="NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED", proxy_fill=0, net_return=None,
        conditional_net_return=None, slot_net_return=0., actual_exit_date=None, actual_exit_time=None,
        label_available_date=t, label_available_at=at(t, "15:00:00"), label_maturity_at=at(t, "09:25:00"),
        minute_source_observed=False, minute_source_observed_dates=[], t_opening_limit_up_observed=True)
    item.pop("exit_evidence", None)


def test_real_fixed_gate_size_fits_development_only_and_keeps_negative_scores(data, monkeypatch):
    monkeypatch.setattr(old, "run_candidate", lambda *a, **k: pytest.fail("legacy policy runner must not be called"))
    fitted = run(data)
    assert fitted["status"] == "DEVELOPMENT_ONLY_FITTED", fitted["report"]
    report = fitted["report"]
    assert report["quality_gates"] == v3.GATES and not report["engineering_defaults_weakened"]
    assert report["historical_candidate_rows_retained"] == 6753 and report["historical_candidate_dates_retained"] == 910
    assert report["entry_policy_id"] == policy_v3.ENTRY_POLICY_ID
    assert report["source_overlay_contract"] == overlay.CONTRACT and report["market_evidence_scope"] == "BASE_ONLY"
    assert report["release_readiness"] == "NOT_ELIGIBLE_UNTOUCHED_FORWARD_HOLDOUT_REQUIRED"
    assert report["profitability_improvement_proven"] is report["production_activation_allowed"] is False
    assert report["comparisons"]["frozen_existing_profit"]["status"] == "UNAVAILABLE_SAME_UNIVERSE_BASELINE"
    assert report["feature_coverage"]["promotion_probability"]["known_rows"] == 0
    assert report["feature_coverage"]["path_label"]["known_rows"] == 0
    assert all(p["candidate_score"] < 0 for p in fitted["predictions"])
    days = {p["signal_date"] for p in fitted["predictions"]}
    selected = [p for p in fitted["predictions"] if p["selected_shadow_slot"]]
    assert len(selected) == 2 * len(days) and {p["selected_shadow_slot"] for p in selected} == {1, 2}
    assert all(p["entry_policy_id"] == policy_v3.ENTRY_POLICY_ID for p in fitted["predictions"])
    model = fitted["candidate_model"]
    assert model["schema_version"] == v3.SCHEMA and model["specification"] == old.MODEL_SPEC
    assert model["fit_signal_date_max"] < v3.TRAIN_CUTOFF and model["fit_label_available_date_max"] < v3.TRAIN_CUTOFF
    comparison = report["comparisons"]["candidate"]
    for rank in ("top1", "top2"):
        assert comparison["stress_90bp_same_selections"][rank]["mean_net_slot_return"] == pytest.approx(
            comparison[rank]["mean_net_slot_return"] - .0045)
    assert comparison["stress_90bp_same_selections"]["selection_reoptimized"] is False


@pytest.mark.parametrize("key,value", [("as_of_date", "20260912"), ("training_cutoff_date", "20250101"),
    ("validation_end_date", "20260901"), ("holdout_start_date", "20260915")])
def test_registered_windows_cannot_be_moved(data, key, value):
    result = run(data, **{key: value})
    assert result["status"] == "BLOCKED_INPUT" and not result["report"]["training_performed"]
    assert result["report"]["input_error"] == "REGISTERED_DATE_WINDOWS_CANNOT_MOVE"


def test_no_gate_override_parameter(data):
    with pytest.raises(TypeError, match="gates"):
        run(data, gates={"min_train_dates": 1})


@pytest.mark.parametrize("field,value", [("source_sha256", "0" * 64), ("base_archive_sha256", "0" * 64),
    ("label_report_sha256", None), ("candidate_collection_receipt_sha256", "missing"),
    ("source_provenance_verified", False), ("promotion_prediction_provenance", "IN_SAMPLE"),
    ("feature_timestamp_semantics", "AFTER_T"), ("entry_policy_id", "research_canonical_auction_or_open_no_cap_v2"),
    ("cost_rate", .009), ("source_overlay_contract", dict(policy_v3.CONTRACT)),
    ("source_policy_contract", dict(policy_v3.CONTRACT)), ("market_collection_receipt_sha256", True)])
def test_unbound_or_relabelled_inputs_never_fit(data, monkeypatch, field, value):
    data[2][field] = value
    monkeypatch.setattr(old, "_fit_ridge", lambda *a: pytest.fail("fit must stay behind input gate"))
    result = run(data)
    assert result["status"] == "BLOCKED_INPUT" and not result["report"]["training_performed"]


def test_pending_one_validation_member_blocks_whole_validation_not_dropped(data, monkeypatch):
    item = next(r for r in data[1] if r["signal_date"] >= v3.TRAIN_CUTOFF)
    pending(item)
    bind(data)
    monkeypatch.setattr(old, "_fit_ridge", lambda *a: pytest.fail("incomplete validation must not fit"))
    result = run(data)
    assert result["status"] == "BLOCKED_DATA_QUALITY"
    assert {"min_validation_complete_day_fraction", "INCOMPLETE_VALIDATION_COHORTS_CANNOT_BE_DROPPED"} <= set(result["report"]["failed_quality_gates"])
    assert result["report"]["coverage"]["validation_incomplete_dates"] == [item["signal_date"]]
    assert result["report"]["historical_candidate_rows_retained"] == 6753


def test_more_than_five_percent_training_dates_pending_blocks(data, monkeypatch):
    train_dates = sorted(d for d in data[2]["day_candidate_counts"] if d < v3.TRAIN_CUTOFF)
    damaged = set(train_dates[:len(train_dates) // 20 + 1])
    for item in data[1]:
        if item["signal_date"] in damaged:
            pending(item)
    bind(data)
    monkeypatch.setattr(old, "_fit_ridge", lambda *a: pytest.fail("insufficient training evidence must not fit"))
    result = run(data)
    assert result["status"] == "BLOCKED_DATA_QUALITY"
    assert "min_training_complete_day_fraction" in result["report"]["failed_quality_gates"]


def test_fewer_than_fifty_filled_validation_rows_blocks(data, monkeypatch):
    seen = 0
    for item in data[1]:
        if item["signal_date"] >= v3.TRAIN_CUTOFF:
            seen += 1
            if seen > 49:
                no_fill(item)
    bind(data)
    monkeypatch.setattr(old, "_fit_ridge", lambda *a: pytest.fail("no fill count bypass"))
    result = run(data)
    assert result["status"] == "BLOCKED_DATA_QUALITY"
    assert result["report"]["coverage"]["validation_filled_rows"] == 49
    assert "min_validation_filled_rows" in result["report"]["failed_quality_gates"]


def test_constant_target_is_not_fitted(data):
    for item in data[1]:
        item.update(net_return=-.0245, conditional_net_return=-.0245, slot_net_return=-.0245)
        item["exit_evidence"]["gross_return"] = -.02
    bind(data)
    result = run(data)
    assert result["status"] == "BLOCKED_DATA_QUALITY"
    assert "TRAINING_TARGET_HAS_NO_VARIATION" in result["report"]["failed_quality_gates"]


@pytest.mark.parametrize("bad", ["missing_label", "duplicate_label", "missing_candidate", "rank", "feature_sha", "labels_sha", "future_oof", "bad_label_source", "missing_market_marker"])
def test_complete_universe_provenance_and_all_sha_are_required(data, monkeypatch, bad):
    if bad == "missing_label": data[1].pop()
    elif bad == "duplicate_label": data[1].append(data[1][0])
    elif bad == "missing_candidate": data[0].pop()
    elif bad == "rank": data[0][0]["promotion_rank"] = 2
    elif bad == "future_oof": data[0][0]["promotion_oof_train_end"] = data[0][0]["signal_date"]
    elif bad == "bad_label_source": data[1][0]["auction_source_policy_id"] = "unknown"
    elif bad == "missing_market_marker": data[2].pop("market_collection_receipt_sha256")
    bind(data)
    if bad == "feature_sha": data[2]["development_frozen_rows_sha256"] = "0" * 64
    elif bad == "labels_sha": data[2]["development_label_rows_sha256"] = "0" * 64
    monkeypatch.setattr(old, "_fit_ridge", lambda *a: pytest.fail("invalid manifest must not fit"))
    result = run(data)
    assert result["status"] == "BLOCKED_INPUT" and result["candidate_model"] is None


class OpaqueFuture(dict):
    def get(self, key, default=None):
        assert key in {"signal_date", "ts_code"}, "future content was inspected"
        return super().get(key, default)
    def items(self):
        raise AssertionError("future contents must not be hashed/copied")


def test_future_holdout_outcomes_and_features_not_read_or_hashed(data):
    data[0].append(OpaqueFuture(signal_date=v3.HOLDOUT_START, ts_code="600999.SH", future_feature="FORBIDDEN"))
    data[1].append(OpaqueFuture(signal_date=v3.HOLDOUT_START, ts_code="600999.SH", label_status="FORBIDDEN", net_return=object()))
    result = run(data)
    assert result["status"] == "DEVELOPMENT_ONLY_FITTED"
    assert result["report"]["holdout_rows_excluded_without_outcome_evaluation"] == 1
    assert all(p["signal_date"] < v3.HOLDOUT_START for p in result["predictions"])


def test_new_candidate_source_identity_accepted_without_v2_projection(data):
    item = data[1][0]
    item.update(auction_source_policy_id=overlay.candidate_source.SOURCE_POLICY_ID,
                auction_qualification_policy_id=overlay.qualification.SOURCE_POLICY_ID,
                source_overlay_policy_id=overlay.OVERLAY_POLICY_ID)
    raw = {"ts_code": item["ts_code"], "trade_date": item["exec_date"], "price": 10., "vol": 10000, "amount": None, "pre_close": 10.}
    proof = overlay.qualification.qualify_row(raw, item["exec_date"], item["ts_code"], 10.)
    item["entry_price_evidence"] = {**proof, "source_policy_id": overlay.candidate_source.SOURCE_POLICY_ID,
        "qualification_policy_id": overlay.qualification.SOURCE_POLICY_ID, "overlay_policy_id": overlay.OVERLAY_POLICY_ID,
        "source_origin": "candidate", "daily_source_origin": "base", "source_only_metadata_rewritten": False,
        "loaded_source_qualification_claimed": True, "entry_price_source": "TUSHARE_STK_AUCTION", "daily_open": 10.}
    overlay.validate_label_contract(item)
    bind(data)
    result = run(data)
    assert result["status"] == "DEVELOPMENT_ONLY_FITTED"
    assert item["auction_source_policy_id"] == overlay.candidate_source.SOURCE_POLICY_ID
    assert result["candidate_model"]["source_overlay_contract"] == overlay.CONTRACT


def test_source_library_does_not_mutate_inputs_or_write(data):
    before = v3.canonical_sha(data)
    result = run(data)
    assert result["status"] == "DEVELOPMENT_ONLY_FITTED"
    assert v3.canonical_sha(data) == before
    assert result["report"]["source_verification"].startswith("CALLER_VERIFIED")


@pytest.mark.parametrize("train_days,train_rows,validation_days,validation_rows,expected", [
    (251, 5, 60, 5, "min_train_dates"), (252, 3, 60, 5, "min_train_rows"),
    (252, 5, 59, 6, "min_validation_dates"), (252, 5, 60, 4, "min_validation_rows")])
def test_each_registered_count_gate_is_effective(population, train_days, train_rows, validation_days, validation_rows, expected):
    # Pure gate unit inputs are not a permission to shrink a real 6753-row run.
    days = sorted(population[2]["day_candidate_counts"])
    before = [d for d in days if d < v3.TRAIN_CUTOFF][:train_days]
    after = [d for d in days if d >= v3.TRAIN_CUTOFF][:validation_days]
    prepared = [{"signal_date": d, "terminal": True, "label_available_date": d,
                 "actual_exit_date": None, "proxy_fill": 1, "slot_net_return": -.01 if i % 2 else .01}
                for dates, count in ((before, train_rows), (after, validation_rows))
                for d in dates for i in range(count)]
    assert expected in v3._quality(prepared)[3]


def test_train_labels_maturing_at_cutoff_are_purged_before_fitting(data, monkeypatch):
    damaged_day = data[0][0]["signal_date"]
    for item in data[1]:
        if item["signal_date"] == damaged_day:
            item.update(actual_exit_date=v3.TRAIN_CUTOFF, actual_exit_time=at(v3.TRAIN_CUTOFF, "10:00:00"),
                label_available_date=v3.TRAIN_CUTOFF, label_available_at=at(v3.TRAIN_CUTOFF, "15:00:00"),
                label_maturity_at=at(v3.TRAIN_CUTOFF, "10:00:00"), minute_source_observed_dates=[v3.TRAIN_CUTOFF])
            item["exit_evidence"].update(actual_exit_date=v3.TRAIN_CUTOFF, actual_exit_time=at(v3.TRAIN_CUTOFF, "10:00:00"))
    bind(data)
    actual_fit = old._fit_ridge
    def checked_fit(training, validation):
        assert all(r["signal_date"] != damaged_day and r["label_available_date"] < v3.TRAIN_CUTOFF for r in training)
        return actual_fit(training, validation)
    monkeypatch.setattr(old, "_fit_ridge", checked_fit)
    result = run(data)
    assert result["status"] == "DEVELOPMENT_ONLY_FITTED"
    assert damaged_day in result["report"]["coverage"]["training_purged_or_incomplete_dates"]


def test_postfit_integrity_failure_withholds_model_but_does_not_deny_fit_happened(data, monkeypatch):
    calls = []
    real_guard = v3._guard
    def guard():
        calls.append(True)
        if len(calls) == 3:
            raise ValueError("SIMULATED_POSTFIT_CODE_DRIFT")
        real_guard()
    monkeypatch.setattr(v3, "_guard", guard)
    result = run(data)
    assert result["status"] == "BLOCKED_INPUT" and result["candidate_model"] is None and not result["predictions"]
    assert result["report"]["training_attempted"] is result["report"]["training_performed"] is True
    assert result["report"]["production_activation_allowed"] is False
