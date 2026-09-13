"""Pure Ridge-side historical feature mathematics, not source admission.

Caller must independently verify a complete, causally available clean ledger
and observed daily bars. This module neither projects P0 nor changes its pool,
ranking, source bytes, model, labels or ledger. Unknown columns are not read.
"""
from __future__ import annotations

from datetime import datetime
import math
import re

import pandas as pd

from top10decision.decision import d_close_features as historical

SCHEMA = "dc20_candidate_live_feature_math_20260913_v1"
FIXED_HISTORY_CEILING = "20260814"
UNTOUCHED_OUTCOME_START = "20260914"
REQUIRED_OBSERVED_BARS = 21
DAILY_FEATURES = ("d_pct_change", "volume_ratio", "ret_2d", "ret_5d", "ret_10d",
                  "volatility_5d", "volatility_20d")
FLAGS = {"research_only": True, "pure_mathematics_only": True,
    "source_independently_verified": False, "source_completeness_verified": False,
    "point_in_time_availability_verified": False, "calendar_verified": False,
    "price_provider_or_adjustment_verified": False, "production_activation_allowed": False,
    "model_training_performed": False, "forward_selection_issued": False,
    "P0_rows_or_ranks_changed": False, "pool_recomputed_within_top10": False,
    "future_outcomes_read": False, "model_predictions_computed": False,
    "network_calls_performed": 0, "files_written": 0, "unavailable_returns_imputed_zero": False,
    "historical_feature_bytes_changed": False}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _date(value):
    require(type(value) is str and re.fullmatch(r"20[0-9]{6}", value), "EXACT_DATE_REQUIRED")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError:
        raise ValueError("VALID_DATE_REQUIRED") from None
    return value


def _code(value):
    require(type(value) is str and re.fullmatch(r"[0-9]{6}\.(SH|SZ)", value), "EXACT_STOCK_CODE_REQUIRED")
    return value


def _number(value, *, nullable=False):
    if nullable and value is None:
        return None
    require(type(value) in (int, float), "EXACT_FINITE_NUMBER_OR_EXPLICIT_NONE_REQUIRED")
    try:
        number = float(value)
    except (ValueError, OverflowError):
        raise ValueError("FINITE_NUMBER_REQUIRED") from None
    require(math.isfinite(number), "FINITE_NUMBER_REQUIRED")
    return number


def _history_projection(rows, day):
    require(type(rows) is list, "EXACT_CLEAN_HISTORY_LIST_REQUIRED")
    identities, seen = [], set()
    # Complete identity pass, including non-target stocks, before any outcome.
    for row in rows:
        require(type(row) is dict, "EXACT_HISTORY_ROW_REQUIRED")
        source_day, code = _date(row.get("signal_date")), _code(row.get("ts_code"))
        require(source_day < day and source_day < UNTOUCHED_OUTCOME_START
            and source_day <= FIXED_HISTORY_CEILING, "HISTORY_DATE_OUTSIDE_FIXED_STRICT_PRIOR_SCOPE")
        require((source_day, code) not in seen, "DUPLICATE_HISTORY_IDENTITY")
        seen.add((source_day, code)); identities.append((source_day, code))
    selected = []
    for row, identity in zip(rows, identities):
        require("promotion_hit" in row, "EXPLICIT_PROMOTION_TRUTH_OR_NONE_REQUIRED")
        truth = _number(row["promotion_hit"], nullable=True)
        require(truth is None or truth in (0., 1.), "BINARY_PROMOTION_TRUTH_OR_NONE_REQUIRED")
        selected.append((*identity, truth))
    return tuple(selected)


def compute_stock_prior(history_rows, *, signal_date, ts_code):
    """Historical Beta(2,3) stock posterior from supplied strictly earlier truth.

    Empty/unknown history mathematically retains the cold prior; it is not
    proof that the actual stock had no prior events. Completeness is external.
    """
    day, code = _date(signal_date), _code(ts_code)
    selected = _history_projection(history_rows, day)
    matches = [truth for _, stock, truth in selected if stock == code]
    known = [truth for truth in matches if truth is not None]
    hits, samples = int(sum(known)), len(known)
    end = max((date for date, _, _ in selected), default=None)
    value = (hits + 2.) / (samples + 5.)
    require(_history_projection(history_rows, day) == selected, "HISTORY_INPUT_CHANGED_DURING_MATH")
    return {"schema_version": SCHEMA, "status": "UNVERIFIED_STOCK_PRIOR_COMPUTED",
        "signal_date": day, "ts_code": code, "features": {"five_year_stock_prior_rate": value},
        "prior_hits": hits, "prior_known_samples": samples, "prior_unknown_samples": len(matches) - samples,
        "cold_prior_applied": samples == 0, "history_row_count": len(selected),
        "fixed_history_ceiling": FIXED_HISTORY_CEILING, "observed_history_end": end,
        "untouched_future_outcome_start": UNTOUCHED_OUTCOME_START,
        "source_age_calendar_days": None if end is None else
            (datetime.strptime(day, "%Y%m%d") - datetime.strptime(end, "%Y%m%d")).days,
        "fixed_source_predates_target_D": day > FIXED_HISTORY_CEILING,
        "source_extended_or_refreshed": False, "unknown_truth_counted_as_failure": False,
        "formula": "(strictly_earlier_known_promotion_hits + 2) / (strictly_earlier_known_samples + 5)",
        "source_authority_issued": False, **FLAGS}


def _bar_projection(rows, day, code):
    require(type(rows) is list, "EXACT_OBSERVED_BAR_LIST_REQUIRED")
    identities, seen = [], set()
    # A future bar is rejected before reading any price/volume in any row.
    for row in rows:
        require(type(row) is dict, "EXACT_BAR_ROW_REQUIRED")
        source_day, stock = _date(row.get("trade_date")), _code(row.get("ts_code"))
        require(source_day <= day and stock == code, "BAR_DATE_OR_STOCK_OUTSIDE_D_ONLY_SCOPE")
        require(source_day not in seen, "DUPLICATE_OBSERVED_BAR")
        seen.add(source_day); identities.append(source_day)
    selected = []
    for row, source_day in zip(rows, identities):
        require("close" in row and "volume" in row, "EXPLICIT_CLOSE_AND_VOLUME_OR_NONE_REQUIRED")
        close, volume = _number(row["close"]), _number(row["volume"], nullable=True)
        require(close > 0 and (volume is None or volume >= 0), "INVALID_OBSERVED_PRICE_OR_VOLUME")
        selected.append((source_day, close, volume))
    return tuple(sorted(selected))


def compute_daily_features(bars, *, signal_date, ts_code):
    """Compute seven historical daily features from supplied observed bars.

    The shared canonical function supplies rolling/masking/NA semantics.
    d_pct_change deliberately uses the previous OBSERVED close, unmasked and
    multiplied by 100, exactly as the historical Tencent-bar parser. Supplied
    pre_close, pct_chg, outcomes and all other columns are never consulted.
    Fewer than 21 observations is explicit incompatibility, not silent parity.
    """
    day, code = _date(signal_date), _code(ts_code)
    selected = _bar_projection(bars, day, code)
    require(historical.D_CLOSE_FEATURE_CONTRACT_VERSION == "dc20_daily_candidate_d_close_v1"
        and historical.D_CLOSE_MAX_HISTORY_BARS == REQUIRED_OBSERVED_BARS
        and historical.D_CLOSE_DISCONTINUITY_LIMIT == .125, "SHARED_HISTORICAL_MATH_CONTRACT_CHANGED")
    values = dict.fromkeys(DAILY_FEATURES)
    has_D = bool(selected) and selected[-1][0] == day
    enough = len(selected) >= REQUIRED_OBSERVED_BARS
    status = "MISSING_D_OBSERVATION" if not has_D else "INSUFFICIENT_OBSERVED_HISTORY"
    if has_D and enough:
        projected = [{"trade_date": date, "close": close, "volume": volume}
                     for date, close, volume in selected]
        output = historical.compute_d_close_features(pd.DataFrame(projected), cutoff_date=day)
        require(not output.empty and output.iloc[-1]["trade_date"] == day, "CANONICAL_LAST_D_CHANGED")
        latest = output.iloc[-1]
        for name in DAILY_FEATURES[1:]:
            value = latest[name]
            values[name] = None if pd.isna(value) else _number(float(value))
        values["d_pct_change"] = _number(100. * (selected[-1][1] - selected[-2][1]) / selected[-2][1])
        status = "UNVERIFIED_DAILY_FEATURES_COMPUTED"
    require(_bar_projection(bars, day, code) == selected, "OBSERVED_BAR_INPUT_CHANGED_DURING_MATH")
    return {"schema_version": SCHEMA, "status": status, "signal_date": day, "ts_code": code,
        "features": values, "observed_bar_count": len(selected), "required_observed_bar_count": REQUIRED_OBSERVED_BARS,
        "observed_start": selected[0][0] if selected else None, "observed_end": selected[-1][0] if selected else None,
        "D_observation_present": has_D, "observed_window_requirement_met": has_D and enough,
        "training_window_equivalence_verified": False, "missing_feature_names": [k for k,v in values.items() if v is None],
        "missing_sessions_synthesized": False, "supplied_pre_close_used": False,
        "d_pct_change_units": "PERCENT_NOT_FRACTION", "returns_and_volatility_units": "FRACTION",
        "volume_ratio_units": "DIMENSIONLESS", "numeric_bitwise_equivalence_claimed": False,
        "canonical_math": "top10decision.decision.d_close_features.compute_d_close_features",
        "source_authority_issued": False, **FLAGS}
