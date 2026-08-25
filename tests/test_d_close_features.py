from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from scripts import build_three_engine_five_year_ledger as ledger_builder
from top10decision.auction_v3.config import AuctionV3Config
from top10decision.auction_v3.engine import AuctionV3Engine
from top10decision.decision import d_close_features
from top10decision.decision.d_close_features import (
    D_CLOSE_FEATURE_COLUMNS,
    compute_d_close_features,
)
from top10decision.decision.three_engine_models import (
    three_engine_d_close_market_features,
)


CODE = "600000.SH"


def _bars(count: int = 25) -> tuple[pd.DataFrame, list[str]]:
    dates = [
        date.strftime("%Y%m%d")
        for date in pd.bdate_range("2026-07-20", periods=count)
    ]
    rows: list[dict[str, object]] = []
    previous = 10.0
    for index, trade_date in enumerate(dates):
        close = previous * (1.001 + (index % 4) * 0.0004)
        open_price = previous * (1.0005 - (index % 3) * 0.0001)
        rows.append(
            {
                "ts_code": CODE,
                "trade_date": trade_date,
                "open": open_price,
                "close": close,
                "high": max(open_price, close) * 1.003,
                "low": min(open_price, close) * 0.997,
                "pre_close": previous,
                "volume": 1_000.0 + index * 17.0,
            }
        )
        previous = close
    return pd.DataFrame(rows), dates


def _engine_features(
    tmp_path: Path,
    bars: pd.DataFrame,
    calendar: list[str],
    signal_date: str,
) -> dict[str, float]:
    engine = AuctionV3Engine(AuctionV3Config(root=tmp_path))
    for trade_date, group in bars.groupby("trade_date", sort=False):
        daily = group.copy().rename(columns={"volume": "vol"})
        engine._market_cache[(str(trade_date), "daily")] = daily.set_index(
            "ts_code", drop=False
        )
    return three_engine_d_close_market_features(
        engine,
        signal_date,
        CODE,
        calendar,
    )


def _builder_features(bars: pd.DataFrame, signal_date: str) -> dict[str, float]:
    relevant = pd.DataFrame(
        [{"ts_code": CODE, "signal_date": signal_date}]
    )
    frame = ledger_builder._attach_d_close_history_features(bars, relevant)
    assert len(frame) == 1
    return {name: frame.iloc[0][name] for name in D_CLOSE_FEATURE_COLUMNS}


def _canonical_features(bars: pd.DataFrame, signal_date: str) -> dict[str, float]:
    frame = compute_d_close_features(bars, cutoff_date=signal_date)
    assert not frame.empty
    assert frame.iloc[-1]["trade_date"] == signal_date
    return {name: frame.iloc[-1][name] for name in D_CLOSE_FEATURE_COLUMNS}


def _assert_exact_parity(
    tmp_path: Path,
    bars: pd.DataFrame,
    calendar: list[str],
    signal_date: str,
) -> dict[str, float]:
    expected = _canonical_features(bars, signal_date)
    builder = _builder_features(bars, signal_date)
    runtime = _engine_features(tmp_path, bars, calendar, signal_date)
    for name in D_CLOSE_FEATURE_COLUMNS:
        if pd.isna(expected[name]):
            assert pd.isna(builder[name]), name
            assert pd.isna(runtime[name]), name
        else:
            assert float(builder[name]) == float(expected[name]), name
            assert float(runtime[name]) == float(expected[name]), name
    return expected


def test_builder_and_runtime_reference_one_canonical_function() -> None:
    assert ledger_builder.compute_d_close_features is compute_d_close_features
    assert ledger_builder.RUNTIME_ALIGNED_FEATURE_COLUMNS == (
        D_CLOSE_FEATURE_COLUMNS
    )


def test_normal_21_observed_bars_match_registered_math(tmp_path: Path) -> None:
    bars, calendar = _bars()
    signal_date = calendar[20]
    result = _assert_exact_parity(tmp_path, bars, calendar, signal_date)
    observed = bars[bars["trade_date"].le(signal_date)].tail(21)
    current = observed.iloc[-1]
    previous = observed.iloc[-2]
    assert np.isclose(
        result["returns_1d"],
        100.0 * (current["close"] / previous["close"] - 1.0),
    )
    assert np.isclose(
        result["high_low_range"],
        (current["high"] - current["low"]) / previous["close"],
    )
    assert np.isclose(
        result["volume_ratio"],
        current["volume"] / observed.iloc[-6:-1]["volume"].mean(),
    )
    assert all(not pd.isna(result[name]) for name in D_CLOSE_FEATURE_COLUMNS)


def test_suspension_gap_uses_preceding_observed_bars(tmp_path: Path) -> None:
    bars, calendar = _bars()
    signal_date = calendar[20]
    suspended_date = calendar[9]
    bars = bars[bars["trade_date"].ne(suspended_date)].copy()
    result = _assert_exact_parity(tmp_path, bars, calendar, signal_date)
    observed = bars[bars["trade_date"].le(signal_date)]
    assert np.isclose(
        result["ret_10d"],
        observed.iloc[-1]["close"] / observed.iloc[-11]["close"] - 1.0,
    )


def test_signal_day_suspension_emits_no_d_feature_snapshot(tmp_path: Path) -> None:
    bars, calendar = _bars()
    signal_date = calendar[20]
    suspended = bars[bars["trade_date"].ne(signal_date)].copy()
    relevant = pd.DataFrame(
        [{"ts_code": CODE, "signal_date": signal_date}]
    )
    assert ledger_builder._attach_d_close_history_features(
        suspended, relevant
    ).empty
    runtime = _engine_features(tmp_path, suspended, calendar, signal_date)
    assert all(pd.isna(runtime[name]) for name in D_CLOSE_FEATURE_COLUMNS)


def test_price_discontinuity_is_null_in_return_windows(tmp_path: Path) -> None:
    bars, calendar = _bars()
    break_index = 17
    bars.loc[break_index, "close"] = bars.loc[break_index - 1, "close"] * 1.30
    bars.loc[break_index, "high"] = bars.loc[break_index, "close"] * 1.001
    signal_date = calendar[20]
    _assert_exact_parity(tmp_path, bars, calendar, signal_date)
    features = compute_d_close_features(
        bars,
        cutoff_date=signal_date,
    ).set_index("trade_date")
    assert math.isnan(float(features.at[calendar[break_index], "returns_1d"]))
    assert math.isnan(float(features.at[calendar[break_index + 1], "returns_1d"]))


def test_missing_current_volume_only_nulls_volume_features(tmp_path: Path) -> None:
    bars, calendar = _bars()
    signal_date = calendar[20]
    bars.loc[bars["trade_date"].eq(signal_date), "volume"] = np.nan
    result = _assert_exact_parity(tmp_path, bars, calendar, signal_date)
    assert pd.isna(result["vol"])
    assert pd.isna(result["volume_ratio"])
    assert not pd.isna(result["returns_1d"])
    assert not pd.isna(result["atr"])


def test_minimum_history_windows_fail_individually_closed(tmp_path: Path) -> None:
    bars, calendar = _bars(count=2)
    signal_date = calendar[-1]
    result = _assert_exact_parity(tmp_path, bars, calendar, signal_date)
    assert not pd.isna(result["returns_1d"])
    assert not pd.isna(result["high_low_range"])
    for name in (
        "volume_ratio",
        "volatility_5d",
        "volatility_10d",
        "volatility_20d",
        "atr",
        "ret_2d",
        "ret_5d",
        "ret_10d",
    ):
        assert pd.isna(result[name]), name


def test_duplicate_day_keeps_last_and_future_rows_cannot_cross_d(
    tmp_path: Path,
) -> None:
    bars, calendar = _bars()
    signal_date = calendar[20]
    duplicate = bars[bars["trade_date"].eq(signal_date)].copy()
    duplicate["open"] *= 1.001
    duplicate["close"] *= 1.002
    duplicate["high"] = duplicate[["open", "close"]].max(axis=1) * 1.003
    duplicate["low"] = duplicate[["open", "close"]].min(axis=1) * 0.997
    duplicate["volume"] *= 2.0
    with_duplicate = pd.concat([bars, duplicate], ignore_index=True)
    result = _assert_exact_parity(
        tmp_path,
        with_duplicate,
        [*calendar, signal_date],
        signal_date,
    )
    previous_close = bars[bars["trade_date"].lt(signal_date)].iloc[-1]["close"]
    assert np.isclose(
        result["returns_1d"],
        100.0 * (duplicate.iloc[0]["close"] / previous_close - 1.0),
    )

    changed_future = with_duplicate.copy()
    future = changed_future["trade_date"].gt(signal_date)
    changed_future.loc[
        future, ["open", "close", "high", "low", "volume"]
    ] *= 99.0
    before = _assert_exact_parity(
        tmp_path / "before_future_mutation",
        with_duplicate,
        [*calendar, signal_date],
        signal_date,
    )
    after = _assert_exact_parity(
        tmp_path / "after_future_mutation",
        changed_future,
        [*calendar, signal_date],
        signal_date,
    )
    for name in D_CLOSE_FEATURE_COLUMNS:
        if pd.isna(before[name]):
            assert pd.isna(after[name])
        else:
            assert float(after[name]) == float(before[name]), name
