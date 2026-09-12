from __future__ import annotations

import json
import importlib.util
import copy
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("exit1000_legacy_test_helpers", Path(__file__).with_name("test_decision_executable_profit_shadow_settlement.py"))
old = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(old)
from top10decision.decision import executable_profit_shadow_settlement as settlement
from top10decision.decision.shadow_exit_minute_truth import expected_bar_ends, minute_paths, source_bytes


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(settlement, "validate_internal_forward_shadow_payload", lambda *a, **k: None)
    root = old._prepare_repo(tmp_path, slot_count=1)
    replacements = {"20260824": "20260910", "20260825": "20260911", "20260826": "20260914"}
    selection = root / settlement.SELECTION_ROOT / "shadow_20260824.json"
    raw = selection.read_text()
    for before, after in replacements.items():
        raw = raw.replace(before, after)
    selection.rename(selection.with_name("shadow_20260910.json"))
    selection.with_name("shadow_20260910.json").write_text(raw)
    for before, after in list(replacements.items())[1:]:
        directory = root / "data/market/raw/2026" / before
        directory.rename(directory.with_name(after))
        for file in directory.with_name(after).glob("*.csv"):
            file.write_text(file.read_text().replace(before, after))
    old._write_csv(root / "data/market/raw/2026/20260914/daily.csv",
                   "ts_code,trade_date,open,high,low,close,pre_close,vol",
                   ["600001.SH,20260914,10.20,10.80,10.20,10.80,10.50,100000"])
    return root


def install_minutes(root, *, missing=False):
    raw_rows = []
    for i, stamp in enumerate(expected_bar_ends("20260914")):
        raw_rows.append(dict(ts_code="600001.SH", trade_time=stamp, open=10.2 if i == 0 else 10.8,
                            high=10.8, low=10.2 if i == 0 else 10.8, close=10.8, vol=100, amount=1080))
    data, metadata = source_bytes(raw_rows, "20260914", "600001.SH", fetched_at_utc="2026-09-14T08:00:00+00:00")
    path, meta = minute_paths(root, "20260914", "600001.SH")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    meta.write_bytes(metadata)
    if missing:
        path.write_bytes(data.rsplit(b"\n", 2)[0] + b"\n")


def test_new_settlement_uses_1000_not_daily_open(repo):
    install_minutes(repo)
    result = settlement.settle_signal_date(repo, "20260910", as_of_date="20260914")
    assert result["t1_settlement_path"]
    payload = json.loads((repo / result["t1_settlement_path"]).read_text())
    assert payload["schema_version"] == settlement.SETTLEMENT_SCHEMA_V3
    assert payload["contract_id"] == settlement.EXIT_POLICY_ID_1000
    row = payload["rows"][0]
    assert row["exit_price"] == 10.8
    assert row["net_return_after_cost"] == pytest.approx(0.0755)
    assert "exit_open_price" not in row
    assert "10:00:00" in row["actual_exit_time"]
    assert row["exit_time_semantics"] == "NEXT_BAR_OPEN_MINUTE_PROXY"
    assert any("exit_1000_1m" in src["path"] for src in payload["source_files"])
    settlement.validate_t1_settlement(payload)
    before = (repo / result["t1_settlement_path"]).read_bytes()
    settlement.settle_signal_date(repo, "20260910", as_of_date="20260914")
    assert (repo / result["t1_settlement_path"]).read_bytes() == before


@pytest.mark.parametrize("corrupt", [False, True])
def test_new_exit_missing_minutes_never_falls_back_to_open(repo, corrupt):
    if corrupt:
        install_minutes(repo, missing=True)
    result = settlement.settle_signal_date(repo, "20260910", as_of_date="20260914")
    assert result["t1_settlement_path"] is None
    assert "PENDING" in result["t1_settlement_status"]
    stats = json.loads((repo / result["statistics_path"]).read_text())
    assert stats["cohorts"]["shadow_slot_1"]["selected_slots"] == 1
    assert stats["cohorts"]["shadow_slot_1"]["pending_exit_slots"] == 1
    assert stats["cohorts"]["shadow_slot_1"]["mean_net_return_after_cost"] is None


def test_mixed_policy_returns_not_merged_and_negative_results_retained():
    records = [dict(signal_date="20260908", shadow_slot=1, t_validated=True, proxy_fill=1,
                    terminal=True, strategy_slot_return=-0.1, net_return_after_cost=-0.1,
                    stress_net_return=-0.1045, exit_policy_id="LEGACY_T1_OPEN"),
               dict(signal_date="20260910", shadow_slot=1, t_validated=True, proxy_fill=1,
                    terminal=True, strategy_slot_return=-0.2, net_return_after_cost=-0.2,
                    stress_net_return=-0.2045, exit_policy_id=settlement.EXIT_POLICY_ID_1000)]
    stats = settlement._cohort_metrics(records)
    assert stats["selected_slots"] == 2
    assert stats["mean_net_return_after_cost"] is None
    assert stats["equal_weight_cumulative_return"] is None
    assert stats["by_exit_policy"][settlement.EXIT_POLICY_ID_1000]["mean_net_return_after_cost"] == -0.2


def test_no_new_model_is_falsely_declared_trained():
    policy = json.loads((Path(__file__).resolve().parents[1] / settlement.EXIT_POLICY_PATH_1000).read_text())
    assert policy["new_profit_model_trained"] is False
    assert policy["old_open_exit_labels_allowed_for_new_model"] is False
    assert policy["profit_threshold_skip_allowed"] is False


def completed_payload(repo):
    install_minutes(repo)
    report = settlement.settle_signal_date(repo, "20260910", as_of_date="20260914")
    path = repo / report["t1_settlement_path"]
    return path, json.loads(path.read_bytes())


def seal(payload):
    payload["snapshot_sha256"] = settlement._payload_snapshot(payload)
    return payload


@pytest.mark.parametrize("field,value", [
    ("decision_time", "2026-09-14T10:00:00+00:00"),
    ("decision_time", "2026-09-14 10:00:00"),
    ("decision_time", "2026-09-14T10:00:01+08:00"),
    ("decision_time", "2026-09-11T10:00:00+08:00"),
    ("decision_time", "2026-09-14T09:30:00+08:00"),
    ("decision_time", "2026-09-14T10:01:00+08:00"),
    ("decision_time", "2026-09-14T09:59:00+08:00"),
    ("execution_bar_end", "2026-09-14T10:02:00+08:00"),
    ("execution_bar_end", "2026-09-15T10:01:00+08:00"),
    ("actual_exit_date", "2026-09-14"),
    ("actual_exit_date", "20260915"),
    ("actual_exit_time", "2026-09-14T10:01:00+08:00"),
    ("held_limit_up_sessions", False),
    ("blocked_exit_sessions", "0"),
    ("suspended_exit_sessions", 0.0),
    ("exit_price", True),
    ("entry_open_price", 0),
    ("exit_reason", "LEGACY_FIRST_OPEN"),
])
def test_v3_rejects_resealed_invalid_time_price_and_session_fields(repo, field, value):
    _, payload = completed_payload(repo)
    payload["rows"][0][field] = value
    with pytest.raises(settlement.ExecutableProfitSettlementError):
        settlement.validate_t1_settlement(seal(payload))


@pytest.mark.parametrize("hour", ["08:00", "11:30", "12:00", "15:00"])
def test_v3_rejects_execution_outside_tradable_bar_start(repo, hour):
    _, payload = completed_payload(repo)
    row = payload["rows"][0]
    row["execution_bar_start"] = row["actual_exit_time"] = f"2026-09-14T{hour}:00+08:00"
    end = settlement.datetime.fromisoformat(row["execution_bar_start"]) + settlement.timedelta(minutes=1)
    row["execution_bar_end"] = end.isoformat()
    with pytest.raises(settlement.ExecutableProfitSettlementError):
        settlement.validate_t1_settlement(seal(payload))


@pytest.mark.parametrize("fault", ["csv", "meta", "both", "other_code", "year", "raw", "exact_d", "extra_source"])
def test_v3_rejects_resealed_incomplete_or_wrong_source_bindings(repo, fault):
    _, payload = completed_payload(repo)
    if fault in {"csv", "meta", "both"}:
        payload["source_files"] = [x for x in payload["source_files"] if not (
            "exit_1000_1m" in x["path"] and (fault == "both" or x["path"].endswith(".csv" if fault == "csv" else ".meta.json")))]
    elif fault == "other_code":
        for source in payload["source_files"]:
            source["path"] = source["path"].replace("600001_SH", "600002_SH")
    elif fault == "year":
        for source in payload["source_files"]:
            source["path"] = source["path"].replace("/exit_1000_1m/2026/", "/exit_1000_1m/2025/")
    elif fault == "raw":
        payload["source_files"] = [x for x in payload["source_files"] if not x["path"].endswith("/daily.csv")]
    elif fault == "exact_d":
        payload["t_verification"]["path"] = payload["t_verification"]["path"].replace("20260910", "20260909")
    else:
        payload["source_files"].append({"path": "data/market/raw/2026/20260915/daily.csv", "sha256": "a" * 64})
    with pytest.raises(settlement.ExecutableProfitSettlementError):
        settlement.validate_t1_settlement(seal(payload))


@pytest.mark.parametrize("source", ["t_daily", "t_verification", "exit_daily", "minute", "policy"])
def test_terminal_reuse_checks_actual_bound_bytes(repo, source):
    path, payload = completed_payload(repo)
    if source == "t_daily":
        target = repo / "data/market/raw/2026/20260911/daily.csv"
    elif source == "t_verification":
        target = repo / payload["t_verification"]["path"]
    elif source == "exit_daily":
        target = repo / "data/market/raw/2026/20260914/daily.csv"
    elif source == "minute":
        target = minute_paths(repo, "20260914", "600001.SH")[0]
    else:
        target = repo / settlement.EXIT_POLICY_PATH_1000
    before = path.read_bytes()
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(settlement.ExecutableProfitSettlementError):
        settlement.build_t1_settlement(repo, "20260910", as_of_date="20260914")
    assert path.read_bytes() == before


def test_resealed_valid_time_or_profit_cannot_disagree_with_bound_replay(repo):
    path, payload = completed_payload(repo)
    row = payload["rows"][0]
    row["execution_bar_start"] = row["actual_exit_time"] = "2026-09-14T10:01:00+08:00"
    row["execution_bar_end"] = "2026-09-14T10:02:00+08:00"
    settlement.validate_t1_settlement(seal(payload))  # Shape is valid; source replay is not.
    path.write_bytes(settlement._canonical_bytes(payload))
    with pytest.raises(settlement.ExecutableProfitSettlementError, match="bound minute replay"):
        settlement.build_t1_settlement(repo, "20260910", as_of_date="20260914")


@pytest.mark.parametrize("key,value", [
    ("execution_price", "DAILY_OPEN"), ("blocked_exit", "IGNORE"),
    ("always_record_frozen_top1_top2", 1), ("new_profit_model_trained", True),
])
def test_entire_new_policy_contract_is_checked(repo, key, value):
    policy_path = repo / settlement.EXIT_POLICY_PATH_1000
    policy = json.loads(policy_path.read_bytes())
    policy[key] = value
    policy_path.write_text(json.dumps(policy))
    with pytest.raises(settlement.ExecutableProfitSettlementError, match="policy contract"):
        settlement.build_t1_settlement(repo, "20260910", as_of_date="20260914")


def make_no_fill(repo):
    auction = repo / "data/market/raw/2026/20260911/stk_auction_o.csv"
    auction.write_text(auction.read_text().replace("20000000", "100"))
    report = settlement.settle_signal_date(repo, "20260910", as_of_date="20260911")
    path = repo / report["t1_settlement_path"]
    return path, json.loads(path.read_bytes())


@pytest.mark.parametrize("field,value", [("exit_price", 10), ("actual_exit_time", "2026-09-14T10:00:00+08:00"),
                                          ("held_limit_up_sessions", 1), ("gross_return", 0), ("profit_after_cost", 0)])
def test_no_fill_cannot_invent_minute_price_or_execution(repo, field, value):
    _, payload = make_no_fill(repo)
    assert payload["rows"][0]["proxy_fill"] == 0 and payload["source_files"] == []
    payload["rows"][0][field] = value
    with pytest.raises(settlement.ExecutableProfitSettlementError):
        settlement.validate_t1_settlement(seal(payload))


def legacy_payload(payload):
    payload = copy.deepcopy(payload)
    payload["schema_version"] = settlement.SETTLEMENT_SCHEMA
    payload["contract_id"] = settlement.CONTRACT_ID
    payload.pop("exit_policy")
    for row in payload["rows"]:
        row["exit_open_price"] = row["exit_price"]
        for key in settlement.EXIT_ROW_FIELDS_1000:
            row.pop(key)
        if row["proxy_fill"]:
            row["settlement_status"] = "FINAL_FIRST_TRADABLE_OPEN_PUBLIC_MARKET_PROXY"
    payload["source_files"] = [x for x in payload["source_files"] if "exit_1000_1m" not in x["path"]]
    return seal(payload)


def test_existing_old_no_fill_keeps_original_bytes_under_new_rule(repo):
    path, payload = make_no_fill(repo)
    old_payload = legacy_payload(payload)
    settlement.validate_t1_settlement(old_payload)
    original = settlement._canonical_bytes(old_payload)
    path.write_bytes(original)
    observed, status = settlement.build_t1_settlement(repo, "20260910", as_of_date="20260911")
    assert status == "FINAL_SETTLED_IMMUTABLE_EXISTING" and observed == old_payload
    assert path.read_bytes() == original


def test_legacy_filled_open_exit_is_not_relabelled_as_new_policy(repo):
    path, payload = completed_payload(repo)
    payload = legacy_payload(payload)
    settlement.validate_t1_settlement(payload)
    path.write_bytes(settlement._canonical_bytes(payload))
    with pytest.raises(settlement.ExecutableProfitSettlementError, match="legacy open settlement"):
        settlement.build_t1_settlement(repo, "20260910", as_of_date="20260914")
    with pytest.raises(settlement.ExecutableProfitSettlementError, match="legacy open settlement"):
        settlement.materialize_t1_settlement(repo, payload)
    with pytest.raises(settlement.ExecutableProfitSettlementError, match="legacy open settlement"):
        settlement.build_statistics(repo, as_of_date="20260914")


def install_flat_session(repo, date, price, pre_close, up, down, *, zero_volume=False):
    folder = repo / "data/market/raw/2026" / date
    old._write_csv(folder / "daily.csv", "ts_code,trade_date,open,high,low,close,pre_close,vol",
                   [f"600001.SH,{date},{price},{price},{price},{price},{pre_close},{0 if zero_volume else 24000}"])
    old._write_csv(folder / "stk_limit.csv", "ts_code,trade_date,up_limit,down_limit,pre_close",
                   [f"600001.SH,{date},{up},{down},{pre_close}"])
    if not zero_volume:
        data, metadata = source_bytes([
            dict(ts_code="600001.SH", trade_time=stamp, open=price, high=price, low=price,
                 close=price, vol=100, amount=price * 100)
            for stamp in expected_bar_ends(date)], date, "600001.SH",
            fetched_at_utc=f"{date[:4]}-{date[4:6]}-{date[6:]}T08:00:00+00:00")
        path, meta = minute_paths(repo, date, "600001.SH")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        meta.write_bytes(metadata)


def test_existing_future_exit_is_not_reused_or_read_before_asof(repo, monkeypatch):
    install_flat_session(repo, "20260914", 11.55, 10.5, 11.55, 9.45)
    install_flat_session(repo, "20260915", 11.7, 11.55, 12.71, 10.4)
    report = settlement.settle_signal_date(repo, "20260910", as_of_date="20260915")
    path = repo / report["t1_settlement_path"]
    original = path.read_bytes()
    monkeypatch.setattr(settlement, "_resolve_public_exit_1000", lambda **kw: pytest.fail("must not read future exit truth"))
    payload, status = settlement.build_t1_settlement(repo, "20260910", as_of_date="20260914")
    assert payload is None and status == "PENDING_EXIT_AS_OF_CUTOFF"
    assert path.read_bytes() == original


def test_terminal_replay_uses_its_bound_raw_path_not_new_preferred_alias(repo):
    dated = repo / "data/market/raw/2026/20260914"
    bound = repo / "data/market/raw/20260914"
    dated.rename(bound)
    path, payload = completed_payload(repo)
    original = path.read_bytes()
    dated.mkdir()
    for source in bound.glob("*.csv"):
        (dated / source.name).write_bytes(source.read_bytes())
    observed, status = settlement.build_t1_settlement(repo, "20260910", as_of_date="20260914")
    assert status == "FINAL_SETTLED_IMMUTABLE_EXISTING" and observed == payload
    assert path.read_bytes() == original


def test_suspension_is_only_exception_to_per_day_minutes_and_cannot_be_fabricated(repo):
    install_flat_session(repo, "20260914", 10.5, 10.5, 11.55, 9.45, zero_volume=True)
    install_flat_session(repo, "20260915", 10.7, 10.5, 11.55, 9.45)
    report = settlement.settle_signal_date(repo, "20260910", as_of_date="20260915")
    path = repo / report["t1_settlement_path"]
    payload = json.loads(path.read_bytes())
    row = payload["rows"][0]
    assert row["suspended_exit_sessions"] == row["delayed_trading_days"] == 1
    assert not any("exit_1000_1m/2026/20260914" in x["path"] for x in payload["source_files"])
    settlement.build_t1_settlement(repo, "20260910", as_of_date="20260915")
    # Resealing the payload cannot invent suspension when its exact daily source
    # says trading took place: full terminal reuse independently replays it.
    daily = repo / "data/market/raw/2026/20260914/daily.csv"
    daily.write_text(daily.read_text().replace("10.5,0\n", "10.5,100\n"))
    for src in payload["source_files"]:
        if src["path"] == daily.relative_to(repo).as_posix():
            src["sha256"] = settlement._sha256(daily)
    path.write_bytes(settlement._canonical_bytes(seal(payload)))
    with pytest.raises(settlement.ExecutableProfitSettlementError, match="replay incomplete"):
        settlement.build_t1_settlement(repo, "20260910", as_of_date="20260915")


@pytest.mark.parametrize("entrypoint", ["materialize", "statistics"])
def test_new_publication_and_statistics_do_not_trust_snapshot_without_source_bytes(repo, entrypoint):
    _, payload = completed_payload(repo)
    raw = minute_paths(repo, "20260914", "600001.SH")[0]
    raw.write_bytes(raw.read_bytes() + b"\n")
    with pytest.raises(settlement.ExecutableProfitSettlementError, match="source bytes changed"):
        if entrypoint == "materialize":
            settlement.materialize_t1_settlement(repo, payload)
        else:
            settlement.build_statistics(repo, as_of_date="20260914")


def test_1500_break_can_execute_next_session_with_exact_time_and_one_held_day(repo):
    install_flat_session(repo, "20260914", 11.55, 10.5, 11.55, 9.45)
    install_flat_session(repo, "20260915", 11.6, 11.4, 12.54, 10.26)
    raw_rows = [dict(ts_code="600001.SH", trade_time=stamp, open=11.55, high=11.55,
                    low=11.4 if stamp.endswith("15:00:00") else 11.55,
                    close=11.4 if stamp.endswith("15:00:00") else 11.55, vol=100, amount=1155)
                for stamp in expected_bar_ends("20260914")]
    raw, meta = source_bytes(raw_rows, "20260914", "600001.SH", fetched_at_utc="2026-09-14T08:00:00+00:00")
    minute_path, meta_path = minute_paths(repo, "20260914", "600001.SH")
    minute_path.write_bytes(raw)
    meta_path.write_bytes(meta)
    old._write_csv(repo / "data/market/raw/2026/20260914/daily.csv",
                   "ts_code,trade_date,open,high,low,close,pre_close,vol",
                   ["600001.SH,20260914,11.55,11.55,11.4,11.4,10.5,24000"])
    report = settlement.settle_signal_date(repo, "20260910", as_of_date="20260915")
    payload = json.loads((repo / report["t1_settlement_path"]).read_bytes())
    row = payload["rows"][0]
    assert row["decision_time"] == "2026-09-14T15:00:00+08:00"
    assert row["actual_exit_time"] == "2026-09-15T09:30:00+08:00"
    assert row["held_limit_up_sessions"] == row["delayed_trading_days"] == 1
    assert row["blocked_exit_sessions"] == 0
    assert row["net_return_after_cost"] == pytest.approx(0.1555)
    settlement.build_t1_settlement(repo, "20260910", as_of_date="20260915")


def test_newly_bought_not_due_slot_does_not_hide_completed_synthetic_history():
    complete = dict(signal_date="20260910", shadow_slot=1, t_validated=True, proxy_fill=1,
                    terminal=True, strategy_slot_return=0.02, net_return_after_cost=0.02,
                    stress_net_return=0.0155, exit_policy_id=settlement.EXIT_POLICY_ID_1000)
    pending = dict(signal_date="20260911", shadow_slot=1, t_validated=True, proxy_fill=1,
                   terminal=False, strategy_slot_return=None, net_return_after_cost=None,
                   stress_net_return=None, exit_overdue_or_held=False, exit_policy_id=settlement.EXIT_POLICY_ID_1000)
    stats = settlement._cohort_metrics([complete, pending])
    assert stats["pending_exit_slots"] == 1
    assert stats["equal_weight_cumulative_return"] == 0.02
    for field in ("exit_overdue_or_held", "delayed_trading_days"):
        changed = dict(pending, **{field: True if field == "exit_overdue_or_held" else 1})
        stats = settlement._cohort_metrics([complete, changed])
        assert stats["equal_weight_cumulative_return"] is None
        assert stats["mean_net_return_after_cost"] == 0.02
