"""Synthetic-only mathematical fixtures; no real source/forward admission."""
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
import socket

import pandas as pd
import pytest

from top10decision.decision.d_close_features import compute_d_close_features
from work.profit_1000_upgrade import candidate_live_feature_math as m

D, CODE = "20260914", "600000.SH"


def events():
    # Counts reproduce the six audit examples, not the actual source records.
    specifications = [("603421.SH", 2, 7), ("002912.SZ", 2, 3), ("000993.SZ", 3, 6),
                      ("002201.SZ", 3, 6), ("000823.SZ", 1, 2)]
    rows = []
    for code, hits, samples in specifications:
        for i in range(samples):
            rows.append({"signal_date": f"202501{i+1:02d}", "ts_code": code,
                         "promotion_hit": int(i < hits)})
    return rows


def bars(count=35):
    rows = []; previous = 10.
    # Calendar dates here are synthetic; the kernel never certifies SSE dates.
    for i in range(count):
        day = (date(2026, 6, 1) + timedelta(days=i)).strftime("%Y%m%d")
        close = previous * (1. + .001 * (1 + i % 7))
        rows.append({"trade_date": day, "ts_code": CODE, "close": close, "volume": 1000. + i * 10.})
        previous = close
    return rows


def daily(rows, **kw):
    return m.compute_daily_features(rows, signal_date=kw.pop("signal_date", rows[-1]["trade_date"]),
                                    ts_code=CODE, **kw)


@pytest.mark.parametrize("code,hits,n,expected", [("603421.SH", 2, 7, 1/3), ("002912.SZ", 2, 3, .5),
    ("000993.SZ", 3, 6, 5/11), ("002201.SZ", 3, 6, 5/11), ("000823.SZ", 1, 2, 3/7),
    ("600876.SH", 0, 0, .4)])
def test_synthetic_counts_reproduce_six_historical_beta_values(code, hits, n, expected):
    source = events(); before = deepcopy(source)
    result = m.compute_stock_prior(source, signal_date=D, ts_code=code)
    assert result["features"]["five_year_stock_prior_rate"] == expected
    assert (result["prior_hits"], result["prior_known_samples"]) == (hits, n)
    assert result["source_independently_verified"] is result["source_completeness_verified"] is False
    assert result["production_activation_allowed"] is False and source == before


def test_cold_empty_source_is_mathematics_not_proof_of_complete_history():
    result = m.compute_stock_prior([], signal_date=D, ts_code=CODE)
    assert result["features"]["five_year_stock_prior_rate"] == .4
    assert result["observed_history_end"] is result["source_age_calendar_days"] is None
    assert result["cold_prior_applied"] is True and result["source_authority_issued"] is False


def test_unknown_truth_is_not_counted_as_loss_or_prior_observation():
    rows = [{"signal_date": "20260813", "ts_code": CODE, "promotion_hit": 1},
            {"signal_date": "20260814", "ts_code": CODE, "promotion_hit": None}]
    result = m.compute_stock_prior(rows, signal_date=D, ts_code=CODE)
    assert result["features"]["five_year_stock_prior_rate"] == .5
    assert (result["prior_known_samples"], result["prior_unknown_samples"]) == (1, 1)
    assert result["observed_history_end"] == m.FIXED_HISTORY_CEILING
    assert result["source_age_calendar_days"] == 31
    assert result["fixed_source_predates_target_D"] is True and result["source_extended_or_refreshed"] is False


@pytest.mark.parametrize("bad_day", ["20260914", "20260915", "20260815", "20260230", "bad"])
def test_all_history_dates_checked_before_any_outcome(bad_day, monkeypatch):
    rows = events(); rows[0]["promotion_hit"] = object()
    rows.append({"signal_date": bad_day, "ts_code": CODE, "promotion_hit": object()})
    def forbidden(*a, **kw): raise AssertionError("OUTCOME_READ_BEFORE_ALL_IDENTITIES")
    monkeypatch.setattr(m, "_number", forbidden)
    with pytest.raises(ValueError): m.compute_stock_prior(rows, signal_date=D, ts_code=CODE)


def test_same_day_history_before_holdout_is_still_rejected_before_truth(monkeypatch):
    rows = [{"signal_date": "20260814", "ts_code": CODE, "promotion_hit": object()}]
    monkeypatch.setattr(m, "_number", lambda *a, **kw: pytest.fail("SAME_D_OUTCOME_READ"))
    with pytest.raises(ValueError, match="STRICT_PRIOR"):
        m.compute_stock_prior(rows, signal_date="20260814", ts_code=CODE)


@pytest.mark.parametrize("value", [True, "1", .5, -1, 2, float("nan"), float("inf"), object()])
def test_unknown_or_invalid_outcomes_are_not_coerced(value):
    source = [{"signal_date": "20260814", "ts_code": CODE, "promotion_hit": value}]
    with pytest.raises(ValueError): m.compute_stock_prior(source, signal_date=D, ts_code=CODE)


@pytest.mark.parametrize("kind", ["duplicate", "missing_truth", "bad_code", "nonlist", "row_subclass"])
def test_history_identity_and_shape_fail_closed(kind):
    rows = events()
    if kind == "duplicate": rows.append(rows[0].copy())
    if kind == "missing_truth": rows[0].pop("promotion_hit")
    if kind == "bad_code": rows[0]["ts_code"] = "600000"
    if kind == "nonlist": rows = tuple(rows)
    if kind == "row_subclass": rows[0] = type("CustomDict", (dict,), {})(rows[0])
    with pytest.raises(ValueError): m.compute_stock_prior(rows, signal_date=D, ts_code=CODE)


def test_original_shared_daily_math_is_used_and_all_seven_values_match():
    source = bars(); before = deepcopy(source)
    expected = compute_d_close_features(pd.DataFrame(source)).iloc[-1]
    result = daily(source)
    assert m.historical.compute_d_close_features is compute_d_close_features
    for key in m.DAILY_FEATURES[1:]: assert result["features"][key] == float(expected[key])
    previous, current = source[-2]["close"], source[-1]["close"]
    assert result["features"]["d_pct_change"] == 100.*(current-previous)/previous
    assert source == before and result["observed_window_requirement_met"] is True
    assert result["training_window_equivalence_verified"] is False
    assert result["price_provider_or_adjustment_verified"] is False


def test_suspension_gap_with_earlier_observations_does_not_shorten_window():
    source = bars(); day = source[-1]["trade_date"]
    calendar_window = {r["trade_date"] for r in source[-21:]}
    missing = source[-12]["trade_date"]
    source = [r for r in source if r["trade_date"] != missing]
    truncated = [r for r in source if r["trade_date"] in calendar_window]
    full_result = daily(source)
    assert full_result["observed_window_requirement_met"] is True
    assert full_result["features"]["volatility_20d"] == float(compute_d_close_features(pd.DataFrame(source)).iloc[-1].volatility_20d)
    assert full_result["features"]["volatility_20d"] != float(compute_d_close_features(pd.DataFrame(truncated)).iloc[-1].volatility_20d)
    short = m.compute_daily_features(truncated, signal_date=day, ts_code=CODE)
    assert short["status"] == "INSUFFICIENT_OBSERVED_HISTORY"
    assert all(value is None for value in short["features"].values())


def test_ex_right_preclose_ignored_and_raw_historical_pct_not_discontinuity_masked():
    source = bars(21); source[-1]["close"] = source[-2]["close"] * .5
    for row in source: row["pre_close"] = object(); row["pct_chg"] = object()
    result = daily(source)
    assert result["features"]["d_pct_change"] == -50.
    assert result["supplied_pre_close_used"] is False
    expected = compute_d_close_features(pd.DataFrame([{k:r[k] for k in ('trade_date','close','volume')} for r in source])).iloc[-1]
    assert pd.isna(expected.returns_1d)  # Mask is for rolling returns, not d_pct_change.
    assert result["features"]["volatility_5d"] == float(expected.volatility_5d)


@pytest.mark.parametrize("count", [0, 1, 2, 10, 20])
def test_short_observed_history_is_explicit_incompatibility(count):
    source = bars(count)
    result = m.compute_daily_features(source, signal_date=source[-1]['trade_date'] if source else D, ts_code=CODE)
    assert result["status"] == ("INSUFFICIENT_OBSERVED_HISTORY" if count else "MISSING_D_OBSERVATION")
    assert not result["observed_window_requirement_met"] and set(result["features"].values()) == {None}


def test_missing_D_does_not_reuse_previous_observation():
    source = bars(); day = source[-1]["trade_date"]
    result = daily(source[:-1], signal_date=day)
    assert result["status"] == "MISSING_D_OBSERVATION" and result["D_observation_present"] is False
    assert all(v is None for v in result["features"].values())


@pytest.mark.parametrize("value", [0, -1, None, True, "10", float("nan"), float("inf"), object()])
def test_invalid_close_is_not_silently_dropped(value):
    source = bars(); source[0]["close"] = value
    with pytest.raises(ValueError): daily(source)


@pytest.mark.parametrize("value", [-1, True, "100", float("inf")])
def test_bad_volume_rejected(value):
    source = bars(); source[0]["volume"] = value
    with pytest.raises(ValueError): daily(source)


def test_explicit_missing_volume_keeps_prices_and_does_not_invent_volume():
    source = bars(); source[-1]["volume"] = None
    result = daily(source)
    assert result["features"]["volume_ratio"] is None
    assert result["features"]["ret_10d"] is not None and result["features"]["d_pct_change"] is not None


def test_zero_prior_volume_mean_is_canonical_NA_not_zero_ratio():
    source = bars();
    for row in source[-6:-1]: row["volume"] = 0
    assert daily(source)["features"]["volume_ratio"] is None


def test_discontinuities_keep_raw_momentum_but_can_null_all_rolling_volatility():
    source = bars(21)
    for i, row in enumerate(source): row["close"] = float(2 ** i)
    result = daily(source)
    assert result["features"]["d_pct_change"] == 100.
    assert result["features"]["ret_2d"] == 3.
    assert result["features"]["volatility_5d"] is result["features"]["volatility_20d"] is None
    assert result["observed_window_requirement_met"] is True  # Not a promise that every feature is known.
    assert result["training_window_equivalence_verified"] is False


def test_same_valid_observations_can_arrive_unsorted_without_changing_features():
    source = bars(); expected = daily(source); day = source[-1]["trade_date"]
    source.reverse()
    assert daily(source, signal_date=day) == expected


def test_fixed_prior_and_observed_window_contracts_cannot_be_overridden():
    with pytest.raises(TypeError):
        m.compute_stock_prior([], signal_date=D, ts_code=CODE, history_ceiling="20260913")
    with pytest.raises(TypeError):
        m.compute_stock_prior([], signal_date=D, ts_code=CODE, beta=(0, 0))
    with pytest.raises(TypeError): daily(bars(), required_observed_bars=10)


def test_shared_math_definition_drift_is_not_silently_accepted(monkeypatch):
    monkeypatch.setattr(m.historical, "D_CLOSE_DISCONTINUITY_LIMIT", .2)
    with pytest.raises(ValueError, match="CONTRACT_CHANGED"): daily(bars())


@pytest.mark.parametrize("kind", ["future", "mixed_code", "duplicate", "invalid_date", "missing_volume"])
def test_bar_identity_and_required_fields(kind, monkeypatch):
    source = bars(); day = source[-1]["trade_date"]
    if kind == "future": source[-1]["trade_date"] = "20260915"
    if kind == "mixed_code": source[-1]["ts_code"] = "600001.SH"
    if kind == "duplicate": source[-1] = source[0].copy()
    if kind == "invalid_date": source[-1]["trade_date"] = "20260230"
    if kind == "missing_volume": source[-1].pop("volume")
    if kind != "missing_volume":
        source[0]["close"] = object()
        monkeypatch.setattr(m, "_number", lambda *a, **kw: pytest.fail("PRICE_READ_BEFORE_ALL_IDENTITIES"))
    with pytest.raises(ValueError): m.compute_daily_features(source, signal_date=day, ts_code=CODE)


def test_unknown_fields_are_not_read_hashed_or_copied():
    class Trap:
        def __repr__(self): raise AssertionError("UNKNOWN_REPR")
        def __deepcopy__(self, memo): raise AssertionError("UNKNOWN_COPY")
        def __float__(self): raise AssertionError("UNKNOWN_FLOAT")
        def __iter__(self): raise AssertionError("UNKNOWN_ITER")
    source, history = bars(), events()
    expected_daily, expected_prior = daily(source), m.compute_stock_prior(history, signal_date=D, ts_code=CODE)
    for row in source + history:
        row.update(future_outcome=Trap(), net_return=Trap(), P0_rank=Trap(), pre_close=Trap())
    assert daily(source) == expected_daily
    assert m.compute_stock_prior(history, signal_date=D, ts_code=CODE) == expected_prior


def test_no_file_network_or_fit_used(monkeypatch):
    from work.profit_1000_upgrade import candidate
    source, history = bars(), events()
    def forbidden(*a, **kw): raise AssertionError("NO_IO_FIT_OR_SOURCE_ACCESS")
    with monkeypatch.context() as isolated:
        isolated.setattr(Path, "open", forbidden); isolated.setattr(socket, "socket", forbidden)
        isolated.setattr(candidate, "_fit_ridge", forbidden)
        outputs = (daily(source), m.compute_stock_prior(history, signal_date=D, ts_code=CODE))
    for result in outputs:
        assert result["files_written"] == result["network_calls_performed"] == 0
        assert result["model_training_performed"] is result["P0_rows_or_ranks_changed"] is False


def test_mutated_bar_source_during_shared_math_is_rejected(monkeypatch):
    source = bars(); original = m.historical.compute_d_close_features
    def mutate(frame, **kw):
        result = original(frame, **kw); source[0]["close"] *= 1.01; return result
    monkeypatch.setattr(m.historical, "compute_d_close_features", mutate)
    with pytest.raises(ValueError, match="INPUT_CHANGED"): daily(source)


def test_mutated_history_between_projection_and_completion_is_rejected(monkeypatch):
    source = events(); original = m._history_projection; calls = 0
    def mutate(rows, day):
        nonlocal calls
        result = original(rows, day); calls += 1
        if calls == 1: rows[0]["promotion_hit"] = 0
        return result
    monkeypatch.setattr(m, "_history_projection", mutate)
    with pytest.raises(ValueError, match="INPUT_CHANGED"):
        m.compute_stock_prior(source, signal_date=D, ts_code=CODE)
