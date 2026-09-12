from __future__ import annotations

import copy
from pathlib import Path

import pytest

from work.profit_1000_upgrade.capital import (
    COST_RATE, EXIT_POLICY_ID, SETTLED, replay_capital, replay_capital_from_repository,
)


DATES = ["20260901", "20260902", "20260903", "20260904", "20260907", "20260908", "20260909", "20260910", "20260911", "20260914", "20260915", "20260916", "20260917"]
D, T, T1 = "20260910", "20260911", "20260914"
CODE = "600000.SH"
POLICY = "research_auction_or_open_no_cap_v1"


def row(day=D, code=CODE, rank=1):
    return {"signal_date": day, "ts_code": code, "candidate_rank": rank, "candidate_score": -.5}


def label(day=D, code=CODE, *, price=10, net=-.0245, exit_day=T1, status=SETTLED):
    index = DATES.index(day)
    t, t1 = DATES[index + 1:index + 3]
    timestamp = exit_day[:4] + "-" + exit_day[4:6] + "-" + exit_day[6:] + "T10:00:00+08:00"
    return {"signal_date": day, "ts_code": code, "exec_date": t, "scheduled_exit_date": t1,
            "label_policy_id": EXIT_POLICY_ID, "entry_policy_id": POLICY, "round_trip_cost_rate": COST_RATE,
            "entry_price": price, "label_status": status, "proxy_fill": 1,
            "slot_net_return": net if status == SETTLED else None, "conditional_net_return": net if status == SETTLED else None,
            "actual_exit_date": exit_day if status == SETTLED else None,
            "actual_exit_time": timestamp if status == SETTLED else None,
            "exit_evidence": {"exit_policy_id": EXIT_POLICY_ID, "gross_return": net + COST_RATE,
                              "actual_exit_date": exit_day, "actual_exit_time": timestamp}}


def mark(day, code, *, close=10, pre=10, corroboration=None):
    result = {"ts_code": code, "trade_date": day, "close": close, "pre_close": pre,
              "source_files": [{"path": f"data/market/raw/2026/{day}/{code}/daily.csv", "sha256": "a" * 64}]}
    if corroboration is not None:
        result["limits_pre_close"] = corroboration
    return result


def replay(rows=None, labels=None, *, as_of=T1, loader=mark):
    return replay_capital(rows if rows is not None else [row()], labels if labels is not None else [label()],
                          open_dates=DATES, as_of_date=as_of, load_daily=loader)


def test_negative_score_trades_and_net_cost_charged_once():
    out = replay()
    account = out["accounts"]["top1"]
    record = account["records"][0]
    assert record["status"] == "SETTLED" and record["shares"] == 10000
    assert record["invested_notional"] == 100000 and record["net_profit"] == -2450
    assert record["charged_round_trip_cost"] == 450 and account["reserved_exit_cost"] == 0
    assert account["cash_balance"] == account["equity"] == 997550
    assert account["maximum_drawdown"] == pytest.approx(-.00245)
    assert account["equity_curve"][0]["equity"] == 999550  # Future fee reserved, not charged twice.
    assert out["research_only"] and not out["production_activation_allowed"]
    assert out["account_policy"]["compound_slot_returns_used_as_nav"] is False
    assert out["label_verification"] == "CALLER_VERIFIED_LABELS_REQUIRED_NOT_INDEPENDENT_SOURCE_REPLAY"


def test_two_ranks_have_separate_million_accounts_not_shared_cash_or_pnl():
    out = replay([row(), row(code="600001.SH", rank=2)], [label(), label(code="600001.SH", net=.0155)])
    first, second = out["accounts"]["top1"], out["accounts"]["top2"]
    assert first["equity"] == 997550 and second["equity"] == 1001550
    assert len(first["records"]) == len(second["records"]) == 1
    assert first["initial_cash"] == second["initial_cash"] == 1000000


def test_round_lot_floor_actual_notional_and_actual_cost():
    account = replay(labels=[label(price=33)], loader=lambda d, c: mark(d, c, close=33, pre=33))["accounts"]["top1"]
    record = account["records"][0]
    assert record["shares"] == 3000 and record["invested_notional"] == 99000
    assert record["charged_round_trip_cost"] == 445.5
    assert account["cash_balance"] == 997574.5


def test_one_lot_above_budget_does_not_invent_fractional_shares():
    account = replay(labels=[label(price=1001)])["accounts"]["top1"]
    assert account["records"][0]["status"] == "BUDGET_BELOW_ONE_LOT"
    assert account["records"][0]["shares"] == 0 and account["cash_balance"] == 1000000


def test_925_cannot_reuse_same_day_1000_exit_proceeds():
    selected, labels = [], []
    for index, d in enumerate(DATES[:10]):
        code = f"600{index:03d}.SH"
        selected.append(row(d, code))
        labels.append(label(d, code, exit_day="20260915", status=SETTLED if index == 0 else "PENDING_EXIT_LIMIT_UP_HELD"))
    account = replay(selected, labels, as_of="20260915")["accounts"]["top1"]
    assert len(account["records"]) == 10  # No disappearing daily Top1 slot.
    assert account["records"][-1]["status"] == "CAPITAL_UNAVAILABLE"
    assert account["records"][-1]["reason"] == "INSUFFICIENT_CASH_BEFORE_AUCTION"
    assert account["records"][0]["status"] == "SETTLED"
    assert account["capital_unavailable_slots"] == 1 and len(account["open_positions"]) == 8
    assert account["cash_balance"] == 197550
    assert account["available_cash"] == 193950
    assert account["equity"] == 993950


def test_unresolved_entry_reserves_budget_without_inventing_fill_or_zero_nav():
    out = replay(labels=[])
    account = out["accounts"]["top1"]
    assert account["records"][0]["status"] == "PENDING_ENTRY_TRUTH"
    assert account["records"][0]["net_profit"] is None
    assert account["cash_balance"] == 1000000 and account["available_cash"] == 899550
    assert account["reserved_entry_cash"] == 100450
    assert account["equity"] is None and account["maximum_drawdown"] is None


def test_pending_exit_position_is_marked_and_retained_not_zero_return():
    account = replay(labels=[label(status="PENDING_EXIT_LIMIT_UP_HELD")],
                     loader=lambda d, c: mark(d, c, close=10 if d == T else 10.5))["accounts"]["top1"]
    assert account["records"][0]["status"] == "OPEN_POSITION"
    assert account["records"][0]["net_profit"] is None
    assert account["cash_balance"] == 900000 and account["realized_net_profit"] == 0
    assert account["equity"] == 1004550 and account["invested_cost_basis"] == 100000
    assert account["open_positions"][0]["market_value"] == 105000


def test_known_no_fill_keeps_slot_zero_cash_unchanged():
    value = label(status="NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED")
    value.update(proxy_fill=0, slot_net_return=0, conditional_net_return=None)
    account = replay(labels=[value])["accounts"]["top1"]
    assert account["records"][0]["status"] == "NO_FILL"
    assert account["records"][0]["reason"] == "NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED"
    assert account["cash_balance"] == account["equity"] == 1000000
    assert account["records"][0]["charged_round_trip_cost"] == 0


def test_missing_mark_does_not_assume_zero_or_bridge_an_unknown_price_chain():
    account = replay(labels=[label(status="PENDING_EXIT_MISSING_MINUTES")], loader=lambda d, c: None if d == T else mark(d, c))["accounts"]["top1"]
    assert account["equity"] is None and account["maximum_drawdown"] is None
    assert account["cash_balance"] == 900000 and len(account["open_positions"]) == 1
    assert account["equity_curve"][-1]["missing_marks"][0]["reason"] == "EARLIER_MARK_CHAIN_MISSING"


def test_later_verified_exit_restores_known_cash_but_not_unknown_historical_drawdown():
    account = replay(loader=lambda d, c: None)["accounts"]["top1"]
    assert account["cash_balance"] == account["equity"] == 997550
    assert not account["nav_history_complete"] and account["maximum_drawdown"] is None


@pytest.mark.parametrize("kind", ["identity", "date", "sha", "missing", "nan"])
def test_invalid_mark_is_unknown_nav_and_keeps_position(kind):
    def broken(d, c):
        value = mark(d, c)
        if kind == "identity": value["ts_code"] = "600999.SH"
        if kind == "date": value["trade_date"] = T1
        if kind == "sha": value["source_files"][0]["sha256"] = "bad"
        if kind == "missing": value.pop("source_files")
        if kind == "nan": value["close"] = float("nan")
        return value
    account = replay(labels=[label(status="PENDING_EXIT_MISSING_MINUTES")], loader=broken)["accounts"]["top1"]
    assert account["equity"] is None and len(account["open_positions"]) == 1


def test_corporate_action_mark_uses_corroborated_close_preclose_chain():
    def adjusted(d, c):
        return mark(d, c) if d == T else mark(d, c, close=5.1, pre=5, corroboration=5)
    account = replay(labels=[label(status="PENDING_EXIT_LIMIT_UP_HELD")], loader=adjusted)["accounts"]["top1"]
    assert account["equity"] == 1001550  # No fake 49% loss from halved price basis.


def test_uncorroborated_adjusted_mark_is_unavailable():
    def adjusted(d, c):
        return mark(d, c) if d == T else mark(d, c, close=5.1, pre=5)
    account = replay(labels=[label(status="PENDING_EXIT_LIMIT_UP_HELD")], loader=adjusted)["accounts"]["top1"]
    assert account["equity"] is None


def test_future_entry_and_future_exit_are_not_pulled_back_into_snapshot():
    calls = []
    def loader(d, c):
        calls.append(d)
        return mark(d, c)
    future_entry = replay(as_of=D, loader=loader)["accounts"]["top1"]
    assert future_entry["records"][0]["status"] == "WAITING_ENTRY" and not calls
    future_exit = replay(as_of=T, loader=loader)["accounts"]["top1"]
    assert future_exit["records"][0]["status"] == "OPEN_POSITION"
    assert future_exit["cash_balance"] == 900000 and future_exit["realized_net_profit"] == 0
    assert calls == [T]


@pytest.mark.parametrize("change", ["entry_policy", "exit_policy", "cost", "t0_exit", "net_mismatch", "gross_mismatch", "wrong_date"])
def test_policy_and_terminal_truth_mismatch_fail_closed(change):
    value = label()
    if change == "entry_policy": value["entry_policy_id"] = "research_auction_or_open_frozen_cap_v1"
    if change == "exit_policy": value["label_policy_id"] = "old_open_exit"
    if change == "cost": value["round_trip_cost_rate"] = 0
    if change == "t0_exit":
        value.update(actual_exit_date=T, actual_exit_time="2026-09-11T10:00:00+08:00")
    if change == "net_mismatch": value["conditional_net_return"] = .99
    if change == "gross_mismatch": value["exit_evidence"]["gross_return"] = .99
    if change == "wrong_date": value["exec_date"] = T1
    with pytest.raises(ValueError):
        replay(labels=[value])


def test_duplicate_or_incomplete_frozen_ranking_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        replay(rows=[row(), row()])
    with pytest.raises(ValueError, match="missing ranks"):
        replay(rows=[row(rank=2)])
    with pytest.raises(ValueError, match="duplicate label"):
        replay(labels=[label(), label()])


def test_one_candidate_day_keeps_top2_explicitly_absent_not_invented():
    second = replay()["accounts"]["top2"]
    assert second["records"] == [] and second["no_frozen_candidate_dates"] == [D]
    assert second["cash_balance"] == 1000000


@pytest.fixture
def repository_case(tmp_path):
    from work.profit_1000_upgrade.test_labels import daily, binding, minutes
    from work.profit_1000_upgrade.labels import SCHEMA, build_labels
    import json
    import shutil
    root = Path(__file__).resolve().parents[2]
    calendar = tmp_path / "data/market/trade_cal_sse.csv"
    calendar.parent.mkdir(parents=True)
    shutil.copyfile(root / "data/market/trade_cal_sse.csv", calendar)
    daily(tmp_path, T)
    daily(tmp_path, T1, price=9.8)
    minutes(tmp_path)
    source = tmp_path / "features.json"
    source.write_text('{"evidence":"bound retrospective D-only original feature source"}')
    manifest = {"schema_version": SCHEMA, "evidence_kind": "RETROSPECTIVE_D_ONLY_RECONSTRUCTION",
                "feature_columns": ["path_change"], "source_bindings": [binding(tmp_path, source)],
                "expected_candidate_codes": {D: [CODE]},
                "rows": [{"signal_date": D, "ts_code": CODE, "stage": 2, "promotion_rank": 1,
                          "feature_as_of_date": D, "feature_available_at": "2026-09-10T23:59:00+08:00",
                          "features": {"path_change": -.1}}]}
    rebuilt = build_labels(tmp_path, manifest, as_of_date=T1)
    return tmp_path, manifest, rebuilt["rows"]


def test_repository_adapter_does_not_write_or_consume_legacy_labels(repository_case):
    tmp_path, manifest, labels = repository_case
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    out = replay_capital_from_repository(tmp_path, [row()], labels, candidate_manifest=manifest, as_of_date=T1)
    assert out["accounts"]["top1"]["cash_balance"] == 997550
    assert len(out["source_files"]) == 8
    assert out["label_verification"] == "REBUILT_FROM_BOUND_REPOSITORY_PRICE_SOURCES"
    assert out["external_labels_exactly_matched"] is True
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before


@pytest.mark.parametrize("forgery", ["entry_price", "consistent_profit", "missing_exit_evidence"])
def test_repository_adapter_rejects_handwritten_settlement_even_if_arithmetic_matches(repository_case, forgery):
    root, manifest, labels = repository_case
    forged = copy.deepcopy(labels)
    if forgery == "entry_price":
        forged[0]["entry_price"] = 1
    elif forgery == "consistent_profit":
        forged[0].update(net_return=.9955, conditional_net_return=.9955, slot_net_return=.9955)
        forged[0]["exit_evidence"]["gross_return"] = 1.0
    else:
        forged[0].pop("exit_evidence")
    with pytest.raises(ValueError, match="external labels do not exactly match"):
        replay_capital_from_repository(root, [row()], forged, candidate_manifest=manifest, as_of_date=T1)


def test_repository_adapter_rebuilds_without_external_labels(repository_case):
    root, manifest, _ = repository_case
    out = replay_capital_from_repository(root, [row()], candidate_manifest=manifest, as_of_date=T1)
    assert out["accounts"]["top1"]["cash_balance"] == 997550
    assert out["external_labels_exactly_matched"] is False


def test_repository_adapter_rejects_foreign_candidate_and_missing_manifest(repository_case):
    root, manifest, labels = repository_case
    with pytest.raises(TypeError):
        replay_capital_from_repository(root, [row()], labels, as_of_date=T1)
    with pytest.raises(ValueError, match="outside bound label universe"):
        replay_capital_from_repository(root, [row(code="600999.SH")], candidate_manifest=manifest, as_of_date=T1)


def test_repository_adapter_cannot_settle_from_old_external_record_after_minutes_disappear(repository_case):
    from top10decision.decision.shadow_exit_minute_truth import minute_paths
    root, manifest, labels = repository_case
    for source in minute_paths(root, T1, CODE):
        source.unlink()
    with pytest.raises(ValueError, match="external labels do not exactly match"):
        replay_capital_from_repository(root, [row()], labels, candidate_manifest=manifest, as_of_date=T1)
    fresh = replay_capital_from_repository(root, [row()], candidate_manifest=manifest, as_of_date=T1)
    account = fresh["accounts"]["top1"]
    assert account["cash_balance"] == 900000 and account["realized_net_profit"] == 0
    assert account["records"][0]["status"] == "OPEN_POSITION"
