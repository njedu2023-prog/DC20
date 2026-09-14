"""Synthetic-only fixtures; no market collection, network, trading or fitting."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import socket

import pytest

from top10decision.decision import candidate_formal_shadow_summary as m
from work.profit_1000_upgrade import test_candidate_natural_statistics as old


def projection(case):
    frozen = json.loads(case["item"]["snapshot_raw"])
    return {"schema_version": m.DAY_SCHEMA, "activation_id": m.ACTIVATION_ID,
        "model_canonical_sha256": m.MODEL_SHA, "effective_from_signal_date": m.EFFECTIVE_D,
        "snapshot_file_sha256": case["item"]["expected_snapshot_sha256"],
        **{key: frozen[key] for key in ("signal_date", "exec_date", "exit_date", "candidate_slots")},
        "rows": [{key: row[key] for key in
            ("ts_code", "candidate_rank", "candidate_score", "promotion_rank")}
            for row in frozen["prediction"]["rows"]]}


@pytest.fixture
def case(tmp_path, monkeypatch):
    return old.fixture_case(tmp_path, monkeypatch)


def run(case, **kwargs):
    return m.build_summary(kwargs.pop("activation_config", deepcopy(m.publication.EXPECTED_ACTIVATION)),
        kwargs.pop("projections", [projection(case)]), kwargs.pop("day_inputs", [case["item"]]),
        as_of_date=kwargs.pop("as_of_date", "20260916"), calendar_raw=case["calendar"],
        expected_calendar_sha256=m.sha(case["calendar"]),
        clock=kwargs.pop("clock", lambda: datetime(2026, 9, 17, 8, tzinfo=timezone.utc)), **kwargs)


def without_ledger(case):
    clone = {**case, "item": dict(case["item"])}
    for key in ("ledger_raw", "expected_ledger_sha256", "ledger_as_of_date", "source_collection_receipt_sha256"):
        clone["item"][key] = None
    return clone


def test_actual_native_loss_preserved_and_missing_later_D_enumerated(case, monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("NO_NETWORK"))
    monkeypatch.setattr(m.statistics.natural.scorer, "predict_forward", lambda *a, **k: pytest.fail("NO_REFIT_OR_SCORE"))
    result = run(case)
    assert result["test_only"] is True
    assert result["status"] == "SYNTHETIC_CLOCK_ONLY"
    assert result["signal_dates"] == ["20260914", "20260915", "20260916"]
    assert result["published_signal_dates"] == ["20260914"]
    assert result["missing_signal_dates"] == ["20260915", "20260916"]
    assert result["coverage_complete"] is False
    for group in result["groups"].values():
        assert group["negative_filled_slots"] == group["filled_settled_slots"] == 1
        assert group["filled_proxy_win_rate"] == 0
        assert group["mean_net_filled_proxy_return"] == pytest.approx(-.0245)
        assert group["missing_publication_slots"] == 2
        assert group["expected_selection_days"] == 3
        assert group["verified_selection_days"] == 1
        assert group["synthetic_cumulative_net_return"] is group["synthetic_max_drawdown"] is None
        assert group["daily_sequence"][0]["terminal_economic_source_sha256"]
        assert group["daily_sequence"][1]["slot_net_return"] is None
    for key, value in m.FLAGS.items():
        assert result[key] == value


def test_first_frozen_day_records_exact_two_pending_slots(case):
    result = run(without_ledger(case), as_of_date="20260914")
    assert result["coverage_complete"] is True
    assert result["expected_slot_count"] == 2
    for rank, name in enumerate(m.GROUPS, 1):
        group = result["groups"][name]
        row = group["daily_sequence"][0]
        assert row["slot"] == row["candidate_rank"] == rank
        assert row["status"] == "PENDING_T_NOT_DUE"
        assert group["pending_slots"] == group["not_due_slots"] == 1
        assert group["known_no_fill_slots"] == group["complete_day_count"] == 0
        assert group["synthetic_cumulative_net_return"] is group["filled_proxy_win_rate"] is None


def test_no_snapshot_is_missing_D_not_empty_ledger_or_zero(case):
    result = run(case, day_inputs=[], projections=[], as_of_date="20260914")
    assert result["missing_signal_dates"] == ["20260914"]
    assert len(result["groups"]) == 2
    for group in result["groups"].values():
        assert len(group["daily_sequence"]) == 1
        assert group["daily_sequence"][0]["status"] == "MISSING_D_PUBLICATION"
        assert group["daily_sequence"][0]["slot_net_return"] is None


def test_before_cutover_is_empty_new_model_not_legacy_returns(case, monkeypatch):
    result = run(case, day_inputs=[], projections=[], as_of_date="20260911")
    assert result["signal_dates"] == result["published_signal_dates"] == []
    assert result["expected_slot_count"] == 0
    for group in result["groups"].values():
        assert group["daily_sequence"] == []
        assert group["synthetic_cumulative_net_return"] is group["synthetic_max_drawdown"] is None
    monkeypatch.setattr(m, "_validation_calendar", lambda: case["calendar"])
    assert m.validate_summary(result, m.publication.EXPECTED_ACTIVATION, allow_test_only=True) == result


@pytest.mark.parametrize("size", [0, 1])
def test_frozen_missing_candidate_is_a_separate_slot(tmp_path, monkeypatch, size):
    case = old.fixture_case(tmp_path, monkeypatch, size=size)
    result = run(without_ledger(case), as_of_date="20260914")
    for rank, name in enumerate(m.GROUPS, 1):
        group = result["groups"][name]
        assert group["missing_candidate_slots"] == int(rank > size)
        assert group["missing_publication_slots"] == 0
        assert group["verified_selection_days"] == 1
        if rank > size:
            assert group["daily_sequence"][0]["status"] == "MISSING_CANDIDATE"
            assert group["daily_sequence"][0]["slot_net_return"] is None


@pytest.mark.parametrize("kind,status", [("capacity", "NO_FILL_CAPACITY"),
    ("missing_minutes", "PENDING_EXIT_MISSING_MINUTES"),
    ("missing_auction", "PENDING_T_MISSING_CANONICAL_AUCTION")])
def test_native_no_fill_unknown_and_known_loss_not_reclassified(tmp_path, monkeypatch, kind, status):
    case = old.fixture_case(tmp_path, monkeypatch, kind=kind)
    result = run(case)
    matching = [g for g in result["groups"].values() if status in g["status_counts"]]
    assert matching
    for group in matching:
        row = group["daily_sequence"][0]
        if status.startswith("NO_FILL"):
            assert row["slot_net_return"] == 0
            assert group["known_no_fill_slots"] == 1
            assert group["filled_proxy_win_rate"] is None
        else:
            assert row["slot_net_return"] is None
            assert group["pending_slots"] == 1
            assert group["known_no_fill_slots"] == 0


def test_auction_vs_open_proxy_source_is_preserved(tmp_path, monkeypatch):
    case = old.fixture_case(tmp_path, monkeypatch, kind="empty")
    result = run(case)
    rows = [g["daily_sequence"][0] for g in result["groups"].values()]
    assert any(row["entry_price_source"] == "DAILY_OPEN_PROXY" for row in rows)
    assert any(row["entry_price_source"] == "TUSHARE_STK_AUCTION" for row in rows)
    assert result["actual_capacity_verified"] is False


@pytest.mark.parametrize("key,value", [("enabled", "false"), ("activation_id", "old-model"),
    ("effective_from_signal_date", "20260910"), ("model_canonical_sha256", "0" * 64),
    ("negative_score_skip_allowed", True), ("round_trip_cost_rate", .009)])
def test_activation_exact_contract_required(case, key, value):
    config = deepcopy(m.publication.EXPECTED_ACTIVATION)
    config[key] = value
    with pytest.raises(ValueError):
        run(case, activation_config=config)


@pytest.mark.parametrize("field", ["candidate_rank", "candidate_score", "promotion_rank", "ts_code"])
def test_frozen_ranks_scores_members_cannot_change(case, field):
    day = projection(case)
    day["rows"][0][field] = "changed" if field == "ts_code" else 123
    with pytest.raises(ValueError, match="FROZEN_RANK_OR_SCORE"):
        run(case, projections=[day])


@pytest.mark.parametrize("kind", ["extra", "omit", "duplicate", "legacy", "snapshot", "slots"])
def test_day_projection_and_proof_universe_cannot_diverge(case, kind):
    rows = [projection(case)]
    if kind == "extra": rows[0]["signal_date"] = "20260915"
    elif kind == "omit": rows = []
    elif kind == "duplicate": rows *= 2
    elif kind == "legacy": rows[0]["activation_id"] = "legacy"
    elif kind == "snapshot": rows[0]["snapshot_file_sha256"] = "0" * 64
    elif kind == "slots": rows[0]["candidate_slots"][0]["candidate_score"] = 50
    with pytest.raises(ValueError):
        run(case, projections=rows)


def test_plain_dictionary_cannot_stand_in_for_issued_publication(case):
    case["item"]["publication_proof"] = {"verified": True}
    with pytest.raises(ValueError, match="PRIVATE_ISSUED"):
        run(case)


def test_duplicate_D_is_rejected_before_statistics(case):
    with pytest.raises(ValueError, match="SORTED_UNIQUE"):
        run(case, day_inputs=[case["item"], case["item"]])


def test_prior_identical_result_is_idempotent_without_timestamp_churn(case):
    previous = run(case)
    assert run(case, previous_summary=previous) == previous


def test_known_terminal_may_not_return_to_pending(case):
    previous = run(case)
    with pytest.raises(ValueError, match="TERMINAL_SHADOW_HISTORY_CHANGED"):
        run(without_ledger(case), previous_summary=previous)


def test_prior_frozen_snapshot_cannot_disappear(case):
    previous = run(case)
    with pytest.raises(ValueError, match="FROZEN_SAME_D_CHANGED"):
        run(case, day_inputs=[], projections=[], previous_summary=previous)


def test_prior_summary_seal_rejects_mutation(case):
    previous = run(case)
    previous["groups"]["candidate_top1"]["mean_net_filled_proxy_return"] = 10
    with pytest.raises(ValueError, match="PRIOR_SUMMARY_SEAL_CHANGED"):
        run(case, previous_summary=previous)


@pytest.mark.parametrize("field,value", [("slot_net_return", .1),
    ("terminal_economic_source_sha256", "f" * 64), ("entry_price_source", "FORGED_PRICE")])
def test_even_resealed_prior_terminal_cannot_change_value_or_source(case, field, value):
    previous = run(case)
    previous["groups"]["candidate_top1"]["daily_sequence"][0][field] = value
    previous["summary_sha256"] = m.canonical({key: value for key, value in previous.items() if key != "summary_sha256"})
    with pytest.raises(ValueError, match="TERMINAL_SHADOW_HISTORY_CHANGED"):
        run(case, previous_summary=previous)


def test_synthetic_aggregate_drawdown_has_correct_loss_sign_and_zero_no_fill():
    def row(day, status, value):
        return {**m._missing_day(day, 1), "status": status,
            "slot_net_return": value, "snapshot_file_sha256": "1" * 64}
    sequence = [row("20260914", m.statistics.labels.SETTLED, -.1),
        row("20260915", m.statistics.labels.SETTLED, .2),
        row("20260916", "NO_FILL_CAPACITY", 0),
        row("20260917", m.statistics.labels.SETTLED, -.05)]
    group = m._metrics(sequence, coverage_complete=True)
    assert group["synthetic_cumulative_net_return"] == pytest.approx(.026)
    assert group["synthetic_max_drawdown"] == pytest.approx(-.1)
    assert group["filled_proxy_win_rate"] == pytest.approx(1 / 3)
    assert group["complete_day_count"] == 4
    assert group["known_no_fill_slots"] == 1
    assert group["cumulative_available"] is True


def test_empty_metrics_never_claim_zero_return_or_zero_drawdown():
    group = m._metrics([], coverage_complete=True)
    assert group["filled_proxy_win_rate"] is group["mean_net_filled_proxy_return"] is None
    assert group["synthetic_cumulative_net_return"] is group["synthetic_max_drawdown"] is None
    assert group["cumulative_available"] is False


def test_cached_summary_validator_recomputes_metrics_without_source_claim(case, monkeypatch):
    output = run(case)
    monkeypatch.setattr(m, "_validation_calendar", lambda: case["calendar"])
    assert m.validate_summary(output, m.publication.EXPECTED_ACTIVATION, allow_test_only=True) == output
    with pytest.raises(ValueError, match="SYNTHETIC_SUMMARY_CANNOT_BE_PUBLISHED"):
        m.validate_summary(output, m.publication.EXPECTED_ACTIVATION)


@pytest.mark.parametrize("mutation", ["win", "extra_day", "zero_missing", "actual_fills", "wrong_snapshot"])
def test_resealed_cached_metric_or_universe_forgery_rejected(case, monkeypatch, mutation):
    output = run(case)
    monkeypatch.setattr(m, "_validation_calendar", lambda: case["calendar"])
    group = output["groups"]["candidate_top1"]
    if mutation == "win": group["filled_proxy_win_rate"] = 1
    elif mutation == "extra_day": output["signal_dates"].append("20260917")
    elif mutation == "zero_missing": group["daily_sequence"][1]["slot_net_return"] = 0
    elif mutation == "actual_fills": output["actual_execution_claimed"] = True
    elif mutation == "wrong_snapshot": group["daily_sequence"][0]["snapshot_file_sha256"] = "5" * 64
    output["summary_sha256"] = m.canonical({key: value for key, value in output.items() if key != "summary_sha256"})
    with pytest.raises(ValueError):
        m.validate_summary(output, m.publication.EXPECTED_ACTIVATION, allow_test_only=True)


def test_disable_preserves_frozen_slots_and_settled_losses_not_legacy_fallback(case, monkeypatch):
    before = run(case)
    config = deepcopy(m.publication.EXPECTED_ACTIVATION)
    config["enabled"] = False
    after = run(case, activation_config=config, previous_summary=before)
    assert after["enabled"] is False and after["publication_paused"] is True
    assert after["groups"] == before["groups"]
    assert after["groups"]["candidate_top1"]["negative_filled_slots"] == 1
    monkeypatch.setattr(m, "_validation_calendar", lambda: case["calendar"])
    assert m.validate_summary(after, config, allow_test_only=True) == after
    with pytest.raises(ValueError, match="FROZEN_SAME_D_CHANGED"):
        run(case, activation_config=config, projections=[], day_inputs=[], previous_summary=before)


def test_negative_score_is_not_a_skip_instruction():
    item = {"ledger_raw": None, "expected_snapshot_sha256": "1" * 64}
    slot = {"ts_code": "000001.SZ", "candidate_rank": 1, "candidate_score": -.9, "promotion_rank": 3}
    frozen = {"signal_date": "20260914", "exec_date": "20260915", "exit_date": "20260916",
        "candidate_slots": [slot]}
    row = m._row(item, frozen, {"signal_date": "20260914", "ts_code": "000001.SZ",
        "status": "MISSING_OUTCOME_LEDGER", "proxy_fill": None, "slot_net_return": None,
        "ledger_as_of_date": None, "label_available_date": None}, 1, "20260914")
    assert row["candidate_score"] == -.9 and row["ts_code"] == "000001.SZ"
    assert row["status"] == "PENDING_T_NOT_DUE"


def test_published_terminal_cache_is_byte_identical_and_needs_no_new_old_proof(case, monkeypatch):
    before = run(case)
    monkeypatch.setattr(m.statistics, "_publication_type", lambda: pytest.fail("OLD_ARTIFACT_REVALIDATION"))
    after = run(case, day_inputs=[], previous_summary=before, cached_terminal_days=["20260914"])
    assert after == before
    assert after["summary_sha256"] == before["summary_sha256"]
    assert after["groups"]["candidate_top1"]["negative_filled_slots"] == 1


@pytest.mark.parametrize("kind", ["missing_minutes", "missing_auction"])
def test_one_pending_slot_forbids_caching_entire_D(tmp_path, monkeypatch, kind):
    case = old.fixture_case(tmp_path, monkeypatch, kind=kind)
    before = run(case)
    with pytest.raises(ValueError, match="PENDING_OR_MISSING_EVIDENCE_CANNOT_BE_CACHED"):
        run(case, day_inputs=[], previous_summary=before, cached_terminal_days=["20260914"])


@pytest.mark.parametrize("size", [0, 1])
def test_true_frozen_missing_candidate_can_be_reused_but_never_settled_or_zero(tmp_path, monkeypatch, size):
    case = old.fixture_case(tmp_path, monkeypatch, size=size)
    before = run(case)
    after = run(case, day_inputs=[], previous_summary=before, cached_terminal_days=["20260914"])
    assert after == before
    for rank, name in enumerate(m.GROUPS, 1):
        if rank > size:
            group = after["groups"][name]
            assert group["daily_sequence"][0]["slot_net_return"] is None
            assert group["complete_day_count"] == group["known_no_fill_slots"] == 0


def test_terminal_known_no_fill_cache_preserves_zero_not_win(tmp_path, monkeypatch):
    case = old.fixture_case(tmp_path, monkeypatch, kind="capacity")
    before = run(case)
    after = run(case, day_inputs=[], previous_summary=before, cached_terminal_days=["20260914"])
    assert after == before
    assert any(group["known_no_fill_slots"] == 1 and group["filled_proxy_win_rate"] is None
        for group in after["groups"].values())


@pytest.mark.parametrize("mutation", ["score", "p0", "snapshot", "day", "model"])
def test_terminal_cache_cannot_change_any_original_formal_projection(case, mutation):
    before = run(case)
    day = projection(case)
    if mutation == "score": day["rows"][0]["candidate_score"] += 1
    elif mutation == "p0": day["p0_file_sha256"] = "f" * 64
    elif mutation == "snapshot": day["snapshot_file_sha256"] = "f" * 64
    elif mutation == "day": day["signal_date"] = "20260915"
    elif mutation == "model": day["model_canonical_sha256"] = "f" * 64
    with pytest.raises(ValueError):
        run(case, projections=[day], day_inputs=[], previous_summary=before, cached_terminal_days=["20260914"])


@pytest.mark.parametrize("mutation", ["profit", "date", "native_source"])
def test_changed_previous_cached_economics_rejected(case, mutation):
    before = run(case)
    row = before["groups"]["candidate_top1"]["daily_sequence"][0]
    if mutation == "profit": row["slot_net_return"] = 1
    elif mutation == "date": row["exit_date"] = "20260917"
    elif mutation == "native_source": row["terminal_economic_source_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="SUMMARY_SEAL_CHANGED"):
        run(case, day_inputs=[], previous_summary=before, cached_terminal_days=["20260914"])


def test_cache_requires_original_summary_and_does_not_overlap_fresh_input(case):
    with pytest.raises(ValueError, match="NONOVERLAPPING"):
        run(case, cached_terminal_days=["20260914"])
    with pytest.raises(ValueError, match="BOUND_PREVIOUS_SUMMARY"):
        run(case, day_inputs=[], cached_terminal_days=["20260914"])


def test_missing_D_or_missing_outcome_cannot_be_cached_as_complete(case):
    before = run(without_ledger(case))
    with pytest.raises(ValueError, match="PENDING_OR_MISSING_EVIDENCE"):
        run(case, day_inputs=[], previous_summary=before, cached_terminal_days=["20260914"])
