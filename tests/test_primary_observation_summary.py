from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from scripts import settle_primary_observations as projection
from scripts.settle_primary_observations import (
    EXIT_1000_POLICY_ID, EXIT_POLICY_CONFIG_PATH, LEGACY_EXIT_POLICY_ID,
    VERSIONED_POLICY_ID, VERSIONED_SCHEMA, build, observation_row,
    plan_exit_minute_requests, summarize,
)

ROOT = Path(__file__).resolve().parents[1]
FROZEN = dict(ts_code="600001.SH", name="样本", industry="示例", stage_transition="2→3", promotion_rank=1)


def tables(date, name):
    if name == "stk_limit":
        return {"600001.SH": dict(up_limit=11, down_limit=9)}
    return {"600001.SH": dict(open=10, close=10.5, pre_close=10, vol=100)}


def test_pending_date_never_reads_future_market():
    def forbidden(*args):
        raise AssertionError("future read")
    row = observation_row(FROZEN, "20260904", "20260907", "20260908", "20260904", forbidden)
    assert row["validation_status"] == "PENDING_T"
    assert row["slot_net_return"] is None


def test_missing_data_is_not_no_fill_or_zero_return():
    row = observation_row(FROZEN, "20260902", "20260903", "20260904", "20260904", lambda *args: {})
    assert row["validation_status"] == "MISSING_T_TRUTH"
    assert row["proxy_fill"] is None
    assert row["slot_net_return"] is None


@pytest.mark.parametrize("bad_date,expected_status", [
    ("20260903", "MISSING_T_TRUTH"), ("20260904", "MISSING_T1_TRUTH"),
])
def test_negative_volume_is_invalid_truth_not_zero_return_or_blocked_exit(bad_date, expected_status):
    def negative(date, name):
        result = tables(date, name)
        if date == bad_date and name == "daily":
            result["600001.SH"]["vol"] = -1
        return result
    row = observation_row(FROZEN, "20260902", "20260903", "20260904", "20260904", negative)
    assert row["validation_status"] == expected_status
    assert row["actual_net_return"] is None
    assert row["slot_net_return"] is None
    assert "actual_exit_date" not in row


def test_t_verified_before_t1_matures():
    def only_t(date, name):
        assert date == "20260903"
        return tables(date, name)
    row = observation_row(FROZEN, "20260902", "20260903", "20260904", "20260903", only_t)
    assert row["validation_status"] == "PENDING_T1"
    assert row["continuation_limit_up_hit"] == 0
    assert row["actual_net_return"] is None


def test_down_limit_exit_is_unresolved_not_profitable_or_zero():
    def down(date, name):
        result = tables(date, name)
        if date == "20260904" and name == "daily":
            result["600001.SH"]["open"] = 9
        return result
    row = observation_row(FROZEN, "20260902", "20260903", "20260904", "20260904", down)
    assert row["validation_status"] == "UNRESOLVED_EXIT_PROXY"
    assert row["slot_net_return"] is None


def test_up_limit_entry_is_no_fill_only_when_t1_mature():
    def up(date, name):
        result = tables(date, name)
        if name == "daily":
            result["600001.SH"].update(open=11, close=11)
        return result
    pending = observation_row(FROZEN, "20260902", "20260903", "20260904", "20260903", up)
    mature = observation_row(FROZEN, "20260902", "20260903", "20260904", "20260904", up)
    assert pending["slot_net_return"] is None
    assert mature["validation_status"] == "FINAL_NO_FILL_PROXY"
    assert mature["slot_net_return"] == 0
    assert mature["actual_net_return"] is None


def test_return_cost_and_adjusted_previous_close():
    def split(date, name):
        result = tables(date, name)
        if date == "20260904":
            if name == "daily":
                result["600001.SH"].update(open=5.5, pre_close=5.25)
            else:
                result["600001.SH"].update(down_limit=4.72, up_limit=5.78)
        return result
    row = observation_row(FROZEN, "20260902", "20260903", "20260904", "20260904", split)
    assert row["actual_net_return"] == pytest.approx(.1 - .0045)
    assert row["actual_order_fill_observed"] is False


def test_blocked_exit_resolves_at_first_later_tradable_open_not_best_price():
    read_dates = []
    def delayed(date, name):
        read_dates.append(date)
        result = tables(date, name)
        if date == "20260904" and name == "daily":
            result["600001.SH"].update(open=9, close=9, pre_close=10.5)
        elif date == "20260907" and name == "daily":
            result["600001.SH"].update(open=9.5, close=10, pre_close=9)
        return result
    row = observation_row(FROZEN, "20260902", "20260903", "20260904", "20260908", delayed,
                          ["20260904", "20260907", "20260908"])
    assert row["actual_exit_date"] == "20260907"
    assert row["actual_net_return"] == pytest.approx(-.05 - .0045)
    assert "20260908" not in read_dates
    assert row["blocked_exit_sessions"] == 1


def test_real_frozen_window_excludes_recovery_and_preserves_ranks():
    payload, rows = build(ROOT, "20260904")
    assert payload["public_start_signal_date"] == "20260828"
    assert payload["statistics"]["excluded_retrospective_rows"] == 20
    assert sorted({r["signal_date"] for r in rows}) == ["20260828", "20260902", "20260903", "20260904"]
    for daily in payload["daily_summaries"]:
        d = daily["signal_date"]
        original = json.loads((ROOT / f"outputs/decision/three_rank_top10_{d}.json").read_text())
        expected = [(r["ts_code"], r["promotion_rank"]) for r in original["rows"]]
        assert [(r["ts_code"], r["promotion_rank"]) for r in rows if r["signal_date"] == d] == expected
    assert all(r["slot_net_return"] is None for r in rows if r["signal_date"] == "20260904")
    assert all(f["path"].split("/")[-2] <= "20260904" for f in payload["source_files"])
    assert payload["policy"]["return_strategy_forward_evidence"] is False
    assert payload["statistics"]["portfolio_is_capital_nav"] is False
    assert payload["statistics"]["equal_slot_cumulative_return"] is None
    assert payload["schema_version"] == "dc20_primary_observation_summary_v1"
    assert payload["policy"]["id"] == LEGACY_EXIT_POLICY_ID
    assert all("exit_policy_id" not in row and "actual_exit_time" not in row for row in rows)
    assert "return_statistics_by_exit_policy" not in payload["statistics"]
    assert EXIT_POLICY_CONFIG_PATH not in {item["path"] for item in payload["source_files"]}


def test_exact_calendar_rejects_weekend():
    with pytest.raises(ValueError, match="SSE"):
        build(ROOT, "20260905")


def test_missing_and_unfinished_day_never_enters_portfolio():
    row = observation_row(FROZEN, "20260902", "20260903", "20260904", "20260904", lambda *args: {})
    day = dict(signal_date="20260902", exec_date="20260903", pending_t_rows=0, pending_t1_rows=0,
               missing_t_truth_rows=1, missing_t1_truth_rows=0, unresolved_exit_rows=0)
    stats = summarize([row], [day], 0)
    assert stats["matured_portfolio_dates"] == 0
    assert stats["equal_slot_cumulative_return"] is None
    assert stats["final_win_rate"] is None


def test_hash_corruption_fails_closed(tmp_path):
    for relative in ("data/market/trade_cal_sse.csv",):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    directory = tmp_path / "outputs/decision"
    directory.mkdir(parents=True)
    for prefix, extension in (("primary_d_receipt", "json"), ("primary_d_runtime_features", "csv"),
                               ("three_rank_top10", "json"), ("three_rank_top10", "csv")):
        name = f"{prefix}_20260904.{extension}"
        shutil.copyfile(ROOT / "outputs/decision" / name, directory / name)
    runtime = directory / "primary_d_runtime_features_20260904.csv"
    runtime.write_bytes(runtime.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="binding"):
        build(tmp_path, "20260904")


def settled_exit(**changes):
    result = dict(
        exit_policy_id=EXIT_1000_POLICY_ID, exit_price=10.4,
        actual_exit_date="20260914", actual_exit_time="2026-09-14T10:00:00+08:00",
        decision_time="2026-09-14T10:00:00+08:00",
        execution_bar_start="2026-09-14T10:00:00+08:00",
        execution_bar_end="2026-09-14T10:01:00+08:00", timezone="Asia/Shanghai",
        exit_time_semantics="NEXT_BAR_OPEN_MINUTE_PROXY", exit_reason="TIME_1000_NEXT_BAR_OPEN",
        gross_return=.04, held_limit_up_sessions=0, blocked_exit_sessions=0,
        suspended_exit_sessions=0, delayed_trading_days=0, research_only=True,
        actual_execution_claimed=False, exit_capacity_verified=False,
    )
    return dict(result, **changes)


def new_row(asof="20260914", *, resolver=None, market=tables, minutes=None):
    return observation_row(FROZEN, "20260910", "20260911", "20260914", asof, market,
                           ["20260910", "20260911", "20260914", "20260915"], minutes, resolver)


def day_summary(row):
    status = row["validation_status"]
    day = dict(signal_date=row["signal_date"], exec_date=row["exec_date"],
               pending_t_rows=int(status == "PENDING_T"),
               pending_t1_rows=int(status in {"PENDING_T1", "PENDING_EXIT_PROXY"}),
               missing_t_truth_rows=int(status == "MISSING_T_TRUTH"),
               missing_t1_truth_rows=int(status == "MISSING_T1_TRUTH"),
               unresolved_exit_rows=int(status == "UNRESOLVED_EXIT_PROXY"))
    if row.get("exit_policy_id") == EXIT_1000_POLICY_ID:
        day["pending_exit_rows"] = int(status == "PENDING_EXIT_PROXY")
    return day


def test_legacy_scheduled_exit_uses_original_policy_even_after_cutover():
    def forbidden(**kwargs):
        raise AssertionError("old history must not use the new exit engine")
    expected = observation_row(FROZEN, "20260909", "20260910", "20260911", "20260911", tables)
    replay = observation_row(FROZEN, "20260909", "20260910", "20260911", "20260914", tables,
                             ["20260911", "20260914"], exit_resolver=forbidden)
    assert replay == expected
    assert replay["actual_exit_price"] == 10
    assert "exit_policy_id" not in replay
    assert "actual_exit_time" not in replay


@pytest.mark.parametrize("asof,expected", [("20260910", "PENDING_T"), ("20260911", "PENDING_T1")])
def test_new_policy_not_due_never_reads_exit_engine_or_minutes(asof, expected):
    def forbidden(*args, **kwargs):
        raise AssertionError("future exit input read")
    def entry_only(date, name):
        assert date <= asof and date == "20260911"
        return tables(date, name)
    row = new_row(asof, resolver=forbidden, minutes=forbidden, market=entry_only)
    assert row["validation_status"] == expected
    assert row["exit_policy_id"] == EXIT_1000_POLICY_ID
    assert row["actual_exit_price"] is row["actual_exit_time"] is row["slot_net_return"] is None


def test_new_policy_costs_shared_engine_return_once_not_daily_open():
    calls = []
    def resolver(**kwargs):
        calls.append(kwargs)
        assert kwargs["entry_price"] == 10 and kwargs["t_close_price"] == 10.5
        assert kwargs["scheduled_exit_date"] == kwargs["as_of_date"] == "20260914"
        assert kwargs["load_minutes"]("20260914") == {"test": "only minute source"}
        return settled_exit(), "SETTLED_EXIT_1000_MINUTE_PROXY"
    row = new_row(resolver=resolver, minutes=lambda date, code: {"test": "only minute source"})
    assert len(calls) == 1
    assert row["validation_status"] == "FINAL_VERIFIED_PROXY"
    assert row["actual_exit_price"] == 10.4
    assert row["actual_exit_time"] == "2026-09-14T10:00:00+08:00"
    assert row["actual_net_return"] == row["slot_net_return"] == pytest.approx(.04 - .0045)
    assert row["truth_source"] == "daily_open_entry_minute_exit_proxy"
    assert row["actual_execution_claimed"] is row["actual_order_fill_observed"] is False
    assert row["research_only"] is True and row["exit_capacity_verified"] is False


@pytest.mark.parametrize("status,expected", [
    ("PENDING_EXIT_LIMIT_UP_HELD", "PENDING_EXIT_PROXY"),
    ("PENDING_EXIT_UNSELLABLE", "UNRESOLVED_EXIT_PROXY"),
    ("PENDING_EXIT_MISSING_MINUTES", "MISSING_T1_TRUTH"),
    ("PENDING_EXIT_SOURCE_INVALID", "MISSING_T1_TRUTH"),
])
def test_new_exit_pending_never_falls_back_to_old_daily_open_or_zero(status, expected):
    row = new_row(resolver=lambda **kwargs: (None, status))
    assert row["validation_status"] == expected
    assert row["exit_validation_status"] == status
    assert row["actual_net_return"] is row["slot_net_return"] is row["actual_exit_price"] is None
    assert "actual_exit_date" not in row


@pytest.mark.parametrize("changes", [
    {"exit_policy_id": "old_open"}, {"exit_price": 0}, {"gross_return": float("nan")},
    {"gross_return": -1}, {"actual_exit_date": "20260915"},
    {"actual_exit_date": "20260911"}, {"actual_exit_time": None},
    {"actual_execution_claimed": True}, {"research_only": False},
    {"execution_bar_end": "2026-09-14T10:00:00+08:00"},
    {"decision_time": "2026-09-14T10:01:00+08:00"},
    {"actual_exit_time": "2026-09-14T10:00:00"},
    {"exit_time_semantics": "ACTUAL_TICK_FILL"},
])
def test_invalid_new_engine_result_cannot_become_observed_exit(changes):
    with pytest.raises(ValueError, match="refusing an open-price fallback"):
        new_row(resolver=lambda **kwargs: (settled_exit(**changes), "SETTLED_EXIT_1000_MINUTE_PROXY"))


def test_new_no_fill_does_not_require_minutes_or_claim_exit():
    def market(date, name):
        result = tables(date, name)
        if name == "daily":
            result["600001.SH"].update(open=11, close=11)
        return result
    def forbidden(**kwargs):
        raise AssertionError("unbought slot must not generate an exit")
    row = new_row(market=market, resolver=forbidden)
    assert row["validation_status"] == "FINAL_NO_FILL_PROXY" and row["slot_net_return"] == 0
    assert row["actual_net_return"] is row["actual_exit_time"] is None


def test_mixed_policies_keep_promotion_truth_but_never_combine_returns():
    old = observation_row(FROZEN, "20260909", "20260910", "20260911", "20260911", tables)
    new = new_row(resolver=lambda **kwargs: (settled_exit(), "SETTLED_EXIT_1000_MINUTE_PROXY"))
    new["continuation_limit_up_hit"] = 1
    stats = summarize([old, new], [day_summary(old), day_summary(new)], 7)
    assert stats["top1_continuation"] == {"samples": 2, "hits": 1, "hit_rate": .5}
    assert stats["excluded_retrospective_rows"] == 7
    assert stats["final_verified_trades"] == 2
    for key in ("final_win_rate", "mean_final_net_return", "median_final_net_return",
                "worst_final_net_return", "tail_10pct_mean_return", "profit_factor",
                "equal_slot_cumulative_return", "equal_slot_max_drawdown"):
        assert stats[key] is None
    assert stats["daily_portfolio"] == []
    assert stats["portfolio_curve_reason"] == "MIXED_EXIT_POLICIES_RETURNS_NOT_COMBINED"
    policies = stats["return_statistics_by_exit_policy"]
    assert set(policies) == {LEGACY_EXIT_POLICY_ID, EXIT_1000_POLICY_ID}
    assert policies[LEGACY_EXIT_POLICY_ID]["mean_final_net_return"] == old["actual_net_return"]
    assert policies[EXIT_1000_POLICY_ID]["mean_final_net_return"] == new["actual_net_return"]
    assert all(p["mean_final_net_return"] is None and p["win_rate"] is None
               for p in stats["path_performance"].values())


def test_held_limit_up_is_pending_not_missing_or_capital_nav():
    row = new_row(resolver=lambda **kwargs: (None, "PENDING_EXIT_LIMIT_UP_HELD"))
    stats = summarize([row], [day_summary(row)], 0)
    assert stats["pending_exit_rows"] == stats["pending_t1_rows"] == 1
    assert stats["missing_t1_truth_rows"] == stats["unresolved_exit_rows"] == 0
    assert stats["final_verified_trades"] == 0
    assert stats["equal_slot_cumulative_return"] is stats["mean_final_net_return"] is None
    assert stats["portfolio_curve_reason"] == "DELAYED_EXIT_CAPITAL_OVERLAP_NOT_MODELED"


def minute_fixture(day="20260914", code="600001.SH", *, sealed=False):
    from top10decision.decision.shadow_exit_minute_truth import expected_bar_ends
    price = 11 if sealed else 10.2
    rows = [dict(ts_code=code, trade_time=stamp, open=price, high=price, low=price,
                 close=price, vol=100, amount=1000) for stamp in expected_bar_ends(day)]
    if not sealed:
        rows[30].update(open=10.4, high=10.4, low=10.4, close=10.4)
    daily = dict(ts_code=code, trade_date=day, open=price, high=11 if sealed else 10.4,
                 low=price, close=price, pre_close=10.5, vol=24000)
    limits = dict(ts_code=code, trade_date=day, up_limit=11, down_limit=9)
    return rows, daily, limits


def test_real_shared_engine_uses_next_bar_price_and_full_timezone():
    from top10decision.decision.shadow_exit_minute_truth import SCHEMA, normalize_source_rows
    raw, daily, limits = minute_fixture()
    bars, _ = normalize_source_rows(raw, "20260914", "600001.SH")
    envelope = dict(schema_version=SCHEMA, trade_date="20260914", ts_code="600001.SH",
                    timezone="Asia/Shanghai", timestamp_semantics="BAR_END", interval_seconds=60,
                    complete_session=True, rows=bars,
                    source_files=[{"path": "test/minutes.csv", "sha256": "a" * 64}])
    def market(date, name):
        return {"600001.SH": daily if name == "daily" else limits} if date == "20260914" else tables(date, name)
    row = new_row(market=market, minutes=lambda date, code: envelope)
    assert row["actual_exit_price"] == 10.4 != daily["open"]
    assert row["actual_net_return"] == pytest.approx(.04 - .0045)
    assert row["decision_time"] == row["actual_exit_time"] == "2026-09-14T10:00:00+08:00"
    assert row["execution_bar_end"] == "2026-09-14T10:01:00+08:00"
    assert row["minute_source_files"] == envelope["source_files"]


def _write_projection_fixture(tmp_path, monkeypatch, *, sealed=False, minute_mode="valid"):
    """Synthetic only: keep production frozen contracts and ledger files untouched."""
    from top10decision.decision.shadow_exit_minute_truth import minute_paths, source_bytes
    monkeypatch.setattr(projection, "_strict_open_dates", lambda root: ["20260910", "20260911", "20260914", "20260915"])
    monkeypatch.setattr(projection, "build_primary_d_runtime_index", lambda *args, **kwargs: {"fixture": True})
    base = tmp_path / "outputs/decision"
    base.mkdir(parents=True)
    receipt = {"inputs": {"calendar": {"sha256": projection.CALENDAR_SHA256}},
               "generation_mode": "NATURAL", "prospective": True, "forward_eligible": True,
               "not_forward_generated": False}
    (base / "primary_d_receipt_20260910.json").write_text(json.dumps(receipt))
    contract = dict(exec_date="20260911", exit_date="20260914", top10_count=1, rows=[FROZEN],
                    generated_at_utc="2026-09-10T10:00:00Z")
    (base / "three_rank_top10_20260910.json").write_text(json.dumps(contract))
    (base / "primary_d_runtime_features_20260910.csv").write_text("ts_code,path_label\n600001.SH,test path\n")
    config = tmp_path / EXIT_POLICY_CONFIG_PATH
    config.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / EXIT_POLICY_CONFIG_PATH, config)
    raw_minutes, exit_daily, exit_limit = minute_fixture(sealed=sealed)
    for date in ("20260911", "20260914"):
        directory = tmp_path / "data/market/raw/2026" / date
        directory.mkdir(parents=True)
        for name in ("daily", "stk_limit"):
            row = (exit_daily if name == "daily" else exit_limit) if date == "20260914" else dict(
                tables(date, name)["600001.SH"], ts_code="600001.SH", trade_date=date)
            with (directory / f"{name}.csv").open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)
    if minute_mode != "missing":
        raw, meta = source_bytes(raw_minutes, "20260914", "600001.SH", fetched_at_utc="2026-09-14T07:10:00Z")
        path, meta_path = minute_paths(tmp_path, "20260914", "600001.SH")
        path.parent.mkdir(parents=True)
        path.write_bytes(raw + (b"\n" if minute_mode == "corrupt" else b""))
        meta_path.write_bytes(meta)
    return tmp_path


def test_versioned_build_binds_minutes_metadata_policy_and_not_legacy_fallback(tmp_path, monkeypatch):
    _write_projection_fixture(tmp_path, monkeypatch)
    payload, rows = build(tmp_path, "20260914")
    assert payload["schema_version"] == VERSIONED_SCHEMA and payload["policy"]["id"] == VERSIONED_POLICY_ID
    assert payload["policy"]["mixed_exit_returns_combined"] is False
    assert payload["policy"]["exit_policy_effective_scheduled_date"] == "20260914"
    assert payload["policy"]["return_strategy_forward_evidence"] is False
    paths = {item["path"]: item["sha256"] for item in payload["source_files"]}
    assert set(paths) == {
        "data/market/raw/2026/20260911/daily.csv", "data/market/raw/2026/20260911/stk_limit.csv",
        "data/market/raw/2026/20260914/daily.csv", "data/market/raw/2026/20260914/stk_limit.csv",
        "data/market/exit_1000_1m/2026/20260914/600001_SH.csv",
        "data/market/exit_1000_1m/2026/20260914/600001_SH.meta.json", EXIT_POLICY_CONFIG_PATH,
    }
    assert all(sha == hashlib.sha256((tmp_path / path).read_bytes()).hexdigest() for path, sha in paths.items())
    assert payload["policy"]["exit_policy_config"] == {"path": EXIT_POLICY_CONFIG_PATH, "sha256": paths[EXIT_POLICY_CONFIG_PATH]}
    assert rows[0]["actual_exit_price"] == 10.4 and rows[0]["slot_net_return"] == pytest.approx(.04 - .0045)
    assert plan_exit_minute_requests(tmp_path, "20260914") == []
    assert not (tmp_path / "outputs/decision/primary_observation").exists()


@pytest.mark.parametrize("mode", ["missing", "corrupt"])
def test_build_missing_or_corrupt_minutes_stay_unsettled_and_request_exact_stock(tmp_path, monkeypatch, mode):
    _write_projection_fixture(tmp_path, monkeypatch, minute_mode=mode)
    payload, rows = build(tmp_path, "20260914")
    assert payload["status"] == "PARTIAL_TRUTH"
    assert rows[0]["validation_status"] == "MISSING_T1_TRUTH"
    assert rows[0]["actual_net_return"] is rows[0]["slot_net_return"] is None
    assert plan_exit_minute_requests(tmp_path, "20260914") == [{"trade_date": "20260914", "ts_code": "600001.SH"}]


def test_build_sealed_limit_is_normal_pending_with_bound_minute_sources(tmp_path, monkeypatch):
    _write_projection_fixture(tmp_path, monkeypatch, sealed=True)
    payload, rows = build(tmp_path, "20260914")
    assert payload["status"] == "PENDING_DATES"
    assert payload["statistics"]["pending_exit_rows"] == payload["daily_summaries"][0]["pending_exit_rows"] == 1
    assert payload["statistics"]["missing_t1_truth_rows"] == 0
    assert rows[0]["validation_status"] == "PENDING_EXIT_PROXY"
    assert len([item for item in payload["source_files"] if "/exit_1000_1m/" in item["path"]]) == 2


@pytest.mark.parametrize("tamper", ["missing_meta", "wrong_sha", "outside_path", "parent_symlink"])
def test_projection_defends_exact_minute_pair_against_binding_or_path_tamper(tmp_path, monkeypatch, tamper):
    from top10decision.decision import shadow_exit_minute_truth as minute_truth
    _write_projection_fixture(tmp_path, monkeypatch)
    envelope = minute_truth.load_exit_minutes(tmp_path, "20260914", "600001.SH")
    if tamper == "missing_meta":
        envelope["source_files"].pop()
    elif tamper == "wrong_sha":
        envelope["source_files"][-1]["sha256"] = "0" * 64
    elif tamper == "outside_path":
        envelope["source_files"][-1]["path"] = "../outside.meta.json"
    else:
        original = tmp_path / "data/market/exit_1000_1m/2026/20260914"
        moved = tmp_path / "moved-fixture"
        original.rename(moved)
        original.symlink_to(moved, target_is_directory=True)
    monkeypatch.setattr(minute_truth, "load_exit_minutes", lambda *args: envelope)
    payload, rows = build(tmp_path, "20260914")
    assert rows[0]["validation_status"] == "MISSING_T1_TRUTH"
    assert rows[0]["actual_net_return"] is rows[0]["slot_net_return"] is None
    assert not any("/exit_1000_1m/" in item["path"] for item in payload["source_files"])


def test_request_plan_keeps_missing_intermediate_daily_and_limit_days(tmp_path, monkeypatch):
    _write_projection_fixture(tmp_path, monkeypatch, minute_mode="missing")
    directory = tmp_path / "data/market/raw/2026/20260914"
    for path in directory.iterdir():
        path.unlink()
    assert plan_exit_minute_requests(tmp_path, "20260915") == [
        {"trade_date": "20260914", "ts_code": "600001.SH"},
        {"trade_date": "20260915", "ts_code": "600001.SH"},
    ]


@pytest.mark.parametrize("changes", [
    {"policy_id": "old"}, {"effective_scheduled_exit_date": "20260911"},
    {"ordinary_exit_decision_time": "09:45:00"}, {"actual_execution_claimed": True},
    {"missing_minute_truth": "USE_OPEN"},
])
def test_versioned_build_rejects_exit_policy_drift(tmp_path, monkeypatch, changes):
    _write_projection_fixture(tmp_path, monkeypatch)
    config = tmp_path / EXIT_POLICY_CONFIG_PATH
    config.write_text(json.dumps(dict(json.loads(config.read_text()), **changes)))
    with pytest.raises(ValueError, match="exit policy configuration"):
        build(tmp_path, "20260914")


def test_request_plan_only_new_filled_unfinished_positions_exact_sessions(tmp_path, monkeypatch):
    old = dict(exit_policy_id=LEGACY_EXIT_POLICY_ID, proxy_fill=1, validation_status="UNRESOLVED_EXIT_PROXY",
               exit_date="20260911", ts_code="600001.SH")
    pending = dict(old, exit_policy_id=EXIT_1000_POLICY_ID, exit_date="20260914")
    rows = [old, pending, dict(pending), dict(pending, proxy_fill=0),
            dict(pending, ts_code="600002.SH", validation_status="FINAL_VERIFIED_PROXY"),
            dict(pending, ts_code="600003.SH", proxy_fill=None),
            dict(pending, ts_code="600004.SH", exit_date="20260916")]
    monkeypatch.setattr(projection, "build", lambda root, asof: ({}, rows))
    monkeypatch.setattr(projection, "_strict_open_dates", lambda root: ["20260911", "20260914", "20260915", "20260916"])
    assert plan_exit_minute_requests(tmp_path, "20260915") == [
        {"trade_date": "20260914", "ts_code": "600001.SH"},
        {"trade_date": "20260915", "ts_code": "600001.SH"},
    ]
