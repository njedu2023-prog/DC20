from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


D_CLOSE_FEATURE_CONTRACT_VERSION = "dc20_daily_candidate_d_close_v1"
D_CLOSE_FEATURE_COLUMNS = (
    "returns_1d",
    "high_low_range",
    "candle_body",
    "gap_open",
    "vol",
    "volume_ratio",
    "volatility_5d",
    "volatility_10d",
    "volatility_20d",
    "atr",
    "ret_2d",
    "ret_5d",
    "ret_10d",
    "bid_ask_proxy",
    "spread_proxy",
)
D_CLOSE_MAX_HISTORY_BARS = 21
D_CLOSE_DISCONTINUITY_LIMIT = 0.125


def _normal_date(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def empty_d_close_feature_values() -> dict[str, float]:
    return {name: math.nan for name in D_CLOSE_FEATURE_COLUMNS}


def compute_d_close_features(
    bars: pd.DataFrame,
    *,
    cutoff_date: str | None = None,
) -> pd.DataFrame:
    """Return the canonical 15 D-close features for each observed bar.

    Rows are stock-local observed daily bars. Duplicate dates use their last
    occurrence, missing sessions are not synthesized, and no row later than
    ``cutoff_date`` can affect the result. A close-to-close move above 12.5%
    in absolute value is treated as a price discontinuity for return and
    volatility features, matching the five-year ledger contract.
    """

    columns = ["trade_date", *D_CLOSE_FEATURE_COLUMNS]
    if bars.empty:
        return pd.DataFrame(columns=columns)
    if "trade_date" not in bars.columns or "close" not in bars.columns:
        raise ValueError("D-close bars require trade_date and close")

    frame = bars.copy()
    frame["_source_order"] = np.arange(len(frame), dtype=int)
    frame["trade_date"] = frame["trade_date"].map(_normal_date)
    frame = frame[frame["trade_date"].str.fullmatch(r"\d{8}")].copy()
    cutoff = _normal_date(cutoff_date) if cutoff_date is not None else ""
    if cutoff_date is not None and not cutoff:
        raise ValueError("D-close cutoff_date is invalid")
    if cutoff:
        frame = frame[frame["trade_date"].le(cutoff)].copy()
    for name in ("open", "close", "high", "low", "volume"):
        if name not in frame.columns:
            frame[name] = math.nan
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    frame = frame[
        frame["close"].notna()
        & np.isfinite(frame["close"])
        & frame["close"].gt(0.0)
    ].copy()
    frame = (
        frame.sort_values(["trade_date", "_source_order"], kind="stable")
        .drop_duplicates("trade_date", keep="last")
        .reset_index(drop=True)
    )
    if frame.empty:
        return pd.DataFrame(columns=columns)

    close = frame["close"]
    open_price = frame["open"]
    high = frame["high"]
    low = frame["low"]
    volume = frame["volume"]
    previous_close = close.shift(1)
    valid_previous_close = previous_close.where(previous_close.gt(0.0))
    safe_close_return = close.div(valid_previous_close).sub(1.0)
    safe_close_return = safe_close_return.where(
        safe_close_return.abs().le(D_CLOSE_DISCONTINUITY_LIMIT)
    )

    features = pd.DataFrame({"trade_date": frame["trade_date"]})
    features["returns_1d"] = safe_close_return.mul(100.0)
    price_range = high.sub(low)
    features["high_low_range"] = price_range.div(valid_previous_close)
    features["candle_body"] = close.sub(open_price).div(valid_previous_close)
    features["gap_open"] = open_price.sub(previous_close).div(
        valid_previous_close
    )
    features["vol"] = volume
    prior_volume_mean = (
        volume.shift(1)
        .rolling(5, min_periods=3)
        .mean()
        .replace(0.0, math.nan)
    )
    features["volume_ratio"] = volume.div(prior_volume_mean)
    for window in (5, 10, 20):
        features[f"volatility_{window}d"] = safe_close_return.rolling(
            window,
            min_periods=max(2, window // 2),
        ).std(ddof=0)
    for lag in (2, 5, 10):
        lagged_close = close.shift(lag)
        features[f"ret_{lag}d"] = close.div(
            lagged_close.where(lagged_close.gt(0.0))
        ).sub(1.0)

    true_range = pd.concat(
        (
            high.sub(low).abs(),
            high.sub(previous_close).abs(),
            low.sub(previous_close).abs(),
        ),
        axis=1,
    ).max(axis=1, skipna=False)
    normalized_true_range = true_range.div(
        previous_close.where(previous_close.gt(0.0), close)
    )
    features["atr"] = normalized_true_range.rolling(
        14,
        min_periods=5,
    ).mean()
    features["bid_ask_proxy"] = price_range.div(close.where(close.gt(0.0)))
    features["spread_proxy"] = price_range.div(valid_previous_close)
    return features[columns]


__all__ = [
    "D_CLOSE_DISCONTINUITY_LIMIT",
    "D_CLOSE_FEATURE_COLUMNS",
    "D_CLOSE_FEATURE_CONTRACT_VERSION",
    "D_CLOSE_MAX_HISTORY_BARS",
    "compute_d_close_features",
    "empty_d_close_feature_values",
]
