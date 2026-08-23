#!/usr/bin/env python3
"""Build DC20's owned five-year supervised ledger for the three rank engines.

The input event table contains point-in-time D-day 2->3 / 3->4 features.  This
builder downloads unadjusted exchange-price bars from Tencent, binds every D
row to the next two market sessions, and creates three explicit targets:

* promotion: T closes at the exchange-rounded 10% main-board limit
* big loss: executable T-open to T+1-open net return <= -3%
* profit: executable T-open to T+1-open net return > 0

Rows that cannot be bought because T is a one-price limit-up are retained for
promotion training, but their return targets are null.  This avoids converting
non-fills into artificial zero returns.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import os
import random
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from top10decision.decision.d_close_features import (
    D_CLOSE_FEATURE_COLUMNS,
    D_CLOSE_FEATURE_CONTRACT_VERSION,
    compute_d_close_features,
)


ROOT = Path(__file__).resolve().parents[1]
EVENT_PATH = ROOT / "data" / "auction_v3" / "promotion_prior" / "five_year_event_features.csv.gz"
PREDICTION_ROOT = ROOT / "outputs" / "auction_v3" / "predictions"
OUTPUT_ROOT = ROOT / "data" / "decision_three_engines"
LEDGER_PATH = OUTPUT_ROOT / "five_year_supervised_ledger.csv.gz"
MANIFEST_PATH = OUTPUT_ROOT / "five_year_ledger_manifest.json"
TENCENT_ENDPOINT = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
FOCUS_STAGES = {2, 3}
FOCUS_BOARDS = {"SH_MAIN", "SZ_MAIN"}
ROUND_TRIP_COST = 0.0045
BIG_LOSS_THRESHOLD = -0.03
RUNTIME_ALIGNED_FEATURE_VERSION = D_CLOSE_FEATURE_CONTRACT_VERSION
RUNTIME_ALIGNED_FEATURE_COLUMNS = D_CLOSE_FEATURE_COLUMNS


def _normal_date(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _normal_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if "." not in text:
        digits = "".join(ch for ch in text if ch.isdigit())[:6]
        if len(digits) != 6:
            return ""
        suffix = "SH" if digits.startswith("6") else "SZ"
        return f"{digits}.{suffix}"
    digits, suffix = text.split(".", 1)
    return f"{digits.zfill(6)}.{suffix}"


def _tencent_symbol(code: str) -> str:
    digits, suffix = _normal_code(code).split(".", 1)
    return f"{'sh' if suffix == 'SH' else 'sz'}{digits}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _event_stage(frame: pd.DataFrame) -> pd.Series:
    source = frame.get(
        "limit_times",
        frame.get("stage", frame.get("stage_transition", pd.Series(index=frame.index))),
    )
    text = source.fillna("").astype(str).str.replace("→", "->", regex=False)
    return pd.to_numeric(text.str.split("->").str[0], errors="coerce").round()


def _load_owned_events(
    event_path: Path,
    prediction_root: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Union the frozen five-year seed with newer canonical DC20 D snapshots."""

    seed = pd.read_csv(event_path, low_memory=False)
    required = {"signal_date", "ts_code", "stage", "board"}
    missing = sorted(required - set(seed.columns))
    if missing:
        raise ValueError(f"event table missing columns: {missing}")
    seed["signal_date"] = seed["signal_date"].map(_normal_date)
    seed["ts_code"] = seed["ts_code"].map(_normal_code)
    seed["stage"] = _event_stage(seed)
    seed = seed.drop_duplicates(["signal_date", "ts_code"], keep="last")
    seed_end = max(seed["signal_date"].dropna().astype(str), default="")

    additions: list[pd.DataFrame] = []
    inventory: list[dict[str, Any]] = []
    if prediction_root.is_dir():
        for path in sorted(prediction_root.glob("pred_20*.csv")):
            match = re.fullmatch(r"pred_(20\d{6})\.csv", path.name)
            if match is None:
                continue
            signal_date = match.group(1)
            if signal_date <= seed_end:
                continue
            frame = pd.read_csv(path, low_memory=False)
            if frame.empty:
                continue
            if not {"signal_date", "ts_code"}.issubset(frame.columns):
                raise ValueError(f"canonical prediction lacks identity columns: {path}")
            claimed_dates = frame["signal_date"].map(_normal_date)
            if set(claimed_dates) != {signal_date}:
                raise ValueError(f"canonical prediction date binding failed: {path}")
            codes = frame["ts_code"].map(_normal_code)
            stages = _event_stage(frame)
            mechanism = pd.to_numeric(
                frame.get(
                    "mechanism_limit_pct",
                    frame.get("decision_limit_pct", pd.Series(10.0, index=frame.index)),
                ),
                errors="coerce",
            )
            eligible = (
                stages.isin(FOCUS_STAGES)
                & codes.str.fullmatch(r"\d{6}\.(SH|SZ)")
                & mechanism.le(10.0)
            )
            selected = pd.DataFrame(
                {
                    "signal_date": claimed_dates.loc[eligible],
                    "ts_code": codes.loc[eligible],
                    "stage": stages.loc[eligible],
                }
            )
            selected["board"] = selected["ts_code"].map(
                lambda code: "SH_MAIN" if str(code).endswith(".SH") else "SZ_MAIN"
            )
            if selected.duplicated(["signal_date", "ts_code"]).any():
                raise ValueError(f"canonical prediction has duplicate eligible rows: {path}")
            if not selected.empty:
                additions.append(selected)
            inventory.append(
                {
                    "path": path.relative_to(ROOT).as_posix()
                    if path.is_relative_to(ROOT)
                    else str(path),
                    "sha256": _sha256(path),
                    "signal_date": signal_date,
                    "source_rows": int(len(frame)),
                    "eligible_rows": int(len(selected)),
                }
            )
    if additions:
        live = pd.concat(additions, ignore_index=True)
        for column in seed.columns:
            if column not in live.columns:
                live[column] = pd.NA
        events = pd.concat([seed, live[seed.columns]], ignore_index=True)
    else:
        events = seed
    events = events.sort_values(["signal_date", "stage", "ts_code"], kind="stable")
    events = events.drop_duplicates(["signal_date", "ts_code"], keep="first")
    return events.reset_index(drop=True), {
        "seed_path": event_path.relative_to(ROOT).as_posix()
        if event_path.is_relative_to(ROOT)
        else str(event_path),
        "seed_sha256": _sha256(event_path),
        "seed_end_signal_date": seed_end,
        "canonical_prediction_files": inventory,
        "canonical_prediction_file_count": len(inventory),
        "new_eligible_rows_discovered": int(sum(item["eligible_rows"] for item in inventory)),
    }


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(rendered)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _atomic_gzip_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as raw:
        temporary = Path(raw.name)
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as text:
                frame.to_csv(text, index=False, lineterminator="\n", float_format="%.10g")
        raw.flush()
        os.fsync(raw.fileno())
    os.replace(temporary, path)


def _fetch_payload(code: str, *, begin: str, end: str, timeout: float, attempts: int) -> dict[str, Any]:
    symbol = _tencent_symbol(code)
    begin_text = f"{begin[:4]}-{begin[4:6]}-{begin[6:]}"
    end_text = f"{end[:4]}-{end[4:6]}-{end[6:]}"
    params = {"param": f"{symbol},day,{begin_text},{end_text},2000,"}
    headers = {"Accept": "application/json", "User-Agent": "DC20-three-engine-ledger/1.0"}
    last_error: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            response = requests.get(TENCENT_ENDPOINT, params=params, headers=headers, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            source = (payload.get("data") or {}).get(symbol)
            if payload.get("code") != 0 or not isinstance(source, dict):
                raise RuntimeError(f"Tencent returned no data for {code}")
            klines = source.get("day") or source.get("qfqday") or source.get("hfqday")
            if not isinstance(klines, list) or not klines:
                raise RuntimeError(f"Tencent returned no bars for {code}")
            return payload
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(min(8.0, 0.35 * (2 ** (attempt - 1))) + random.random() * 0.2)
    raise RuntimeError(f"failed to fetch {code}: {last_error}")


def _load_or_fetch(
    code: str,
    *,
    cache_root: Path,
    begin: str,
    end: str,
    timeout: float,
    attempts: int,
) -> tuple[str, dict[str, Any], bool]:
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_path = cache_root / f"{code.replace('.', '_')}_{begin}_{end}.json.gz"
    cache_candidates = [cache_path]
    prefix = f"{code.replace('.', '_')}_{begin}_"
    for candidate in sorted(cache_root.glob(f"{prefix}*.json.gz"), reverse=True):
        suffix = candidate.name.removeprefix(prefix).removesuffix(".json.gz")
        if re.fullmatch(r"\d{8}", suffix) and suffix >= end and candidate not in cache_candidates:
            cache_candidates.append(candidate)
    for cached_path in cache_candidates:
        if not cached_path.is_file():
            continue
        try:
            with gzip.open(cached_path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
            source = (payload.get("data") or {}).get(_tencent_symbol(code))
            if payload.get("code") == 0 and isinstance(source, dict) and (source.get("day") or source.get("qfqday")):
                return code, payload, True
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    payload = _fetch_payload(code, begin=begin, end=end, timeout=timeout, attempts=attempts)
    with tempfile.NamedTemporaryFile("wb", dir=cache_root, delete=False) as raw:
        temporary = Path(raw.name)
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            compressed.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        raw.flush()
        os.fsync(raw.fileno())
    os.replace(temporary, cache_path)
    return code, payload, False


def _bars(code: str, payload: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    source = (payload.get("data") or {}).get(_tencent_symbol(code)) or {}
    klines = source.get("day") or source.get("qfqday") or source.get("hfqday") or []
    for line in klines:
        values = list(line) if isinstance(line, list) else str(line).split(",")
        if len(values) < 6:
            continue
        rows.append(
            {
                "ts_code": code,
                "trade_date": _normal_date(values[0]),
                "open": pd.to_numeric(values[1], errors="coerce"),
                "close": pd.to_numeric(values[2], errors="coerce"),
                "high": pd.to_numeric(values[3], errors="coerce"),
                "low": pd.to_numeric(values[4], errors="coerce"),
                "volume": pd.to_numeric(values[5], errors="coerce"),
                "amount": math.nan,
                "turnover_pct": math.nan,
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame = frame.dropna(subset=["trade_date", "open", "close"])
    frame = frame.sort_values("trade_date", kind="stable")
    frame["pre_close"] = frame["close"].shift(1)
    frame["change"] = frame["close"] - frame["pre_close"]
    frame["pct_change"] = 100.0 * frame["change"] / frame["pre_close"].replace(0.0, math.nan)
    return frame.drop_duplicates(["ts_code", "trade_date"], keep="last")


def _attach_d_close_history_features(
    prices: pd.DataFrame,
    relevant_keys: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Attach only features observable by the D close for every stock/date.

    Tencent's unadjusted transaction-price bars intentionally remain the raw
    source.  Discontinuous daily moves are nulled before rolling calculations
    so a split, rights issue, or stale quote cannot masquerade as momentum.
    Rolling windows are stock-local and backward-looking; no T/T+1 value enters
    a D feature.
    """

    if prices.empty:
        return prices.copy()
    relevant: dict[str, set[str]] = {}
    if relevant_keys is not None and not relevant_keys.empty:
        for code, group in relevant_keys.groupby("ts_code", sort=False):
            relevant[str(code)] = set(group["signal_date"].astype(str))

    frames: list[pd.DataFrame] = []
    for code, source in prices.groupby("ts_code", sort=False):
        output = (
            source.sort_values("trade_date", kind="stable")
            .drop_duplicates("trade_date", keep="last")
            .copy()
        )
        output["trade_date"] = output["trade_date"].map(_normal_date)
        for column in ("open", "close", "high", "low", "pre_close", "volume"):
            if column not in output.columns:
                output[column] = math.nan
            output[column] = pd.to_numeric(output[column], errors="coerce")

        canonical_runtime = compute_d_close_features(output)
        output = output.drop(
            columns=list(RUNTIME_ALIGNED_FEATURE_COLUMNS),
            errors="ignore",
        ).merge(
            canonical_runtime,
            on="trade_date",
            how="left",
            sort=False,
            validate="one_to_one",
        )

        safe_close_return = pd.to_numeric(
            output["returns_1d"], errors="coerce"
        ).div(100.0)
        overnight = output["open"].div(output["pre_close"]).sub(1.0)
        overnight = overnight.where(overnight.abs().le(0.125))
        open_to_open = output["open"].pct_change(fill_method=None)
        open_to_open = open_to_open.where(open_to_open.abs().le(0.25))

        output["five_year_d_open_gap"] = overnight
        output["five_year_d_open_to_open_return"] = open_to_open
        output["five_year_d_intraday_range"] = output["high"].sub(
            output["low"]
        ).div(output["pre_close"].replace(0.0, math.nan))
        output["five_year_d_body_return"] = output["close"].div(
            output["open"].replace(0.0, math.nan)
        ).sub(1.0)
        spread = output["high"].sub(output["low"])
        output["five_year_d_close_location"] = output["close"].sub(
            output["low"]
        ).div(spread.where(spread.abs().gt(1e-12)))

        for lag in (1, 2, 5, 10, 20, 60):
            output[f"five_year_pre_{lag}d_momentum"] = output["pre_close"].div(
                output["close"].shift(lag).replace(0.0, math.nan)
            ).sub(1.0)

        prior_return = safe_close_return.shift(1)
        prior_overnight = overnight.shift(1)
        prior_open_to_open = open_to_open.shift(1)
        for window in (5, 10, 20, 60):
            minimum = max(3, window // 2)
            output[f"five_year_pre_{window}d_volatility"] = prior_return.rolling(
                window, min_periods=minimum
            ).std()
            output[f"five_year_pre_{window}d_positive_share"] = prior_return.gt(
                0.0
            ).where(prior_return.notna()).rolling(window, min_periods=minimum).mean()
            output[f"five_year_pre_{window}d_overnight_mean"] = prior_overnight.rolling(
                window, min_periods=minimum
            ).mean()
            output[f"five_year_pre_{window}d_overnight_volatility"] = (
                prior_overnight.rolling(window, min_periods=minimum).std()
            )
            output[f"five_year_pre_{window}d_open_to_open_mean"] = (
                prior_open_to_open.rolling(window, min_periods=minimum).mean()
            )
            output[f"five_year_pre_{window}d_open_to_open_volatility"] = (
                prior_open_to_open.rolling(window, min_periods=minimum).std()
            )

        prior_volume = output["volume"].shift(1)
        for window in (5, 20, 60):
            mean_volume = prior_volume.rolling(
                window, min_periods=max(3, window // 2)
            ).mean()
            output[f"five_year_d_volume_to_pre_{window}d"] = output["volume"].div(
                mean_volume.replace(0.0, math.nan)
            )
        pre20_volume = output["volume"].shift(2).rolling(20, min_periods=10).mean()
        output["five_year_pre_1d_volume_to_pre_20d"] = prior_volume.div(
            pre20_volume.replace(0.0, math.nan)
        )
        prior_60d_high = output["close"].shift(1).rolling(60, min_periods=20).max()
        output["five_year_pre_60d_drawdown"] = output["pre_close"].div(
            prior_60d_high.replace(0.0, math.nan)
        ).sub(1.0)
        if relevant:
            output = output[output["trade_date"].astype(str).isin(relevant.get(str(code), set()))]
        frames.append(output)
    return pd.concat(frames, ignore_index=True) if frames else prices.head(0).copy()


def _next_session(calendar: list[str]) -> dict[str, str]:
    return {current: following for current, following in zip(calendar, calendar[1:])}


def _limit_price(pre_close: Any, ratio: Decimal = Decimal("1.10")) -> float:
    value = Decimal(str(float(pre_close))) * ratio
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _build_ledger(events: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    d_history = _attach_d_close_history_features(
        prices,
        events[["ts_code", "signal_date"]].drop_duplicates(),
    )
    calendar = sorted(prices["trade_date"].dropna().astype(str).unique())
    next_one = _next_session(calendar)
    next_two = {date: next_one.get(next_one.get(date, ""), "") for date in calendar}
    output = events.copy()
    output["buy_date"] = output["signal_date"].map(next_one)
    output["target_exit_date"] = output["signal_date"].map(next_two)
    # A canonical D snapshot enters the supervised ledger only once the market
    # calendar proves that its T+1 session has occurred.  Per-stock suspension
    # gaps remain null labels; a not-yet-mature calendar date is excluded.
    output = output[output["target_exit_date"].fillna("").astype(str).str.len().eq(8)].copy()
    history_columns = [
        column for column in d_history.columns if column.startswith("five_year_")
    ]
    runtime_columns = [
        column for column in RUNTIME_ALIGNED_FEATURE_COLUMNS if column in d_history.columns
    ]
    bar_columns = [
        "ts_code", "trade_date", "open", "close", "high", "low", "volume",
        "amount", "pct_change", "pre_close", "turnover_pct", *history_columns,
        *runtime_columns,
    ]
    d_bars = d_history[bar_columns].rename(columns={
        "trade_date": "signal_date", "open": "d_open", "close": "d_close", "high": "d_high",
        "low": "d_low", "volume": "d_volume", "amount": "d_amount", "pct_change": "d_pct_change",
        "pre_close": "d_pre_close", "turnover_pct": "d_turnover_pct",
    })
    t_bar_columns = [
        "ts_code", "trade_date", "open", "close", "high", "low", "amount",
        "pct_change", "pre_close", "turnover_pct",
    ]
    t_bars = prices[t_bar_columns].rename(columns={
        "trade_date": "buy_date", "open": "t_open", "close": "t_close", "high": "t_high",
        "low": "t_low", "amount": "t_amount", "pct_change": "t_pct_change",
        "pre_close": "t_pre_close", "turnover_pct": "t_turnover_pct",
    })
    exit_bars = prices[["ts_code", "trade_date", "open"]].rename(columns={"trade_date": "target_exit_date", "open": "tplus1_open"})
    output = output.merge(d_bars, on=["ts_code", "signal_date"], how="left")
    output = output.merge(t_bars, on=["ts_code", "buy_date"], how="left")
    output = output.merge(exit_bars, on=["ts_code", "target_exit_date"], how="left")
    output["mechanism_limit_pct"] = 10.0
    d_up_limit = output["d_pre_close"].map(lambda value: _limit_price(value) if _finite(value) and float(value) > 0 else math.nan)
    d_close = pd.to_numeric(output["d_close"], errors="coerce")
    # The source prior includes a small number of legacy/unknown rows.  The
    # three official engines admit only confirmed standard 10% main-board
    # closes; ST/20%/30% mechanisms cannot leak into the training universe.
    confirmed_standard_limit = d_up_limit.notna() & d_close.sub(d_up_limit).abs().le(0.011)
    output = output.loc[confirmed_standard_limit].copy()
    t_up_limit = output["t_pre_close"].map(lambda value: _limit_price(value) if _finite(value) and float(value) > 0 else math.nan)
    t_close = pd.to_numeric(output["t_close"], errors="coerce")
    output["promotion_hit"] = (t_up_limit.notna() & t_close.sub(t_up_limit).abs().le(0.011)).astype("Int64")
    output.loc[output["t_close"].isna() | t_up_limit.isna(), "promotion_hit"] = pd.NA
    t_open = pd.to_numeric(output["t_open"], errors="coerce")
    t_low = pd.to_numeric(output["t_low"], errors="coerce")
    one_price_up = t_up_limit.notna() & t_open.ge(t_up_limit - 0.011) & t_low.ge(t_up_limit - 0.011)
    entry_ratio = t_open / pd.to_numeric(output["d_close"], errors="coerce")
    entry_truth_complete = t_open.gt(0) & entry_ratio.between(0.88, 1.12, inclusive="both")
    tplus1_open = pd.to_numeric(output["tplus1_open"], errors="coerce")
    exit_ratio = tplus1_open / t_close
    return_truth_complete = (
        entry_truth_complete
        & ~one_price_up
        & tplus1_open.gt(0)
        & exit_ratio.between(0.88, 1.12, inclusive="both")
    )
    output["market_fill"] = (entry_truth_complete & ~one_price_up).astype("Int64")
    output.loc[~entry_truth_complete, "market_fill"] = pd.NA
    output["fill_reason"] = "t_open_proxy_buyable"
    output.loc[one_price_up, "fill_reason"] = "t_one_price_limit_up_unfilled"
    output.loc[~entry_truth_complete, "fill_reason"] = "entry_price_truth_incomplete_or_discontinuous"
    output.loc[output["market_fill"].eq(1) & ~return_truth_complete, "fill_reason"] = "t_open_proxy_buyable_return_truth_incomplete_or_discontinuous"
    output["gross_return"] = pd.NA
    output.loc[return_truth_complete, "gross_return"] = (
        pd.to_numeric(output.loc[return_truth_complete, "tplus1_open"], errors="coerce")
        / pd.to_numeric(output.loc[return_truth_complete, "t_open"], errors="coerce") - 1.0
    )
    output["net_return"] = pd.to_numeric(output["gross_return"], errors="coerce") - ROUND_TRIP_COST
    output.loc[~return_truth_complete, "net_return"] = pd.NA
    output["big_loss_hit"] = pd.Series(pd.NA, index=output.index, dtype="Int64")
    output["profit_hit"] = pd.Series(pd.NA, index=output.index, dtype="Int64")
    output.loc[return_truth_complete, "big_loss_hit"] = (pd.to_numeric(output.loc[return_truth_complete, "net_return"], errors="coerce") <= BIG_LOSS_THRESHOLD).astype(int)
    output.loc[return_truth_complete, "profit_hit"] = (pd.to_numeric(output.loc[return_truth_complete, "net_return"], errors="coerce") > 0.0).astype(int)
    date_pool = output.groupby("signal_date")["ts_code"].transform("size")
    stage2 = output["stage"].eq(2).groupby(output["signal_date"]).transform("sum")
    stage3 = output["stage"].eq(3).groupby(output["signal_date"]).transform("sum")
    output["focus_pool_size"] = date_pool.astype(float)
    output["stage2_pool_size"] = stage2.astype(float)
    output["stage3_pool_size"] = stage3.astype(float)
    output["stage_pool_share"] = 0.0
    output.loc[output["stage"].eq(2), "stage_pool_share"] = stage2 / date_pool.clip(lower=1)
    output.loc[output["stage"].eq(3), "stage_pool_share"] = stage3 / date_pool.clip(lower=1)
    ordered_columns = [
        "signal_date", "buy_date", "target_exit_date", "ts_code", "stage", "board",
        "mechanism_limit_pct", "promotion_hit", "market_fill", "fill_reason", "gross_return",
        "net_return", "big_loss_hit", "profit_hit", "d_open", "d_close", "d_high", "d_low", "d_volume",
        "d_amount", "d_pct_change", "d_turnover_pct", "t_open", "t_close", "t_high", "t_low",
        "t_amount", "t_pct_change", "t_turnover_pct", "tplus1_open", "focus_pool_size",
        "stage2_pool_size", "stage3_pool_size", "stage_pool_share", *history_columns,
        *runtime_columns,
    ]
    feature_columns = [column for column in events.columns if column not in {"signal_date", "ts_code", "stage", "board"}]
    output = output[[*ordered_columns, *feature_columns]]
    # Candidate-pool percentile ranks are point-in-time cross-sectional
    # features.  They improve comparability across price/volume regimes while
    # remaining strictly within the same immutable D snapshot.
    cross_sectional = [
        "five_year_d_intraday_range",
        "five_year_d_body_return",
        "five_year_d_volume_to_pre_20d",
        "five_year_pre_5d_momentum",
        "five_year_pre_20d_momentum",
        "five_year_pre_20d_volatility",
        "five_year_pre_20d_overnight_mean",
        "five_year_pre_20d_open_to_open_mean",
        "five_year_pre_60d_drawdown",
    ]
    for column in cross_sectional:
        if column in output.columns:
            output[f"{column}_pool_pct_rank"] = output.groupby("signal_date")[
                column
            ].rank(method="average", pct=True)
    return output.sort_values(["signal_date", "stage", "ts_code"], kind="stable").reset_index(drop=True)


def _recompute_point_in_time_promotion_priors(ledger: pd.DataFrame) -> pd.DataFrame:
    """Overwrite promotion priors from this ledger using only earlier D truth."""

    if ledger.empty:
        return ledger.copy()
    from top10decision.auction_v3.promotion_model import (
        PROMOTION_PRIOR_FEATURES,
        _prior_grid,
    )

    output = ledger.copy()
    truth = pd.to_numeric(output["promotion_hit"], errors="coerce")
    daily = output.loc[truth.isin((0, 1)), ["signal_date", "stage", "board"]].copy()
    daily["hits"] = truth.loc[daily.index].astype(float)
    daily["samples"] = 1.0
    daily = daily.groupby(
        ["signal_date", "stage", "board"], as_index=False
    ).agg(samples=("samples", "sum"), hits=("hits", "sum"))
    priors = _prior_grid(daily, sorted(output["signal_date"].astype(str).unique()))
    source_columns = {
        feature: f"{feature}_strict_prior" for feature in PROMOTION_PRIOR_FEATURES
    }
    priors = priors.rename(columns=source_columns)
    output = output.merge(
        priors,
        on=["signal_date", "stage", "board"],
        how="left",
        validate="many_to_one",
    )
    for feature, source in source_columns.items():
        output[feature] = pd.to_numeric(output[source], errors="coerce")
    return output.drop(columns=list(source_columns.values()))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--event-path", default=str(EVENT_PATH))
    parser.add_argument("--prediction-root", default=str(PREDICTION_ROOT))
    parser.add_argument("--output", default=str(LEDGER_PATH))
    parser.add_argument("--manifest", default=str(MANIFEST_PATH))
    parser.add_argument("--cache-root", default=str(Path(tempfile.gettempdir()) / "dc20-three-engine-tencent-cache"))
    parser.add_argument("--max-workers", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--attempts", type=int, default=4)
    parser.add_argument("--minimum-price-coverage", type=float, default=0.98)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    event_path = Path(args.event_path).resolve()
    prediction_root = Path(args.prediction_root).resolve()
    output_path = Path(args.output).resolve()
    manifest_path = Path(args.manifest).resolve()
    cache_root = Path(args.cache_root).resolve()
    if not event_path.is_file():
        raise FileNotFoundError(event_path)
    if not 1 <= args.max_workers <= 32:
        raise ValueError("max-workers must be between 1 and 32")
    if not 0.90 <= args.minimum_price_coverage <= 1.0:
        raise ValueError("minimum-price-coverage must be between 0.90 and 1.0")
    events, event_source_inventory = _load_owned_events(
        event_path,
        prediction_root,
    )
    events["signal_date"] = events["signal_date"].map(_normal_date)
    events["ts_code"] = events["ts_code"].map(_normal_code)
    events["stage"] = pd.to_numeric(events["stage"], errors="coerce").round()
    events = events[
        events["stage"].isin(FOCUS_STAGES)
        & events["board"].astype(str).isin(FOCUS_BOARDS)
        & events["signal_date"].str.fullmatch(r"\d{8}")
        & events["ts_code"].str.fullmatch(r"\d{6}\.(SH|SZ)")
    ].copy()
    events["stage"] = events["stage"].astype(int)
    events = events.drop_duplicates(["signal_date", "ts_code"], keep="last")
    if events.empty:
        raise ValueError("no eligible 2->3 / 3->4 events")
    begin = (pd.Timestamp(min(events["signal_date"])) - pd.Timedelta(days=35)).strftime("%Y%m%d")
    requested_end = min(
        (pd.Timestamp(max(events["signal_date"])) + pd.Timedelta(days=14)).strftime("%Y%m%d"),
        datetime.now(timezone.utc).strftime("%Y%m%d"),
    )
    codes = sorted(events["ts_code"].unique())
    payloads: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    cache_hits = 0
    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {
            executor.submit(
                _load_or_fetch, code, cache_root=cache_root, begin=begin, end=requested_end,
                timeout=args.timeout, attempts=args.attempts,
            ): code for code in codes
        }
        for future in as_completed(futures):
            code = futures[future]
            try:
                fetched_code, payload, cached = future.result()
                payloads[fetched_code] = payload
                cache_hits += int(cached)
            except Exception as exc:
                failures[code] = f"{type(exc).__name__}: {exc}"
    price_frames = [_bars(code, payload) for code, payload in payloads.items()]
    prices = pd.concat([frame for frame in price_frames if not frame.empty], ignore_index=True)
    prices = prices.drop_duplicates(["ts_code", "trade_date"], keep="last")
    ledger = _build_ledger(events, prices)
    # Rebuild every stage/board prior from strictly earlier dates.  The event
    # artifact stores stock context only; the official promotion feature
    # contract also requires the point-in-time five-year prior grid.
    import sys

    source_root = Path(args.root).resolve()
    source_path = source_root / "src"
    if str(source_path) not in sys.path:
        sys.path.insert(0, str(source_path))
    from top10decision.auction_v3.promotion_model import attach_promotion_source_features

    ledger = attach_promotion_source_features(ledger, source_root)
    ledger = _recompute_point_in_time_promotion_priors(ledger)
    d_coverage = float(ledger["d_close"].notna().mean())
    t_coverage = float(ledger["t_open"].notna().mean())
    exit_coverage = float(ledger["tplus1_open"].notna().mean())
    promotion_coverage = float(ledger["promotion_hit"].notna().mean())
    return_coverage = float(ledger["net_return"].notna().mean())
    hard_price_coverage = min(d_coverage, t_coverage, exit_coverage)
    if hard_price_coverage < args.minimum_price_coverage:
        sample = dict(sorted(failures.items())[:20])
        raise RuntimeError(
            f"five-year price coverage gate failed: {hard_price_coverage:.4%} < "
            f"{args.minimum_price_coverage:.4%}; fetch_failures={sample}"
        )
    if ledger["signal_date"].nunique() < 1_100 or len(ledger) < 10_000:
        raise RuntimeError("five-year event coverage gate failed")
    for target in ("promotion_hit", "big_loss_hit", "profit_hit"):
        values = pd.to_numeric(ledger[target], errors="coerce").dropna()
        counts = values.astype(int).value_counts()
        if set(counts.index) != {0, 1} or int(counts.min()) < 200:
            raise RuntimeError(f"target class support gate failed for {target}: {counts.to_dict()}")
    _atomic_gzip_csv(ledger, output_path)
    manifest = {
        "schema_version": "dc20_three_engine_five_year_ledger_v1",
        "owner": "njedu2023-prog/DC20",
        "runtime_dependency_on_top10_decision": False,
        "source": {
            "event_artifact": str(event_path.relative_to(Path(args.root).resolve())),
            "event_sha256": _sha256(event_path),
            "price_provider": "Tencent ifzq public daily kline",
            "price_endpoint": TENCENT_ENDPOINT,
            "adjustment": "none (exchange transaction prices)",
            "requested_begin": begin,
            "requested_end": requested_end,
            "codes": len(codes),
            "successful_codes": len(payloads),
            "cache_hits": cache_hits,
            "fetch_failures": failures,
            "event_source_inventory": event_source_inventory,
            "prior_grid_truth_cutoff_rule": "strictly_before_signal_date",
        },
        "target_contract": {
            "promotion_hit": "T close equals exchange-rounded pre_close * 1.10",
            "market_fill": "T bar exists and is not a one-price 10% limit-up",
            "return_window": "T open proxy to T+1 open",
            "round_trip_cost_rate": ROUND_TRIP_COST,
            "big_loss_threshold": BIG_LOSS_THRESHOLD,
            "nonfill_return_targets": "null",
        },
        "runtime_feature_contract": {
            "version": RUNTIME_ALIGNED_FEATURE_VERSION,
            "columns": list(RUNTIME_ALIGNED_FEATURE_COLUMNS),
            "available_by_d_close": True,
            "future_columns_used": [],
        },
        "coverage": {
            "start_signal_date": min(ledger["signal_date"]),
            "end_signal_date": max(ledger["signal_date"]),
            "signal_dates": int(ledger["signal_date"].nunique()),
            "rows": int(len(ledger)),
            "codes": int(ledger["ts_code"].nunique()),
            "d_price": d_coverage,
            "t_price": t_coverage,
            "tplus1_price": exit_coverage,
            "promotion_truth": promotion_coverage,
            "executable_return_truth": return_coverage,
        },
        "targets": {
            target: {
                "rows": int(pd.to_numeric(ledger[target], errors="coerce").notna().sum()),
                "positives": int(pd.to_numeric(ledger[target], errors="coerce").eq(1).sum()),
                "rate": float(pd.to_numeric(ledger[target], errors="coerce").mean()),
            } for target in ("promotion_hit", "big_loss_hit", "profit_hit")
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ledger_path": str(output_path.relative_to(Path(args.root).resolve())),
    }
    manifest["ledger_sha256"] = _sha256(output_path)
    _atomic_json(manifest_path, manifest)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
