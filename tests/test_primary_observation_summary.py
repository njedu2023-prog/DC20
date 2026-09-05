from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.settle_primary_observations import build, observation_row, summarize

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
