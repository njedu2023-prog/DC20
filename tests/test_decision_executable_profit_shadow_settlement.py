from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from top10decision.decision import executable_profit_shadow as shadow
from top10decision.decision import executable_profit_shadow_settlement as settlement


ROOT = Path(__file__).resolve().parents[1]
AS_OF_D = "20260824"
AS_OF_T = "20260825"
AS_OF_T1 = "20260826"
AS_OF_DELAYED = "20260827"


@pytest.fixture(autouse=True)
def _selection_validator_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    # The scorer has its own exhaustive contract tests. These tests isolate the
    # downstream immutable truth, source binding and deterministic accounting.
    monkeypatch.setattr(
        settlement,
        "validate_internal_forward_shadow_payload",
        lambda payload, require_downloads=False: None,
    )


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_csv(path: Path, header: str, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")


def _selection_payload(slot_count: int = 2) -> dict:
    all_rows = [
        {
            "ts_code": "600001.SH",
            "name": "样本一",
            "industry": "测试",
            "stage_transition": "2→3",
            "promotion_rank": 2,
            "predicted_promotion_probability": 0.70,
            "research_fill_proxy_score": 0.80,
            "research_conditional_profit_score": 0.75,
            "research_joint_proxy_score": 0.60,
            "internal_shadow_order": 1,
            "internal_shadow_selected": 1,
            "shadow_slot": 1,
            "shadow_max_price": 10.10,
            "shadow_price_basis": "D_FROZEN_RECOMMENDED_MAX_PRICE",
            "shadow_price_source_sha256": "c" * 64,
            "lagged_prior_max_history_exit_date": "20260821",
            "lagged_prior_snapshot_sha256": "e" * 64,
        },
        {
            "ts_code": "000002.SZ",
            "name": "样本二",
            "industry": "测试",
            "stage_transition": "3→4",
            "promotion_rank": 1,
            "predicted_promotion_probability": 0.80,
            "research_fill_proxy_score": 0.70,
            "research_conditional_profit_score": 0.70,
            "research_joint_proxy_score": 0.49,
            "internal_shadow_order": 2,
            "internal_shadow_selected": 1,
            "shadow_slot": 2,
            "shadow_max_price": 11.10,
            "shadow_price_basis": "D_FROZEN_RECOMMENDED_MAX_PRICE",
            "shadow_price_source_sha256": "c" * 64,
            "lagged_prior_max_history_exit_date": "20260821",
            "lagged_prior_snapshot_sha256": "f" * 64,
        },
    ][:slot_count]
    top2 = [
        {
            "shadow_slot": row["shadow_slot"],
            "ts_code": row["ts_code"],
            "promotion_rank": row["promotion_rank"],
            "research_fill_proxy_score": row["research_fill_proxy_score"],
            "research_conditional_profit_score": row[
                "research_conditional_profit_score"
            ],
            "research_joint_proxy_score": row["research_joint_proxy_score"],
            "shadow_max_price": row["shadow_max_price"],
            "shadow_price_basis": row["shadow_price_basis"],
            "shadow_price_source_sha256": row["shadow_price_source_sha256"],
        }
        for row in all_rows
    ]
    return {
        "signal_date": "20260824",
        "exec_date": "20260825",
        "exit_date": "20260826",
        "snapshot_sha256": "a" * 64,
        "top10_members_sha256": "b" * 64,
        "source_d_feature": {
            "file_name": "pred_20260824.csv",
            "file_sha256": "c" * 64,
            "generated_at_utc": "2026-08-24T08:00:00Z",
            "generated_at_source": "dated_pred_row_uniform",
            "selected_row_count": slot_count,
            "required_promotion_source_features_present": True,
            "old_feature_incomplete_prediction_allowed": False,
        },
        "ranking_contract": {
            "candidate_scope": "complete frozen promotion TopN, 0<=N<=10",
            "primary_sort": "research_joint_proxy_score descending",
            "tie_breakers": [
                "research_conditional_profit_score descending",
                "research_fill_proxy_score descending",
                "ts_code ascending",
            ],
            "top2_top3_exact_joint_tie_policy": "FAIL_CLOSED_FOR_N_AT_LEAST_3",
            "shadow_slots": 2,
            "shadow_slot_rule": "min(2, N); no padding",
            "top2_frozen_before_outcome_truth": True,
            "entry_policy_id": settlement.ENTRY_POLICY_ID,
            "entry_price_rule": "T proxy open must not exceed D-frozen shadow_max_price",
            "actual_order_fill_claimed": False,
        },
        "rows": all_rows,
        "shadow_top2": {
            "status": "FROZEN_INTERNAL_RESEARCH_ONLY",
            "requested_slots": 2,
            "rows": top2,
            "actual_slots": slot_count,
        },
        "downloads": {},
    }


def _real_validated_single_selection() -> dict:
    signal_date = "20260824"
    code = "600001.SH"
    source_sha = "c" * 64
    row = {
        "ts_code": code,
        "name": "真实合同样本",
        "industry": "测试",
        "stage_transition": "2→3",
        "promotion_rank": 1,
        "predicted_promotion_probability": 0.70,
        "research_fill_proxy_score": 0.80,
        "research_conditional_profit_score": 0.60,
        "research_joint_proxy_score": 0.48,
        "internal_shadow_order": 1,
        "internal_shadow_selected": 1,
        "shadow_slot": 1,
        "shadow_max_price": 10.10,
        "shadow_price_basis": "D_FROZEN_RECOMMENDED_MAX_PRICE",
        "shadow_price_source_sha256": source_sha,
        "lagged_prior_max_history_exit_date": "20260821",
        "lagged_prior_snapshot_sha256": "e" * 64,
    }
    payload = {
        "schema_version": shadow.SCHEMA_VERSION,
        "artifact_kind": shadow.ARTIFACT_KIND,
        "contract_id": shadow.CONTRACT_ID,
        "status": shadow.INTERNAL_STATUS,
        "research_only": True,
        "proxy_scores_uncalibrated": True,
        "score_semantics": {
            "research_fill_proxy_score": "historical public daily-bar buyability proxy; not actual order fill probability",
            "research_conditional_profit_score": "uncalibrated conditional research score among proxy-buyable rows",
            "research_joint_proxy_score": "exact product of the two uncalibrated research proxy scores",
        },
        "signal_date": signal_date,
        "exec_date": "20260825",
        "exit_date": "20260826",
        "feature_as_of_date": signal_date,
        "top10_count": 1,
        "top10_members_sha256": shadow.top10_members_sha256(signal_date, [code]),
        "source_promotion": {
            "authority": "complete_frozen_promotion_topn_only",
            "source_bundle_sha256": "1" * 64,
            "source_feature_snapshot_sha256": "2" * 64,
            "source_top10_members_sha256": shadow.top10_members_sha256(signal_date, [code]),
            "membership_and_promotion_ranks_may_change": False,
        },
        "source_d_feature": {
            "file_name": "pred_20260824.csv",
            "file_sha256": source_sha,
            "generated_at_utc": "2026-08-24T08:00:00Z",
            "generated_at_source": "dated_pred_row_uniform",
            "selected_row_count": 1,
            "required_promotion_source_features_present": True,
            "old_feature_incomplete_prediction_allowed": False,
        },
        "model": {
            "status": shadow.INTERNAL_STATUS,
            "artifact_status": shadow.ARTIFACT_STATUS,
            "model_kind": "hgb",
            "variant": "full_priors",
            "artifact_sha256": shadow.EXPECTED_MODEL_SHA256,
            "model_loaded": True,
            "inference_performed": True,
            "calibrated_probability_output": False,
            "return_lcb_component_available": False,
            "big_loss_tie_break_available": False,
            "retrospective_window_was_viewed": True,
            "independent_untouched_confirmation_available": False,
            "forward_release_evidence_available": False,
        },
        "feature_contract": {
            "feature_count": 156,
            "base_feature_count": 48,
            "lagged_prior_feature_count": 108,
            "required_promotion_source_feature_count": len(shadow.PROMOTION_SOURCE_FEATURES),
            "required_promotion_source_features": list(shadow.PROMOTION_SOURCE_FEATURES),
            "feature_columns_sha256": shadow.EXPECTED_ALL_FEATURES_SHA256,
            "feature_snapshot_sha256": "3" * 64,
            "lagged_prior_max_history_exit_date": "20260821",
            "feature_rows_scored": 1,
            "lagged_prior_rows_built": 1,
            "empty_event_reason": None,
            "strict_history_availability_rule": "outcome availability date strictly before signal D",
        },
        "ranking_contract": {
            "candidate_scope": "complete frozen promotion TopN, 0<=N<=10",
            "primary_sort": "research_joint_proxy_score descending",
            "tie_breakers": [
                "research_conditional_profit_score descending",
                "research_fill_proxy_score descending",
                "ts_code ascending",
            ],
            "top2_top3_exact_joint_tie_policy": "FAIL_CLOSED_FOR_N_AT_LEAST_3",
            "shadow_slots": 2,
            "shadow_slot_rule": "min(2, N); no padding",
            "top2_frozen_before_outcome_truth": True,
            "entry_policy_id": shadow.ENTRY_POLICY_ID,
            "entry_price_rule": "T proxy open must not exceed D-frozen shadow_max_price",
            "actual_order_fill_claimed": False,
        },
        "boundaries": {
            "front_end_rank_allowed": False,
            "official_trade_action_allowed": False,
            "production_model_publish_allowed": False,
            "workflow_connected": False,
            "may_change_promotion_membership": False,
            "may_override_promotion_rank": False,
            "may_create_trade_action": False,
            "actual_order_fill_observed": False,
            "actual_execution_claimed": False,
        },
        "source_hashes": {
            "formal_contract_sha256": shadow.EXPECTED_FORMAL_CONTRACT_SHA256,
            "artifact_index_sha256": shadow.EXPECTED_ARTIFACT_INDEX_SHA256,
            "audit_sha256": shadow.EXPECTED_AUDIT_SHA256,
            "model_pickle_sha256": shadow.EXPECTED_MODEL_SHA256,
            "lagged_priors_sha256": shadow.EXPECTED_LAGGED_PRIORS_SHA256,
            "strict_sse_calendar_sha256": shadow.EXPECTED_CALENDAR_SHA256,
            "full_history_ledger_sha256": shadow.EXPECTED_FULL_HISTORY_LEDGER_SHA256,
            "historical_feature_manifest_sha256": shadow.EXPECTED_HISTORICAL_MANIFEST_SHA256,
        },
        "rows": [row],
        "shadow_top2": {
            "status": "FROZEN_INTERNAL_RESEARCH_ONLY",
            "requested_slots": 2,
            "actual_slots": 1,
            "rows": [
                {
                    "shadow_slot": 1,
                    "ts_code": code,
                    "promotion_rank": 1,
                    "research_fill_proxy_score": 0.80,
                    "research_conditional_profit_score": 0.60,
                    "research_joint_proxy_score": 0.48,
                    "shadow_max_price": 10.10,
                    "shadow_price_basis": "D_FROZEN_RECOMMENDED_MAX_PRICE",
                    "shadow_price_source_sha256": source_sha,
                }
            ],
        },
    }
    payload["snapshot_sha256"] = shadow._canonical_sha256(payload)
    payload["downloads"] = {
        "json_url": "data/decision_executable_profit/forward/selections/shadow_20260824.json",
        "csv_url": "data/decision_executable_profit/forward/selections/shadow_20260824.csv",
        "csv_sha256": "d" * 64,
        "row_count": 1,
    }
    return payload


def _prepare_repo(tmp_path: Path, *, slot_count: int = 2, include_t1: bool = True) -> Path:
    shutil.copytree(
        ROOT / settlement.CONTRACT_PATH.parent,
        tmp_path / settlement.CONTRACT_PATH.parent,
    )
    calendar = tmp_path / settlement.CALENDAR_PATH
    calendar.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / settlement.CALENDAR_PATH, calendar)
    _write_json(
        tmp_path / settlement.SELECTION_ROOT / "shadow_20260824.json",
        _selection_payload(slot_count),
    )
    market = tmp_path / "data/market/raw/2026/20260825"
    _write_csv(
        market / "daily.csv",
        "ts_code,trade_date,open,high,low,close,pre_close",
        [
            "600001.SH,20260825,10.00,11.00,9.80,10.50,9.50",
            "000002.SZ,20260825,11.00,11.00,11.00,11.00,10.00",
        ][:slot_count],
    )
    _write_csv(
        market / "stk_limit.csv",
        "ts_code,trade_date,up_limit,down_limit",
        [
            "600001.SH,20260825,10.45,8.55",
            "000002.SZ,20260825,11.00,9.00",
        ][:slot_count],
    )
    _write_csv(
        market / "stk_auction_o.csv",
        "ts_code,trade_date,close,amount,vol,vwap",
        [
            "600001.SH,20260825,10.00,20000000,1000000,10.00",
            "000002.SZ,20260825,11.00,20000000,1000000,11.00",
        ][:slot_count],
    )
    if include_t1:
        exit_market = tmp_path / "data/market/raw/2026/20260826"
        _write_csv(
            exit_market / "daily.csv",
            "ts_code,trade_date,open,high,low,close,pre_close",
            ["600001.SH,20260826,10.80,11.20,10.60,11.00,10.50"],
        )
        _write_csv(
            exit_market / "stk_limit.csv",
            "ts_code,trade_date,up_limit,down_limit",
            ["600001.SH,20260826,11.55,9.45"],
        )
    return tmp_path


def test_terminal_proxy_settlement_binds_frozen_selection_and_costs(tmp_path: Path) -> None:
    repo = _prepare_repo(tmp_path)
    result = settlement.settle_signal_date(
        repo, "20260824", as_of_date=AS_OF_T1
    )

    assert result["t_verification_status"] == "T_VERIFIED"
    assert result["t1_settlement_status"] == "FINAL_SETTLED"
    truth = json.loads((repo / result["t1_settlement_path"]).read_text(encoding="utf-8"))
    assert truth["selection"]["snapshot_sha256"] == "a" * 64
    assert truth["selection"]["selected_members"] == [
        {"shadow_slot": 1, "ts_code": "600001.SH"},
        {"shadow_slot": 2, "ts_code": "000002.SZ"},
    ]
    assert truth["rows"][0]["gross_return"] == pytest.approx(0.08)
    assert truth["rows"][0]["net_return_after_cost"] == pytest.approx(0.0755)
    assert truth["rows"][0]["stress_net_return"] == pytest.approx(0.071)
    assert truth["rows"][0]["scheduled_exit_date"] == "20260826"
    assert truth["rows"][0]["actual_exit_date"] == "20260826"
    assert truth["rows"][0]["delayed_trading_days"] == 0
    assert truth["rows"][0]["profit_after_cost"] == 1
    assert truth["rows"][1]["settlement_status"] == "FINAL_PROXY_NO_FILL"
    assert truth["rows"][1]["strategy_slot_return"] == 0.0
    assert truth["rows"][1]["net_return_after_cost"] is None
    assert all(row["actual_human_trade_return"] is None for row in truth["rows"])
    assert truth["boundaries"]["official_trade_action_allowed"] is False
    assert not list(repo.rglob(".settlement.lock"))


def test_statistics_are_deterministic_and_separate_top1_top2(tmp_path: Path) -> None:
    repo = _prepare_repo(tmp_path)
    settlement.settle_signal_date(repo, "20260824", as_of_date=AS_OF_T1)
    statistics_path = repo / settlement.STATISTICS_PATH
    first = statistics_path.read_bytes()
    rebuilt = settlement.build_statistics(repo, as_of_date=AS_OF_T1)
    settlement.materialize_statistics(repo, rebuilt)
    assert statistics_path.read_bytes() == first

    stats = json.loads(first)
    overall = stats["cohorts"]["all_selected_slots"]
    assert overall["selection_dates"] == 1
    assert overall["effective_dates"] == 1
    assert overall["selected_slots"] == 2
    assert overall["t_validated_slots"] == 2
    assert overall["proxy_fill_slots"] == 1
    assert overall["proxy_no_fill_slots"] == 1
    assert overall["proxy_buyable_rate"] == 0.5
    assert overall["t1_settled_slots"] == 1
    assert overall["pending_validation_slots"] == 0
    assert overall["pending_settlement_slots"] == 0
    assert overall["win_rate"] == 1.0
    assert overall["mean_net_return_after_cost"] == pytest.approx(0.0755)
    assert overall["median_net_return_after_cost"] == pytest.approx(0.0755)
    assert overall["realized_big_loss_rate_at_minus_3pct"] == 0.0
    assert overall["worst_10pct_mean_net_return"] == pytest.approx(0.0755)
    assert overall["mean_stress_net_return_90bp"] == pytest.approx(0.071)
    assert overall["equal_weight_cumulative_return"] == pytest.approx(0.03775)
    assert overall["maximum_drawdown"] == 0.0
    assert stats["cohorts"]["shadow_slot_1"]["t1_settled_slots"] == 1
    assert stats["cohorts"]["shadow_slot_2"]["proxy_no_fill_slots"] == 1
    assert stats["cohorts"]["stage_2_to_3"]["t1_settled_slots"] == 1
    assert stats["cohorts"]["stage_3_to_4"]["proxy_no_fill_slots"] == 1
    assert stats["probability_diagnostics"] == {
        "status": "UNCALIBRATED",
        "brier_score": None,
        "expected_calibration_error": None,
        "log_loss": None,
        "reason": "research_joint_proxy_score is not a calibrated probability",
    }
    assert "manual_actual_trade_ledger" in stats["excluded_ledgers"]
    assert stats["forward_signal_date_progress_180"] == {
        "observed_signal_dates": 1,
        "target_signal_dates": 180,
        "remaining_signal_dates": 179,
        "progress_pct": pytest.approx(0.555555555556),
        "release_sample_reached": False,
    }
    assert stats["scope"]["human_actual_trade_ledger_included"] is False


def test_missing_t1_truth_stays_pending_without_deleting_selection(tmp_path: Path) -> None:
    repo = _prepare_repo(tmp_path, include_t1=False)
    result = settlement.settle_signal_date(
        repo, "20260824", as_of_date=AS_OF_T1
    )
    assert result["t_verification_path"] is not None
    assert result["t1_settlement_path"] is None
    assert result["t1_settlement_status"] == "PENDING_EXIT_SOURCE_FILES:20260826:600001.SH"
    assert (repo / settlement.SELECTION_ROOT / "shadow_20260824.json").is_file()
    stats = json.loads((repo / settlement.STATISTICS_PATH).read_text(encoding="utf-8"))
    overall = stats["cohorts"]["all_selected_slots"]
    assert overall["t_validated_slots"] == 2
    assert overall["terminal_slots"] == 1
    assert overall["pending_settlement_slots"] == 1
    assert overall["effective_dates"] == 0
    assert stats["cohorts"]["shadow_slot_2"]["effective_dates"] == 1


def test_as_of_before_t_never_reads_already_present_future_market_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _prepare_repo(tmp_path)

    def forbidden_market_read(*args: object, **kwargs: object) -> None:
        raise AssertionError("future market source was read")

    monkeypatch.setattr(settlement, "_find_market_file", forbidden_market_read)
    result = settlement.settle_signal_date(
        repo,
        "20260824",
        as_of_date=AS_OF_D,
    )
    assert result["t_verification_status"] == "PENDING_T_NOT_REACHED"
    assert result["t_verification_path"] is None
    assert result["t1_settlement_status"] == "PENDING_T_NOT_REACHED"
    stats = json.loads((repo / settlement.STATISTICS_PATH).read_text(encoding="utf-8"))
    assert stats["as_of_date"] == AS_OF_D
    assert stats["cohorts"]["all_selected_slots"]["pending_validation_slots"] == 2


def test_one_price_limit_down_remains_pending(tmp_path: Path) -> None:
    repo = _prepare_repo(tmp_path)
    exit_market = repo / "data/market/raw/2026/20260826"
    _write_csv(
        exit_market / "daily.csv",
        "ts_code,trade_date,open,high,low,close,pre_close",
        ["600001.SH,20260826,9.45,9.45,9.45,9.45,10.50"],
    )
    verification, status = settlement.build_t_verification(
        repo, "20260824", as_of_date=AS_OF_T
    )
    assert status == "T_VERIFIED"
    assert verification is not None
    settlement.materialize_t_verification(repo, verification)
    payload, status = settlement.build_t1_settlement(
        repo, "20260824", as_of_date=AS_OF_T1
    )
    assert payload is None
    assert status == "PENDING_EXIT_AS_OF_CUTOFF:20260826:600001.SH"
    assert not (repo / settlement.SETTLEMENT_ROOT / "settlement_20260824.json").exists()


def test_one_tick_above_down_limit_is_not_treated_as_limit_down(
    tmp_path: Path,
) -> None:
    repo = _prepare_repo(tmp_path)
    exit_market = repo / "data/market/raw/2026/20260826"
    _write_csv(
        exit_market / "daily.csv",
        "ts_code,trade_date,open,high,low,close,pre_close",
        ["600001.SH,20260826,9.46,9.46,9.46,9.46,10.50"],
    )
    _write_csv(
        exit_market / "stk_limit.csv",
        "ts_code,trade_date,up_limit,down_limit",
        ["600001.SH,20260826,11.55,9.45"],
    )
    verification, verification_status = settlement.build_t_verification(
        repo, "20260824", as_of_date=AS_OF_T
    )
    assert verification_status == "T_VERIFIED"
    assert verification is not None
    settlement.materialize_t_verification(repo, verification)

    payload, status = settlement.build_t1_settlement(
        repo, "20260824", as_of_date=AS_OF_T1
    )
    assert status == "FINAL_SETTLED"
    assert payload is not None
    assert payload["rows"][0]["actual_exit_date"] == AS_OF_T1
    assert payload["rows"][0]["exit_open_price"] == 9.46
    assert payload["rows"][0]["blocked_exit_sessions"] == 0


def test_one_price_limit_down_walks_strict_sse_and_chains_adjusted_wealth(
    tmp_path: Path,
) -> None:
    repo = _prepare_repo(tmp_path)
    scheduled = repo / "data/market/raw/2026/20260826"
    _write_csv(
        scheduled / "daily.csv",
        "ts_code,trade_date,open,high,low,close,pre_close",
        ["600001.SH,20260826,9.45,9.45,9.45,9.45,10.50"],
    )
    delayed = repo / "data/market/raw/2026/20260827"
    _write_csv(
        delayed / "daily.csv",
        "ts_code,trade_date,open,high,low,close,pre_close",
        ["600001.SH,20260827,9.60,9.90,9.50,9.80,9.45"],
    )
    _write_csv(
        delayed / "stk_limit.csv",
        "ts_code,trade_date,up_limit,down_limit",
        ["600001.SH,20260827,10.40,8.51"],
    )
    verification, verification_status = settlement.build_t_verification(
        repo, "20260824", as_of_date=AS_OF_T
    )
    assert verification_status == "T_VERIFIED"
    assert verification is not None
    settlement.materialize_t_verification(repo, verification)
    premature, premature_status = settlement.build_t1_settlement(
        repo, "20260824", as_of_date=AS_OF_T1
    )
    assert premature is None
    assert premature_status == "PENDING_EXIT_AS_OF_CUTOFF:20260826:600001.SH"
    result = settlement.settle_signal_date(
        repo, "20260824", as_of_date=AS_OF_DELAYED
    )
    truth = json.loads((repo / result["t1_settlement_path"]).read_text(encoding="utf-8"))
    row = truth["rows"][0]
    assert row["scheduled_exit_date"] == "20260826"
    assert row["actual_exit_date"] == "20260827"
    assert row["delayed_trading_days"] == 1
    assert row["blocked_exit_sessions"] == 1
    assert row["exit_reason"] == "DELAYED_FIRST_TRADABLE_OPEN_AFTER_ONE_PRICE_LIMIT_DOWN"
    # T close/entry * blocked close/pre_close * exit open/pre_close = 0.96.
    assert row["gross_return"] == pytest.approx(-0.04)
    assert row["net_return_after_cost"] == pytest.approx(-0.0445)
    assert row["stress_net_return"] == pytest.approx(-0.049)
    assert {item["path"] for item in truth["source_files"]} == {
        "data/market/raw/2026/20260826/daily.csv",
        "data/market/raw/2026/20260826/stk_limit.csv",
        "data/market/raw/2026/20260827/daily.csv",
        "data/market/raw/2026/20260827/stk_limit.csv",
    }
    stats = json.loads((repo / settlement.STATISTICS_PATH).read_text(encoding="utf-8"))
    overall = stats["cohorts"]["all_selected_slots"]
    assert overall["historically_blocked_exit_slots"] == 1
    assert overall["historically_blocked_exit_sessions"] == 1
    assert overall["blocked_exit_slots"] == 1
    assert overall["blocked_exit_sessions"] == 1
    assert overall["delayed_exit_slots"] == 1
    assert overall["realized_big_loss_rate_at_minus_3pct"] == 1.0
    assert overall["worst_10pct_mean_net_return"] == pytest.approx(-0.0445)
    historical = settlement.build_statistics(repo, as_of_date=AS_OF_T1)
    assert historical["as_of_date"] == AS_OF_T1
    assert historical["cohorts"]["all_selected_slots"]["t1_settled_slots"] == 0
    assert historical["cohorts"]["all_selected_slots"]["pending_settlement_slots"] == 1
    with pytest.raises(
        settlement.ExecutableProfitSettlementError,
        match="cannot move backward",
    ):
        settlement.materialize_statistics(repo, historical)


def test_immutable_truth_rejects_same_date_rewrite_and_stale_selection(tmp_path: Path) -> None:
    repo = _prepare_repo(tmp_path)
    verification, _ = settlement.build_t_verification(
        repo, "20260824", as_of_date=AS_OF_T
    )
    assert verification is not None
    settlement.materialize_t_verification(repo, verification)
    changed = copy.deepcopy(verification)
    changed["rows"][0]["entry_open_price"] = 10.01
    changed["snapshot_sha256"] = settlement._payload_snapshot(changed)
    with pytest.raises(settlement.ExecutableProfitSettlementError, match="cannot be rewritten"):
        settlement.materialize_t_verification(repo, changed)

    selection_path = repo / settlement.SELECTION_ROOT / "shadow_20260824.json"
    selection_payload = json.loads(selection_path.read_text(encoding="utf-8"))
    selection_payload["rows"][0]["name"] = "事后篡改"
    _write_json(selection_path, selection_payload)
    with pytest.raises(settlement.ExecutableProfitSettlementError, match="stale selection binding"):
        settlement.build_statistics(repo, as_of_date=AS_OF_T1)


@pytest.mark.parametrize(
    ("field", "forged_value"),
    (("entry_open_price", 1.0), ("t_close_price", 99.0)),
)
def test_supplied_verification_cannot_override_existing_immutable_prices(
    tmp_path: Path,
    field: str,
    forged_value: float,
) -> None:
    repo = _prepare_repo(tmp_path)
    verification, status = settlement.build_t_verification(
        repo, "20260824", as_of_date=AS_OF_T
    )
    assert status == "T_VERIFIED"
    assert verification is not None
    verification_path = settlement.materialize_t_verification(repo, verification)
    immutable_bytes = verification_path.read_bytes()

    forged = copy.deepcopy(verification)
    forged["rows"][0][field] = forged_value
    forged["snapshot_sha256"] = settlement._payload_snapshot(forged)
    # This reproduces the P1: the forged object is internally well-formed, but
    # it is not the immutable verification already written for this D.
    settlement.validate_t_verification(forged)
    with pytest.raises(
        settlement.ExecutableProfitSettlementError,
        match="supplied T verification differs from immutable file",
    ):
        settlement.build_t1_settlement(
            repo,
            "20260824",
            verification=forged,
            as_of_date=AS_OF_T1,
        )
    assert verification_path.read_bytes() == immutable_bytes
    assert not (
        repo / settlement.SETTLEMENT_ROOT / "settlement_20260824.json"
    ).exists()


def test_existing_verification_must_retain_canonical_immutable_bytes(
    tmp_path: Path,
) -> None:
    repo = _prepare_repo(tmp_path)
    verification, status = settlement.build_t_verification(
        repo, "20260824", as_of_date=AS_OF_T
    )
    assert status == "T_VERIFIED"
    assert verification is not None
    verification_path = settlement.materialize_t_verification(repo, verification)
    verification_path.write_text(
        json.dumps(verification, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        settlement.ExecutableProfitSettlementError,
        match="immutable T verification bytes are not canonical",
    ):
        settlement.build_t1_settlement(
            repo,
            "20260824",
            verification=verification,
            as_of_date=AS_OF_T1,
        )
    assert not (
        repo / settlement.SETTLEMENT_ROOT / "settlement_20260824.json"
    ).exists()


def test_single_candidate_records_top1_without_forcing_top2(tmp_path: Path) -> None:
    repo = _prepare_repo(tmp_path, slot_count=1)
    result = settlement.settle_signal_date(
        repo, "20260824", as_of_date=AS_OF_T1
    )
    assert result["t1_settlement_path"] is not None
    stats = json.loads((repo / settlement.STATISTICS_PATH).read_text(encoding="utf-8"))
    assert stats["cohorts"]["all_selected_slots"]["selected_slots"] == 1
    assert stats["cohorts"]["shadow_slot_1"]["selected_slots"] == 1
    assert stats["cohorts"]["shadow_slot_2"]["selected_slots"] == 0


def test_zero_candidate_day_is_counted_without_market_truth_or_padding(
    tmp_path: Path,
) -> None:
    repo = _prepare_repo(tmp_path, slot_count=0, include_t1=False)
    result = settlement.settle_signal_date(
        repo, "20260824", as_of_date=AS_OF_D
    )
    assert result["t_verification_path"] is not None
    assert result["t1_settlement_path"] is not None
    stats = json.loads((repo / settlement.STATISTICS_PATH).read_text(encoding="utf-8"))
    assert stats["scope"]["selection_dates"] == 1
    assert stats["scope"]["no_selected_dates"] == 1
    assert stats["cohorts"]["all_selected_slots"]["selected_slots"] == 0
    assert stats["forward_signal_date_progress_180"]["observed_signal_dates"] == 1


def test_repository_settlement_contract_has_unique_exact_cohorts(tmp_path: Path) -> None:
    repo = _prepare_repo(tmp_path, slot_count=0, include_t1=False)
    contract = settlement._load_contract(repo)
    cohorts = contract["statistics"]["cohorts"]
    assert cohorts == [
        "all_selected_slots",
        "shadow_slot_1",
        "shadow_slot_2",
        "stage_2_to_3",
        "stage_3_to_4",
    ]
    assert len(cohorts) == len(set(cohorts))


def test_non_adjacent_selection_dates_fail_strict_sse_calendar(tmp_path: Path) -> None:
    repo = _prepare_repo(tmp_path)
    path = repo / settlement.SELECTION_ROOT / "shadow_20260824.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["exit_date"] = "20260827"
    _write_json(path, payload)
    with pytest.raises(settlement.ExecutableProfitSettlementError, match="adjacent strict SSE"):
        settlement.build_t_verification(
            repo, "20260824", as_of_date=AS_OF_DELAYED
        )


def test_missing_frozen_cap_or_auction_truth_is_pending_not_no_fill(tmp_path: Path) -> None:
    repo = _prepare_repo(tmp_path)
    selection_path = repo / settlement.SELECTION_ROOT / "shadow_20260824.json"
    payload = json.loads(selection_path.read_text(encoding="utf-8"))
    payload["rows"][0]["shadow_max_price"] = None
    _write_json(selection_path, payload)
    verification, status = settlement.build_t_verification(
        repo, "20260824", as_of_date=AS_OF_T
    )
    assert verification is None
    assert status == "PENDING_T_FROZEN_ENTRY_CAP:600001.SH"

    repo = _prepare_repo(tmp_path / "second")
    (repo / "data/market/raw/2026/20260825/stk_auction_o.csv").unlink()
    verification, status = settlement.build_t_verification(
        repo, "20260824", as_of_date=AS_OF_T
    )
    assert verification is None
    assert status == "PENDING_T_SOURCE_FILES"


@pytest.mark.parametrize("file_name", ("daily.csv", "stk_limit.csv", "stk_auction_o.csv"))
@pytest.mark.parametrize("bad_date", ("", "2026-08-25"))
def test_every_t_truth_row_requires_exact_nonempty_trade_date(
    tmp_path: Path,
    file_name: str,
    bad_date: str,
) -> None:
    repo = _prepare_repo(tmp_path)
    path = repo / "data/market/raw/2026/20260825" / file_name
    source = path.read_text(encoding="utf-8")
    changed = source.replace(",20260825,", f",{bad_date},", 1)
    assert changed != source
    path.write_text(changed, encoding="utf-8")

    with pytest.raises(
        settlement.ExecutableProfitSettlementError,
        match="market row trade_date must exactly equal 20260825",
    ):
        settlement.build_t_verification(
            repo, "20260824", as_of_date=AS_OF_T
        )


@pytest.mark.parametrize("file_name", ("daily.csv", "stk_limit.csv"))
def test_every_t1_truth_row_requires_nonempty_trade_date(
    tmp_path: Path,
    file_name: str,
) -> None:
    repo = _prepare_repo(tmp_path)
    verification, status = settlement.build_t_verification(
        repo, "20260824", as_of_date=AS_OF_T
    )
    assert status == "T_VERIFIED"
    assert verification is not None
    settlement.materialize_t_verification(repo, verification)
    path = repo / "data/market/raw/2026/20260826" / file_name
    source = path.read_text(encoding="utf-8")
    changed = source.replace(",20260826,", ",,", 1)
    assert changed != source
    path.write_text(changed, encoding="utf-8")

    with pytest.raises(
        settlement.ExecutableProfitSettlementError,
        match="market row trade_date must exactly equal 20260826",
    ):
        settlement.build_t1_settlement(
            repo, "20260824", as_of_date=AS_OF_T1
        )


def test_price_equality_uses_half_up_one_cent_ticks() -> None:
    assert settlement._same_rounded_price(9.45, 9.454)
    assert not settlement._same_rounded_price(9.45, 9.455)
    assert not settlement._same_rounded_price(9.45, 9.46)
    assert not settlement._all_at_limit([9.46, 9.46, 9.46, 9.46], 9.45)


def test_auction_and_daily_open_one_tick_apart_are_in_conflict(
    tmp_path: Path,
) -> None:
    repo = _prepare_repo(tmp_path)
    _write_csv(
        repo / "data/market/raw/2026/20260825/stk_auction_o.csv",
        "ts_code,trade_date,close,amount,vol,vwap",
        [
            "600001.SH,20260825,10.01,20000000,1000000,10.01",
            "000002.SZ,20260825,11.00,20000000,1000000,11.00",
        ],
    )
    verification, status = settlement.build_t_verification(
        repo, "20260824", as_of_date=AS_OF_T
    )
    assert status == "T_VERIFIED"
    assert verification is not None
    row = verification["rows"][0]
    assert row["entry_open_price"] == 10.01
    assert row["daily_open_price"] == 10.0
    assert row["auction_daily_open_conflict"] is True
    assert row["proxy_fill"] == 0
    assert (
        row["validation_status"]
        == "T_VERIFIED_PROXY_NO_FILL_AUCTION_DAILY_CONFLICT"
    )


def test_frozen_cap_and_capacity_are_observed_no_fill_not_future_rerank(tmp_path: Path) -> None:
    repo = _prepare_repo(tmp_path)
    selection_path = repo / settlement.SELECTION_ROOT / "shadow_20260824.json"
    payload = json.loads(selection_path.read_text(encoding="utf-8"))
    payload["rows"][0]["shadow_max_price"] = 9.99
    _write_json(selection_path, payload)
    _write_csv(
        repo / "data/market/raw/2026/20260825/daily.csv",
        "ts_code,trade_date,open,high,low,close,pre_close",
        [
            "600001.SH,20260825,10.00,11.00,9.80,10.50,9.50",
            "000002.SZ,20260825,10.90,11.00,10.80,10.95,10.00",
        ],
    )
    auction = repo / "data/market/raw/2026/20260825/stk_auction_o.csv"
    _write_csv(
        auction,
        "ts_code,trade_date,close,amount,vol,vwap",
        [
            "600001.SH,20260825,10.00,20000000,1000000,10.00",
            "000002.SZ,20260825,10.90,5000000,1000000,10.90",
        ],
    )
    verification, status = settlement.build_t_verification(
        repo, "20260824", as_of_date=AS_OF_T
    )
    assert status == "T_VERIFIED"
    assert verification is not None
    assert [(row["shadow_slot"], row["proxy_fill"]) for row in verification["rows"]] == [
        (1, 0),
        (2, 0),
    ]
    assert verification["rows"][0]["validation_status"] == "T_VERIFIED_PROXY_NO_FILL_ABOVE_FROZEN_CAP"
    assert verification["rows"][1]["validation_status"] == "T_VERIFIED_PROXY_NO_FILL_CAPACITY"
    # Observed no-fill never causes a replacement from outside the frozen members.
    assert verification["selection"]["selected_members"] == [
        {"shadow_slot": 1, "ts_code": "600001.SH"},
        {"shadow_slot": 2, "ts_code": "000002.SZ"},
    ]


def test_load_selection_integrates_with_real_main_selection_validator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _prepare_repo(tmp_path, slot_count=1)
    payload = _real_validated_single_selection()
    _write_json(
        repo / settlement.SELECTION_ROOT / "shadow_20260824.json",
        payload,
    )
    # Override the unit-test stub and exercise the production validator used by
    # the main scorer/immutable selection materializer.
    monkeypatch.setattr(
        settlement,
        "validate_internal_forward_shadow_payload",
        shadow.validate_internal_forward_shadow_payload,
    )
    path, loaded, selected = settlement.load_selection(repo, "20260824")
    assert path.name == "shadow_20260824.json"
    assert loaded["snapshot_sha256"] == payload["snapshot_sha256"]
    assert [(row["shadow_slot"], row["ts_code"]) for row in selected] == [
        (1, "600001.SH")
    ]
