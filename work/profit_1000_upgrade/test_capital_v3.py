"""Offline v3-contract fixtures and frozen-economics comparisons, not fill proof."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import inspect
import json
from pathlib import Path
import socket

import pytest

from work.profit_1000_upgrade import capital as legacy, capital_v3 as capital
from work.profit_1000_upgrade import auction_candidate_scope as source
from work.profit_1000_upgrade import auction_truth_v3 as qualification
from work.profit_1000_upgrade import candidate_labels_overlay as overlay, policy_v2, policy_v3
from work.profit_1000_upgrade.test_capital import mark, row
from work.profit_1000_upgrade.test_labels import case
from work.profit_1000_upgrade.test_labels_v3 import v3_case, result as base_label_report
from work.profit_1000_upgrade.test_candidate_labels_overlay import overlay_case, report as overlay_label_report

DATES = ["20260831", "20260901", "20260902", "20260903", "20260904", "20260907", "20260908",
         "20260909", "20260910", "20260911", "20260914", "20260915", "20260916", "20260917"]
D, T, T1, CODE = "20260910", "20260911", "20260914", "600000.SH"
SOURCE_FIELDS = ("auction_source_policy_id", "minute_source_policy_id", "minute_time_semantics",
                 "source_overlay_policy_id", "auction_qualification_policy_id")


def at(day, time):
    return datetime.strptime(day, "%Y%m%d").strftime("%Y-%m-%dT" + time + "+08:00")


def label(day=D, code=CODE, *, origin="base", price=10, net=-.0245, exit_day=T1,
          status=legacy.SETTLED):
    """Synthetic, fully qualified contract only; never a verified source receipt."""
    index = DATES.index(day)
    t, t1 = DATES[index + 1:index + 3]
    raw = {"ts_code": code, "trade_date": t, "price": price, "vol": 2_000_000,
           "amount": price * 2_000_000, "pre_close": price}
    evidence = qualification.qualify_row(raw, t, code, price)
    evidence.update(source_policy_id=policy_v3.AUCTION_SOURCE_POLICY_ID,
                    entry_price_source="TUSHARE_STK_AUCTION", loaded_source_qualification_claimed=True)
    if origin == "candidate":
        evidence.update(source_policy_id=source.SOURCE_POLICY_ID,
            qualification_policy_id=qualification.SOURCE_POLICY_ID, overlay_policy_id=overlay.OVERLAY_POLICY_ID,
            source_origin="candidate", daily_source_origin="base", source_only_metadata_rewritten=False,
            trade_date=t, ts_code=code, daily_open=price,
            source_files=[{"path": "research/synthetic.data.json", "sha256": "a" * 64},
                          {"path": "research/synthetic.meta.json", "sha256": "b" * 64}],
            daily_source_binding={"path": "data/synthetic_daily.csv", "sha256": "c" * 64})
    value = {**dict(policy_v3.CONTRACT), **qualification.FLAGS,
        "signal_date": day, "ts_code": code, "exec_date": t, "scheduled_exit_date": t1,
        "label_policy_id": legacy.EXIT_POLICY_ID, "round_trip_cost_rate": .0045,
        "shadow_max_price": None, "entry_price": price, "entry_price_source": "TUSHARE_STK_AUCTION",
        "entry_price_evidence": evidence, "entry_qualification_status": evidence["status"],
        "label_status": status, "proxy_fill": 1, "auction_request_receipt_observed": True,
        "minute_source_observed": status == legacy.SETTLED,
        "minute_source_observed_dates": [exit_day] if status == legacy.SETTLED else [],
        "net_return": net if status == legacy.SETTLED else None,
        "slot_net_return": net if status == legacy.SETTLED else None,
        "conditional_net_return": net if status == legacy.SETTLED else None,
        "actual_exit_date": exit_day if status == legacy.SETTLED else None,
        "actual_exit_time": at(exit_day, "10:00:00") if status == legacy.SETTLED else None,
        "label_available_date": exit_day if status == legacy.SETTLED else None,
        "label_available_at": at(exit_day, "15:00:00") if status == legacy.SETTLED else None,
        "label_maturity_at": at(exit_day, "10:00:00") if status == legacy.SETTLED else None,
        "exit_evidence": {"exit_policy_id": legacy.EXIT_POLICY_ID, "gross_return": net + .0045,
                          "actual_exit_date": exit_day, "actual_exit_time": at(exit_day, "10:00:00")}}
    for key in ("capacity_proxy_verified", "capacity_amount", "capacity_evidence", "capacity_reason",
                "price_qualified", "auction_trade_observed"):
        value[key] = evidence[key]
    if origin == "candidate":
        value.update(auction_source_policy_id=source.SOURCE_POLICY_ID,
                     source_overlay_policy_id=overlay.OVERLAY_POLICY_ID,
                     auction_qualification_policy_id=qualification.SOURCE_POLICY_ID)
    if status == "NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED":
        value.update(proxy_fill=0, slot_net_return=0., conditional_net_return=None, net_return=None,
                     label_available_date=t, label_available_at=at(t, "15:00:00"),
                     label_maturity_at=at(t, "09:25:00"), t_opening_limit_up_observed=True)
    overlay.validate_label_contract(value)
    return value


def replay(rows=None, labels=None, *, as_of=T1, loader=mark, **kwargs):
    return capital.replay_capital_v3([row()] if rows is None else rows, [label()] if labels is None else labels,
        open_dates=DATES, as_of_date=as_of, load_daily=loader,
        source_overlay_contract=deepcopy(overlay.CONTRACT), **kwargs)


def legacy_label(value):
    # Test-only economic projection for the independent old-kernel oracle.
    # The implementation must never do this or feed a projected row to v2.
    keys = {"signal_date", "ts_code", "exec_date", "scheduled_exit_date", "label_policy_id",
            "round_trip_cost_rate", "entry_price", "label_status", "proxy_fill", "slot_net_return",
            "conditional_net_return", "actual_exit_date", "actual_exit_time", "exit_evidence"}
    return {**{key: deepcopy(value[key]) for key in keys},
            "entry_policy_id": "research_auction_or_open_no_cap_v1"}


def assert_economics_equal(actual, expected):
    accounts = deepcopy(actual["accounts"])
    for account in accounts.values():
        for record in account["records"]:
            for key in SOURCE_FIELDS:
                record.pop(key, None)
            record["entry_policy_id"] = "research_auction_or_open_no_cap_v1"
    assert accounts == expected["accounts"]
    assert actual["account_policy"] == expected["account_policy"]
    assert actual["source_files"] == expected["source_files"]


@pytest.mark.parametrize("origin", ["base", "candidate"])
@pytest.mark.parametrize("scenario", ["loss", "gain", "round_lot", "unaffordable_lot", "missing_entry",
    "pending_entry", "held", "known_no_fill", "missing_first_mark", "late_verified_exit", "adjusted_mark",
    "unverified_adjustment", "future_entry", "future_exit", "overlap", "unknown_entry_overlaps", "two_ranks"])
def test_original_economic_cases_remain_exactly_equal(origin, scenario):
    rows, labels, asof, loader = [row()], [label(origin=origin)], T1, mark
    if scenario == "gain": labels = [label(origin=origin, net=.0155)]
    if scenario == "round_lot":
        labels, loader = [label(origin=origin, price=33)], lambda d, c: mark(d, c, close=33, pre=33)
    if scenario == "unaffordable_lot": labels = [label(origin=origin, price=1001)]
    if scenario == "missing_entry": labels = []
    if scenario == "pending_entry":
        labels = [label(origin=origin, status="PENDING_T_MISSING_CANONICAL_AUCTION")]
        labels[0].update(proxy_fill=None)
    if scenario == "held":
        labels = [label(origin=origin, status="PENDING_EXIT_LIMIT_UP_HELD")]
        loader = lambda d, c: mark(d, c, close=10 if d == T else 10.5)
    if scenario == "known_no_fill": labels = [label(origin=origin, status="NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED")]
    if scenario == "missing_first_mark":
        labels = [label(origin=origin, status="PENDING_EXIT_MISSING_MINUTES")]
        loader = lambda d, c: None if d == T else mark(d, c)
    if scenario == "late_verified_exit": loader = lambda d, c: None
    if scenario in {"adjusted_mark", "unverified_adjustment"}:
        labels = [label(origin=origin, status="PENDING_EXIT_LIMIT_UP_HELD")]
        loader = lambda d, c: mark(d, c) if d == T else mark(d, c, close=5.1, pre=5,
            corroboration=5 if scenario == "adjusted_mark" else None)
    if scenario == "future_entry": asof = D
    if scenario == "future_exit": asof = T
    if scenario in {"overlap", "unknown_entry_overlaps"}:
        rows, labels, asof = [], [], T1
        for i, day in enumerate(DATES[:10]):
            code = f"600{i:03d}.SH"
            rows.append(row(day, code))
            if scenario == "overlap":
                labels.append(label(day, code, origin=origin, exit_day=T1,
                    status=legacy.SETTLED if i == 0 else "PENDING_EXIT_LIMIT_UP_HELD"))
    if scenario == "two_ranks":
        rows.append(row(code="600001.SH", rank=2))
        labels.append(label(code="600001.SH", origin=origin, net=.0155))
    actual = replay(rows, labels, as_of=asof, loader=loader)
    expected = legacy.replay_capital(rows, [legacy_label(v) for v in labels],
        open_dates=DATES, as_of_date=asof, load_daily=loader)
    assert_economics_equal(actual, expected)
    first = actual["accounts"]["top1"]
    if scenario == "loss":
        assert first["equity"] == 997550 and first["records"][0]["net_profit"] == -2450
        assert first["records"][0]["charged_round_trip_cost"] == 450
        assert first["equity_curve"][0]["equity"] == 999550
    if scenario == "round_lot":
        assert first["records"][0]["shares"] == 3000 and first["cash_balance"] == 997574.5
    if scenario == "overlap":
        assert len(first["records"]) == 10 and first["capital_unavailable_slots"] == 1
        assert first["records"][-1]["status"] == "CAPITAL_UNAVAILABLE"
        assert len(first["open_positions"]) == 8 and first["equity"] == 993950
    if scenario == "unknown_entry_overlaps":
        assert first["reserved_entry_cash"] == 904050 and first["capital_unavailable_slots"] == 1
        assert first["equity"] is None
    if scenario == "two_ranks":
        assert first["equity"] == 997550 and actual["accounts"]["top2"]["equity"] == 1001550
        assert first["initial_cash"] == actual["accounts"]["top2"]["initial_cash"] == 1_000_000


@pytest.mark.parametrize("origin", ["base", "candidate"])
def test_v3_source_identity_remains_in_input_hash_and_every_record(origin):
    value = label(origin=origin)
    before = deepcopy(value)
    out = replay(labels=[value])
    assert value == before
    assert out["source_overlay_contract"] == overlay.CONTRACT
    assert out["source_overlay_contract"] is not overlay.CONTRACT
    assert out["auction_source_policy_ids_observed"] == [value["auction_source_policy_id"]]
    record = out["accounts"]["top1"]["records"][0]
    for key in SOURCE_FIELDS:
        assert record[key] == value.get(key)
    expected_hash = hashlib.sha256(json.dumps([value], sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    assert out["label_input_sha256"] == expected_hash
    assert "source_policy_contract" not in out
    assert out["label_verification"] == "CALLER_VERIFIED_LABELS_REQUIRED_NOT_INDEPENDENT_SOURCE_REPLAY"
    assert out["files_written"] == 0 and not out["training_performed"]
    assert not out["production_activation_allowed"] and not out["actual_execution_claimed"]


@pytest.mark.parametrize("origin", ["base", "candidate"])
@pytest.mark.parametrize("field", list(policy_v3.CONTRACT) + ["label_policy_id", "basis", "reported_price_preserved",
    "actual_capacity_verified", "actual_execution_claimed", "production_activation_allowed", "minute_source_observed",
    "entry_price_evidence", "round_trip_cost_rate"])
def test_missing_v3_contract_or_source_identity_is_rejected(origin, field):
    value = label(origin=origin)
    value.pop(field)
    with pytest.raises((ValueError, TypeError)):
        replay(labels=[value])


@pytest.mark.parametrize("field", ["source_overlay_policy_id", "auction_qualification_policy_id"])
def test_candidate_source_never_loses_overlay_identity(field):
    value = label(origin="candidate")
    value.pop(field)
    with pytest.raises(ValueError): replay(labels=[value])


@pytest.mark.parametrize("field", ["source_policy_id", "qualification_policy_id", "overlay_policy_id", "source_origin",
                                  "daily_source_origin", "loaded_source_qualification_claimed", "ts_code", "trade_date"])
def test_candidate_entry_evidence_identity_cannot_disappear(field):
    value = label(origin="candidate")
    value["entry_price_evidence"].pop(field)
    with pytest.raises(ValueError): replay(labels=[value])


@pytest.mark.parametrize("changes", [dict(policy_v2.CONTRACT), {"entry_policy_id": "research_auction_or_open_no_cap_v1"},
    {"source_policy_contract": dict(policy_v2.CONTRACT)}, {"plan_version": "v2"},
    {"source_overlay_policy_id": overlay.OVERLAY_POLICY_ID}, {"arbitrary_source_policy_id": "invented"}])
def test_base_v3_cannot_be_disguised_as_v2_or_overlay(changes):
    value = label()
    value.update(changes)
    with pytest.raises(ValueError): replay(labels=[value])


@pytest.mark.parametrize("origin", ["base", "candidate"])
@pytest.mark.parametrize("status", ["NO_FILL_API_ERROR", "NO_FILL_SOURCE_UNAVAILABLE", "NO_FILL_PRICE_MISMATCH", "MISSING_MINUTES"])
def test_unknown_status_does_not_create_zero_trade(origin, status):
    value = label(origin=origin, status="PENDING_EXIT_MISSING_MINUTES")
    value.update(label_status=status, proxy_fill=0, slot_net_return=0.)
    with pytest.raises(ValueError): replay(labels=[value])


@pytest.mark.parametrize("origin", ["base", "candidate"])
@pytest.mark.parametrize("change", ["cost", "t0_exit", "net_mismatch", "gross_mismatch", "wrong_date", "old_exit"])
def test_economic_policy_and_terminal_mismatch_fail_closed(origin, change):
    value = label(origin=origin)
    if change == "cost": value["round_trip_cost_rate"] = .009
    if change == "t0_exit": value.update(actual_exit_date=T, actual_exit_time=at(T, "10:00:00"))
    if change == "net_mismatch": value["conditional_net_return"] = .99
    if change == "gross_mismatch": value["exit_evidence"]["gross_return"] = .99
    if change == "wrong_date": value["exec_date"] = T1
    if change == "old_exit": value["label_policy_id"] = "old_open_exit"
    with pytest.raises(ValueError): replay(labels=[value])


@pytest.mark.parametrize("mutation", ["missing", "extra", "v2", "wrong_cost", "wrong_source", "none"])
def test_registered_overlay_contract_required_exactly(mutation):
    contract = deepcopy(overlay.CONTRACT)
    if mutation == "missing": contract.pop("overlay_policy_id")
    if mutation == "extra": contract["extra"] = True
    if mutation == "v2": contract = dict(policy_v2.CONTRACT)
    if mutation == "wrong_cost": contract["round_trip_cost_rate"] = .009
    if mutation == "wrong_source": contract["auction_source_policy_ids"] = [policy_v3.AUCTION_SOURCE_POLICY_ID]
    if mutation == "none": contract = None
    with pytest.raises(ValueError, match="EXACT_V3_SOURCE_OVERLAY"):
        capital.replay_capital_v3([row()], [label()], open_dates=DATES, as_of_date=T1,
            load_daily=mark, source_overlay_contract=contract)


class IdentityOnly(dict):
    """Tripwire: any inspection beyond frozen D/code fails, including JSON hash."""
    def get(self, key, default=None):
        if key not in {"signal_date", "ts_code"}: raise AssertionError("future outcome inspected")
        return super().get(key, default)
    def __getitem__(self, key):
        if key not in {"signal_date", "ts_code"}: raise AssertionError("future outcome inspected")
        return super().__getitem__(key)
    def keys(self): raise AssertionError("future dict copied")
    def items(self): raise AssertionError("future dict hashed")
    def __iter__(self): raise AssertionError("future dict iterated")


def test_future_holdout_not_validated_copied_ranked_hashed_or_marked():
    future = IdentityOnly(signal_date="20260914", ts_code="600009.SH", label_status=object(),
                          slot_net_return=object(), candidate_rank=object())
    calls = []
    def loader(day, code):
        calls.append((day, code))
        return mark(day, code)
    baseline = replay()
    out = replay([future, row()], [future, label()], loader=loader)
    assert out["accounts"] == baseline["accounts"]
    assert out["label_input_sha256"] == baseline["label_input_sha256"]
    assert out["ranking_input_sha256"] == baseline["ranking_input_sha256"]
    assert out["holdout_label_rows_excluded_without_outcome_evaluation"] == 1
    assert out["holdout_ranked_rows_excluded_without_outcome_evaluation"] == 1
    assert calls == [(T, CODE)] and out["holdout_role"] == "FUTURE_FORWARD_ONLY_NOT_EVALUATED"


def test_future_entry_and_exit_never_load_future_daily_or_create_early_sale():
    calls = []
    def loader(day, code):
        assert day <= T
        calls.append(day)
        return mark(day, code)
    waiting = replay(as_of=D, loader=loader)["accounts"]["top1"]
    assert waiting["records"][0]["status"] == "WAITING_ENTRY" and not calls
    held = replay(as_of=T, loader=loader)["accounts"]["top1"]
    assert held["cash_balance"] == 900000 and held["realized_net_profit"] == 0
    assert held["records"][0]["actual_exit_time"] is None and calls == [T]


@pytest.mark.parametrize("kind", ["identity", "date", "sha", "missing", "nan"])
def test_invalid_mark_stays_unknown_and_keeps_capital_occupied(kind):
    def broken(day, code):
        value = mark(day, code)
        if kind == "identity": value["ts_code"] = "600999.SH"
        if kind == "date": value["trade_date"] = T1
        if kind == "sha": value["source_files"][0]["sha256"] = "bad"
        if kind == "missing": value.pop("source_files")
        if kind == "nan": value["close"] = float("nan")
        return value
    first = replay(labels=[label(status="PENDING_EXIT_MISSING_MINUTES")], loader=broken)["accounts"]["top1"]
    assert first["equity"] is first["maximum_drawdown"] is None
    assert len(first["open_positions"]) == 1 and first["cash_balance"] == 900000


def test_duplicates_missing_ranks_and_empty_top2_are_honest():
    with pytest.raises(ValueError, match="duplicate"): replay([row(), row()])
    with pytest.raises(ValueError, match="missing ranks"): replay([row(rank=2)])
    with pytest.raises(ValueError, match="duplicate label"): replay(labels=[label(), label()])
    second = replay()["accounts"]["top2"]
    assert second["records"] == [] and second["no_frozen_candidate_dates"] == [D]
    assert second["cash_balance"] == 1_000_000


def test_alternative_frozen_rank_field_preserves_ranking():
    value = row()
    value["promotion_rank"] = value.pop("candidate_rank")
    result = replay([value], rank_field="promotion_rank")
    assert result["rank_field"] == "promotion_rank" and result["accounts"]["top1"]["equity"] == 997550


def test_pure_replay_never_calls_old_v2_adapter_writes_or_network(monkeypatch, tmp_path):
    value = label(origin="candidate")
    def forbidden(*args, **kwargs): raise AssertionError("unexpected write/network/legacy replay")
    monkeypatch.setattr(legacy, "replay_capital", forbidden)
    monkeypatch.setattr(policy_v2, "validate_label_contract", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    result = replay(labels=[value])
    assert result["files_written"] == 0 and not result["training_performed"]
    assert list(tmp_path.iterdir()) == [] and result["accounts"]["top1"]["equity"] == 997550


def test_event_loop_is_mechanically_preserved_from_frozen_capital():
    old, new = inspect.getsource(legacy.replay_capital), inspect.getsource(capital.replay_capital_v3)
    # Source contracts/holdout filtering change around the loop, never its economics.
    start, end = '        events.sort(', '    return '
    assert old[old.index(start):old.index(end)] == new[new.index(start):new.index(end)]


@pytest.mark.parametrize("origin", ["base", "candidate"])
def test_offline_real_builder_wiring_preserves_loss_and_all_source_files(request, monkeypatch, origin):
    # The small fixture sources are synthetic; overlay authority is explicitly
    # mocked by the shared fixture. This is wiring coverage, not cloud acceptance.
    from work.profit_1000_upgrade import labels_v3
    selected_case = request.getfixturevalue("v3_case" if origin == "base" else "overlay_case")
    if origin == "base":
        root = selected_case[0]
        labels = base_label_report(selected_case)["rows"]
        roots = [root]
    else:
        root = selected_case[0]
        labels = overlay_label_report(selected_case, monkeypatch)["rows"]
        roots = [root, selected_case[2]]
    before = {str(p): p.read_bytes() for folder in roots for p in folder.rglob("*") if p.is_file()}
    settlement = labels_v3.settlement
    def loader(day, code):
        assert day <= T1
        path = settlement._find_market_file(root, day, "daily")
        value = settlement._market_rows(path, day)[code]
        return dict(value, source_files=[settlement._source_binding(root, path)])
    out = replay(labels=labels, loader=loader)
    assert out["accounts"]["top1"]["equity"] == 997550
    assert out["accounts"]["top1"]["records"][0]["net_profit"] == -2450
    assert out["auction_source_policy_ids_observed"] == [labels[0]["auction_source_policy_id"]]
    after = {str(p): p.read_bytes() for folder in roots for p in folder.rglob("*") if p.is_file()}
    assert before == after and out["files_written"] == 0
