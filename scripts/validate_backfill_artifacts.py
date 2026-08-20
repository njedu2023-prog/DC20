#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import stat
import sys
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from top10decision.auction_v3.config import (  # noqa: E402
    AuctionV3Config,
    TARGET_HISTORY_DATES,
    TARGET_INDEPENDENT_OOS_DATES,
    WALKFORWARD_WARMUP_DATES,
)
from top10decision.decision.canonical_fingerprint import (  # noqa: E402
    canonical_json_bytes,
)
from top10decision.decision.contracts import (  # noqa: E402
    EXIT_LATEST_TIME,
    EXIT_POLICY_VERSION,
    EXIT_STOP_LOSS_PCT,
    EXIT_TAKE_PROFIT_PCT,
    HISTORY_CONTRACT_VERSION,
)


YMD_RE = re.compile(r"[0-9]{8}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
RECEIPT_SCHEMA = "decision_v11_backfill_receipt_v1"
MANIFEST_SCHEMA = "decision_v11_history_manifest_v2"
HISTORY_PREFIX = (
    PurePosixPath("data/auction_v3/history") / EXIT_POLICY_VERSION
)
CALENDAR_PATH = PurePosixPath("data/market/trade_cal_sse.csv")
CALENDAR_COLUMNS = ("exchange", "cal_date", "is_open", "pretrade_date")
MARKET_DATA_READY_TIME = clock_time(18, 0)
CODE_RE = re.compile(r"[0-9]{6}\.(?:SH|SZ|BJ)")
MANIFEST_KEYS = {
    "schema_version",
    "generated_at_utc",
    "evaluated_at_utc",
    "calendar_source",
    "strict_calendar",
    "calendar_file",
    "calendar_bytes_sha256",
    "calendar_bytes",
    "calendar_open_dates",
    "requested_start_date",
    "requested_end_date",
    "target_signal_dates",
    "target_window_start",
    "target_window_end",
    "target_window_open_sessions",
    "target_window_signal_dates",
    "target_history_sessions",
    "walkforward_warmup_sessions",
    "max_missing_dates",
    "target_signal_date_count",
    "produced_signal_dates",
    "produced_rows",
    "official_auction_truth_rows",
    "auction_truth_coverage",
    "total_compact_signal_dates",
    "target_independent_dates",
    "exit_policy",
    "output_file",
    "output_sha256",
    "output_canonical_sha256",
    "output_bytes_sha256",
    "output_bytes",
    "endpoint_rows",
    "failures",
    "credential_persisted",
}
TRAINING_NAME_RE = re.compile(
    r"training_([0-9]{8})_([0-9]{8})(?:_part([0-9]{3}))?\.csv"
)
# Complete AuctionV3Engine.build_history compact-backfill contract. A model
# feature migration must update this reviewed tuple; a self-signed receipt may
# never add, remove, or reorder training columns on its own.
EXPECTED_HISTORY_COLUMNS = (
    "signal_date",
    "buy_date",
    "target_exit_date",
    "actual_exit_date",
    "exit_delay_days",
    "ts_code",
    "name",
    "industry",
    "stage",
    "source_rank",
    "d_close",
    "buy_open",
    "auction_vwap",
    "auction_amount",
    "auction_truth_source",
    "exit_open",
    "actual_buy_gap",
    "gross_return",
    "net_return",
    "profit_hit",
    "big_loss_hit",
    "continuation_limit_up_hit",
    "exit_on_time",
    "market_fill",
    "public_market_buyable",
    "actual_order_fill_observed",
    "actual_order_fill",
    "mechanism_limit_pct",
    "fill_reason",
    "exit_reason",
    "exit_policy_version",
    "take_profit_pct",
    "stop_loss_pct",
    "latest_exit_time",
    "history_source",
    "history_contract_version",
    "prior_probability",
    "strength_score",
    "theme_boost",
    "final_score",
    "intraday_quality",
    "intraday_risk",
    "intraday_hard_risk",
    "auction_strength",
    "intraday_confidence",
    "stage_quality",
    "stage_risk",
    "stage_prior",
    "limit_times",
    "open_board_count",
    "reseal_score",
    "late_withdraw",
    "d_return",
    "d_range",
    "d_turnover_proxy",
    "d_amount_log",
    "limit_open_times",
    "limit_first_time_minutes",
    "limit_last_time_minutes",
    "limit_fd_amount_log",
    "limit_seal_to_amount",
    "limit_seal_to_float_mv",
    "d_turnover_rate",
    "d_volume_ratio",
    "d_float_mv_log",
    "order_to_d_amount",
    "order_to_float_mv",
    "is_hot_board",
    "board_rank",
    "board_limit_up_count",
    "d_amount_percentile",
    "market_median_return",
    "market_up_ratio",
    "market_return_dispersion",
    "market_equal_weight_return",
    "market_down_ratio",
    "market_strong_up_ratio",
    "market_strong_down_ratio",
    "market_limit_up_count_log",
    "market_limit_down_count_log",
    "market_limit_up_down_log_ratio",
    "market_failed_limit_up_rate",
    "market_reseal_rate",
    "market_prev_limit_up_mean_return",
    "market_prev_limit_up_positive_rate",
    "market_prev_limit_up_open_gap_mean",
    "market_focus_promotion_rate",
    "market_limit_up_industry_concentration",
    "market_limit_up_amount_top3_share",
    "market_amount_ratio_5d",
    "market_sentiment_score",
    "market_sentiment_delta",
    "market_sentiment_coverage",
    "market_sentiment_acceleration",
    "market_sentiment_regime_code",
    "market_sentiment_regime_label",
    "market_sentiment_breadth_score",
    "market_sentiment_limit_ecology_score",
    "market_sentiment_promotion_score",
    "market_sentiment_profit_effect_score",
    "market_sentiment_liquidity_score",
    "market_eligible_stock_count",
    "market_limit_up_count",
    "market_limit_down_count",
    "market_touched_up_count",
    "market_failed_limit_up_count",
    "market_reseal_count",
    "market_prev_limit_up_sample",
    "market_2_to_3_promotion_rate",
    "market_2_to_3_promotion_samples",
    "market_3_to_4_promotion_rate",
    "market_3_to_4_promotion_samples",
    "market_focus_promotion_samples",
    "market_max_streak",
    "relative_d_return",
    "minute_available",
    "minute_realized_vol",
    "minute_first_30m_return",
    "minute_last_30m_return",
    "minute_vwap_deviation",
    "minute_opening_volume_share",
    "minute_closing_volume_share",
    "minute_close_location",
    "limit_ratio",
    "proposed_gap",
    "path_days_observed",
    "path_data_coverage",
    "path_strength_latest",
    "path_strength_delta",
    "path_gap_slope",
    "path_first_seal_slope",
    "path_open_times_slope",
    "path_turnover_slope",
    "path_amount_log_slope",
    "path_seal_ratio_slope",
    "path_one_price_ratio",
    "path_weak_to_strong",
    "path_strong_to_weak",
    "path_acceleration_consensus",
    "path_divergence_reseal",
    "path_label_code",
    "path_label",
    "path_explanation",
    "five_year_pre_streak_1d_return",
    "five_year_pre_streak_3d_return",
    "five_year_pre_streak_volatility",
    "five_year_pre_streak_limit_up_count",
    "five_year_recent_limit_up_count",
    "five_year_days_since_prior_limit_up",
    "five_year_streak_runup",
    "five_year_price_log",
    "five_year_stock_prior_rate",
    "five_year_stock_prior_samples_log",
    "stage_pool_size",
    "focus_pool_size",
    "market_max_limit_times",
    "same_industry_stage_count",
    "stage_pool_share",
    "stage_recent_promotion_rate",
    "stage_recent_promotion_samples",
    "five_year_stage_board_prior_rate",
    "five_year_stage_prior_rate",
    "five_year_recent_20d_rate",
    "five_year_recent_60d_rate",
    "five_year_prior_samples_log",
    "five_year_recent_60d_samples_log",
    "five_year_regime_delta",
    "five_year_board_stage_delta",
)
HISTORY_TEXT_COLUMNS = {
    "signal_date",
    "buy_date",
    "target_exit_date",
    "actual_exit_date",
    "ts_code",
    "name",
    "industry",
    "stage",
    "auction_truth_source",
    "fill_reason",
    "exit_reason",
    "exit_policy_version",
    "latest_exit_time",
    "history_source",
    "history_contract_version",
    "market_sentiment_regime_code",
    "market_sentiment_regime_label",
    "path_label_code",
    "path_label",
    "path_explanation",
}
HISTORY_REQUIRED_TEXT_COLUMNS = {
    "signal_date",
    "buy_date",
    "target_exit_date",
    "actual_exit_date",
    "ts_code",
    "name",
    "stage",
    "auction_truth_source",
    "fill_reason",
    "exit_reason",
    "exit_policy_version",
    "latest_exit_time",
    "history_source",
    "history_contract_version",
    "market_sentiment_regime_code",
    "market_sentiment_regime_label",
    "path_label_code",
    "path_label",
    "path_explanation",
}
HISTORY_EXACT_BINARY_COLUMNS = {
    "profit_hit",
    "big_loss_hit",
    "continuation_limit_up_hit",
    "exit_on_time",
    "market_fill",
    "public_market_buyable",
    "actual_order_fill_observed",
}
HISTORY_FLOAT_BINARY_COLUMNS = {
    "minute_available",
    "path_weak_to_strong",
    "path_strong_to_weak",
    "path_acceleration_consensus",
    "path_divergence_reseal",
}
HISTORY_NULLABLE_BINARY_COLUMNS = {"is_hot_board"}
HISTORY_UNIT_INTERVAL_COLUMNS = {"path_one_price_ratio"}
HISTORY_NULLABLE_NUMERIC_COLUMNS = {
    "actual_order_fill",
    "take_profit_pct",
    "stop_loss_pct",
    "prior_probability",
    "strength_score",
    "theme_boost",
    "final_score",
    "intraday_quality",
    "intraday_risk",
    "intraday_hard_risk",
    "auction_strength",
    "intraday_confidence",
    "stage_quality",
    "stage_risk",
    "stage_prior",
    "reseal_score",
    "late_withdraw",
    "open_board_count",
    "limit_open_times",
    "limit_first_time_minutes",
    "limit_last_time_minutes",
    "limit_fd_amount_log",
    "limit_seal_to_amount",
    "limit_seal_to_float_mv",
    "d_turnover_rate",
    "d_volume_ratio",
    "d_float_mv_log",
    "order_to_d_amount",
    "order_to_float_mv",
    "is_hot_board",
    "board_rank",
    "board_limit_up_count",
    "d_return",
    "d_range",
    "d_turnover_proxy",
    "d_amount_log",
    "market_median_return",
    "market_up_ratio",
    "market_return_dispersion",
    "market_equal_weight_return",
    "market_down_ratio",
    "market_strong_up_ratio",
    "market_strong_down_ratio",
    "market_limit_up_count_log",
    "market_limit_down_count_log",
    "market_limit_up_down_log_ratio",
    "market_failed_limit_up_rate",
    "market_reseal_rate",
    "market_prev_limit_up_mean_return",
    "market_prev_limit_up_positive_rate",
    "market_prev_limit_up_open_gap_mean",
    "market_focus_promotion_rate",
    "market_limit_up_industry_concentration",
    "market_limit_up_amount_top3_share",
    "market_amount_ratio_5d",
    "market_sentiment_score",
    "market_sentiment_delta",
    "market_sentiment_acceleration",
    "market_sentiment_breadth_score",
    "market_sentiment_limit_ecology_score",
    "market_sentiment_promotion_score",
    "market_sentiment_profit_effect_score",
    "market_sentiment_liquidity_score",
    "market_2_to_3_promotion_rate",
    "market_3_to_4_promotion_rate",
    "market_max_streak",
    "d_amount_percentile",
    "relative_d_return",
    "minute_realized_vol",
    "minute_first_30m_return",
    "minute_last_30m_return",
    "minute_vwap_deviation",
    "minute_opening_volume_share",
    "minute_closing_volume_share",
    "minute_close_location",
    "path_strength_latest",
    "path_strength_delta",
    "path_gap_slope",
    "path_first_seal_slope",
    "path_open_times_slope",
    "path_turnover_slope",
    "path_amount_log_slope",
    "path_seal_ratio_slope",
    "path_one_price_ratio",
    "stage_recent_promotion_rate",
    "five_year_pre_streak_1d_return",
    "five_year_pre_streak_3d_return",
    "five_year_pre_streak_volatility",
    "five_year_pre_streak_limit_up_count",
    "five_year_recent_limit_up_count",
    "five_year_days_since_prior_limit_up",
    "five_year_streak_runup",
    "five_year_price_log",
    "five_year_stock_prior_rate",
    "five_year_stock_prior_samples_log",
    "five_year_stage_board_prior_rate",
    "five_year_stage_prior_rate",
    "five_year_recent_20d_rate",
    "five_year_recent_60d_rate",
    "five_year_prior_samples_log",
    "five_year_recent_60d_samples_log",
    "five_year_regime_delta",
    "five_year_board_stage_delta",
}
HISTORY_INTEGER_COLUMNS = {
    "exit_delay_days",
    "source_rank",
    "limit_times",
    "open_board_count",
    "limit_open_times",
    "board_rank",
    "board_limit_up_count",
    "market_eligible_stock_count",
    "market_limit_up_count",
    "market_limit_down_count",
    "market_touched_up_count",
    "market_failed_limit_up_count",
    "market_reseal_count",
    "market_prev_limit_up_sample",
    "market_2_to_3_promotion_samples",
    "market_3_to_4_promotion_samples",
    "market_focus_promotion_samples",
    "market_max_streak",
    "path_days_observed",
    "five_year_pre_streak_limit_up_count",
    "five_year_recent_limit_up_count",
    "five_year_days_since_prior_limit_up",
    "stage_pool_size",
    "focus_pool_size",
    "market_max_limit_times",
    "same_industry_stage_count",
    "stage_recent_promotion_samples",
    *HISTORY_EXACT_BINARY_COLUMNS,
    *HISTORY_FLOAT_BINARY_COLUMNS,
}
HISTORY_REQUIRED_FINITE_COLUMNS = (
    set(EXPECTED_HISTORY_COLUMNS)
    .difference(HISTORY_TEXT_COLUMNS)
    .difference(HISTORY_NULLABLE_NUMERIC_COLUMNS)
)
EXPECTED_HISTORY_COLUMNS_SHA256 = hashlib.sha256(
    canonical_json_bytes(list(EXPECTED_HISTORY_COLUMNS))
).hexdigest()
# The checked-in history contains two reviewed legacy schemas. Coverage may be
# sourced from those immutable partitions or the exact V1 producer schema, but
# never from a self-invented one-column file that merely claims signal dates.
COVERAGE_HISTORY_COLUMNS_SHA256 = frozenset(
    {
        "f9bdba786b71e4ab702fc8276830156adfddd65d1b295d4050cb8819fe922459",
        "e4bd4fb66ec63c65107a417901a55ef1a4322576c662f1cef8d47e0b39aeaf3f",
        EXPECTED_HISTORY_COLUMNS_SHA256,
    }
)


class BackfillArtifactError(RuntimeError):
    pass


def _fail(message: str) -> None:
    raise BackfillArtifactError(message)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("JSON contains a duplicate key")
        result[key] = value
    return result


def _read_json(path: Path, label: str) -> dict[str, Any]:
    _require_regular_file(path, label)
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _value: _fail("JSON contains a non-finite number"),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BackfillArtifactError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        _fail(f"{label} must be a JSON object")
    return payload


def _require_regular_file(path: Path, label: str) -> None:
    if path.is_symlink():
        _fail(f"{label} must not be a symlink")
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        raise BackfillArtifactError(f"{label} is missing") from exc
    if not stat.S_ISREG(mode):
        _fail(f"{label} must be a regular file")


def _require_no_symlink_components(root: Path, relative: PurePosixPath) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            _fail("backfill output path contains a symlink component")
    return current


def _native_int(value: Any, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _fail(f"{label} must be a native integer >= {minimum}")
    return value


def _ymd(value: Any, label: str) -> str:
    if type(value) is not str or YMD_RE.fullmatch(value) is None:
        _fail(f"{label} must be YYYYMMDD text")
    try:
        parsed = datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise BackfillArtifactError(f"{label} is not a real calendar date") from exc
    if parsed.strftime("%Y%m%d") != value:
        _fail(f"{label} must round-trip as YYYYMMDD text")
    return value


def _date_list(value: Any, label: str, *, allow_empty: bool) -> list[str]:
    if not isinstance(value, list):
        _fail(f"{label} must be a list")
    dates = [_ymd(item, f"{label}[]") for item in value]
    if not allow_empty and not dates:
        _fail(f"{label} must not be empty")
    if dates != sorted(set(dates)):
        _fail(f"{label} must be sorted and unique")
    return dates


def _strict_utc_seconds(value: Any, label: str) -> str:
    if type(value) is not str or re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\+00:00",
        value,
    ) is None:
        _fail(f"{label} must be canonical UTC seconds")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise BackfillArtifactError(f"{label} is not a real timestamp") from exc
    if (
        parsed.utcoffset() != timedelta(0)
        or parsed.microsecond != 0
        or parsed.isoformat() != value
    ):
        _fail(f"{label} must round-trip as canonical UTC seconds")
    return value


def _read_calendar(
    root: Path,
    payload: dict[str, Any],
    *,
    start_date: str,
    end_date: str,
) -> tuple[list[str], list[str]]:
    if payload.get("calendar_file") != CALENDAR_PATH.as_posix():
        _fail("calendar_file must name the exact SSE calendar artifact")
    path = _require_no_symlink_components(root, CALENDAR_PATH)
    _require_regular_file(path, "SSE trade calendar")
    raw = path.read_bytes()
    if not raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw:
        _fail("SSE trade calendar must be UTF-8 BOM with LF line endings")
    expected_sha = payload.get("calendar_bytes_sha256")
    if (
        type(expected_sha) is not str
        or SHA256_RE.fullmatch(expected_sha) is None
        or expected_sha != hashlib.sha256(raw).hexdigest()
    ):
        _fail("SSE trade calendar byte hash mismatch")
    if _native_int(payload.get("calendar_bytes"), "calendar_bytes", minimum=1) != len(raw):
        _fail("SSE trade calendar byte count mismatch")
    try:
        calendar = pd.read_csv(
            path,
            encoding="utf-8-sig",
            dtype="string",
            keep_default_na=False,
        )
    except (OSError, UnicodeError, pd.errors.ParserError) as exc:
        raise BackfillArtifactError("SSE trade calendar is unreadable") from exc
    if tuple(calendar.columns) != CALENDAR_COLUMNS or calendar.empty:
        _fail("SSE trade calendar schema mismatch")
    if not calendar["exchange"].eq("SSE").all():
        _fail("SSE trade calendar contains another exchange")
    dates = calendar["cal_date"].tolist()
    if (
        any(_ymd(value, "calendar.cal_date") != value for value in dates)
        or dates != sorted(set(dates))
        or not calendar["is_open"].isin({"0", "1"}).all()
    ):
        _fail("SSE trade calendar dates or open flags are noncanonical")
    first_date = datetime.strptime(dates[0], "%Y%m%d").date()
    last_date = datetime.strptime(dates[-1], "%Y%m%d").date()
    complete_dates = [
        (first_date + timedelta(days=offset)).strftime("%Y%m%d")
        for offset in range((last_date - first_date).days + 1)
    ]
    if dates != complete_dates:
        _fail("SSE trade calendar must be a complete daily sequence")
    previous_open = _ymd(
        calendar.iloc[0]["pretrade_date"],
        "calendar.pretrade_date",
    )
    if previous_open >= dates[0]:
        _fail("SSE trade calendar initial pretrade_date is invalid")
    for cal_date, is_open, pretrade_date in calendar[
        ["cal_date", "is_open", "pretrade_date"]
    ].itertuples(index=False, name=None):
        if _ymd(pretrade_date, "calendar.pretrade_date") != pretrade_date:
            _fail("SSE trade calendar pretrade_date is noncanonical")
        if pretrade_date != previous_open:
            _fail("SSE trade calendar pretrade_date chain is inconsistent")
        if is_open == "1":
            previous_open = cal_date
    requested = calendar[
        calendar["cal_date"].ge(start_date) & calendar["cal_date"].le(end_date)
    ].copy()
    first = datetime.strptime(start_date, "%Y%m%d").date()
    last = datetime.strptime(end_date, "%Y%m%d").date()
    expected_dates = [
        (first + timedelta(days=offset)).strftime("%Y%m%d")
        for offset in range((last - first).days + 1)
    ]
    if requested["cal_date"].tolist() != expected_dates:
        _fail("SSE trade calendar does not exactly cover the requested range")
    open_dates = requested.loc[requested["is_open"].eq("1"), "cal_date"].tolist()
    evaluated = datetime.fromisoformat(
        _strict_utc_seconds(payload.get("evaluated_at_utc"), "evaluated_at_utc")
    ).astimezone(ZoneInfo("Asia/Shanghai"))
    if (
        open_dates
        and open_dates[-1] == evaluated.strftime("%Y%m%d")
        and evaluated.time().replace(tzinfo=None) < MARKET_DATA_READY_TIME
    ):
        open_dates = open_dates[:-1]
    if _native_int(payload.get("calendar_open_dates"), "calendar_open_dates") != len(open_dates):
        _fail("calendar_open_dates differs from the verified SSE calendar")
    eligible = (
        open_dates[:-8]
        if len(open_dates) > 8
        else []
    )
    target_window = eligible[-TARGET_HISTORY_DATES:]
    if len(target_window) != TARGET_HISTORY_DATES:
        _fail("verified SSE calendar cannot support the required history window")
    return target_window, open_dates


def _sha256_frame(frame: pd.DataFrame) -> str:
    payload = frame.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_history(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    _require_regular_file(path, "backfill history CSV")
    raw = path.read_bytes()
    if not raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw:
        _fail("backfill history CSV must be UTF-8 BOM with LF line endings")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            if not header or len(header) != len(set(header)):
                _fail("backfill history CSV header is empty or duplicated")
            width = len(header)
            for line_number, row in enumerate(reader, start=2):
                if len(row) != width:
                    _fail(
                        f"backfill history CSV row {line_number} has an invalid width"
                    )
        exact = pd.read_csv(
            path,
            encoding="utf-8-sig",
            dtype="string",
            keep_default_na=False,
        )
        frame = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    except (OSError, UnicodeError, StopIteration, pd.errors.ParserError) as exc:
        raise BackfillArtifactError("backfill history CSV is invalid") from exc
    if frame.empty:
        _fail("backfill history CSV must not be empty")
    if tuple(exact.columns) != EXPECTED_HISTORY_COLUMNS:
        _fail("backfill history CSV exact schema mismatch")
    if list(exact.columns) != list(frame.columns):
        _fail("backfill history CSV exact-text schema drift")
    return frame, exact


def _finite_text_number(value: str, label: str, *, allow_empty: bool) -> float | None:
    if value != value.strip():
        _fail(f"{label} must not contain surrounding whitespace")
    if value == "":
        if allow_empty:
            return None
        _fail(f"{label} must not be empty")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise BackfillArtifactError(f"{label} must be numeric text") from exc
    if not math.isfinite(number):
        _fail(f"{label} must be finite")
    return number


def _covered_signal_dates(history_root: Path) -> set[str]:
    covered: set[str] = set()
    identities: set[tuple[str, str]] = set()
    plain_ranges: set[tuple[str, str]] = set()
    partition_groups: dict[
        tuple[str, str], dict[str, set[Any]]
    ] = {}
    if history_root.is_symlink():
        _fail("backfill history root must not be a symlink")
    for path in sorted(history_root.glob("training_*.csv")):
        if path.is_symlink() or not path.is_file():
            _fail("backfill history contains a non-regular training artifact")
        name_match = TRAINING_NAME_RE.fullmatch(path.name)
        if name_match is None:
            _fail("backfill history contains a noncanonical training filename")
        file_start = _ymd(name_match.group(1), "training filename start date")
        file_end = _ymd(name_match.group(2), "training filename end date")
        if file_start > file_end:
            _fail("backfill history training filename date range is reversed")
        try:
            raw = path.read_bytes()
            if not raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw:
                _fail(
                    "backfill history coverage files must be UTF-8 BOM with LF line endings"
                )
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle)
                header = next(reader)
                if not header or len(header) != len(set(header)):
                    _fail("backfill history coverage header is empty or duplicated")
                header_sha = hashlib.sha256(
                    canonical_json_bytes(header)
                ).hexdigest()
                if header_sha not in COVERAGE_HISTORY_COLUMNS_SHA256:
                    _fail("backfill history coverage schema is not reviewed")
                if header.count("signal_date") != 1 or header.count("ts_code") != 1:
                    _fail(
                        "backfill history coverage lacks exact identity columns"
                    )
                width = len(header)
                date_index = header.index("signal_date")
                code_index = header.index("ts_code")
                file_dates: set[str] = set()
                row_count = 0
                for line_number, row in enumerate(reader, start=2):
                    if len(row) != width:
                        _fail(
                            f"backfill history coverage row {line_number} has an invalid width"
                        )
                    signal_date = _ymd(
                        row[date_index],
                        f"backfill history coverage row {line_number} signal_date",
                    )
                    if not file_start <= signal_date <= file_end:
                        _fail(
                            "backfill history coverage signal_date is outside its filename range"
                        )
                    code = row[code_index]
                    if CODE_RE.fullmatch(code) is None:
                        _fail("backfill history coverage contains a noncanonical ts_code")
                    identity = (signal_date, code)
                    if identity in identities:
                        _fail("backfill history coverage contains a duplicate identity")
                    identities.add(identity)
                    covered.add(signal_date)
                    file_dates.add(signal_date)
                    row_count += 1
                if row_count == 0:
                    _fail("backfill history coverage file must not be empty")
                partition_text = name_match.group(3)
                range_key = (file_start, file_end)
                if partition_text is None:
                    if min(file_dates) != file_start or max(file_dates) != file_end:
                        _fail(
                            "backfill history coverage filename differs from its date endpoints"
                        )
                    plain_ranges.add(range_key)
                else:
                    partition = int(partition_text)
                    if partition < 1:
                        _fail("backfill history coverage partition must be positive")
                    group = partition_groups.setdefault(
                        range_key,
                        {"parts": set(), "dates": set()},
                    )
                    parts = group["parts"]
                    if partition in parts:
                        _fail("backfill history coverage partition is duplicated")
                    parts.add(partition)
                    group["dates"].update(file_dates)
        except (OSError, UnicodeError, StopIteration, csv.Error) as exc:
            raise BackfillArtifactError(
                "backfill history contains an unreadable training artifact"
            ) from exc
    if plain_ranges.intersection(partition_groups):
        _fail("backfill history mixes partitioned and unpartitioned date ranges")
    for (file_start, file_end), group in partition_groups.items():
        parts = group["parts"]
        dates = group["dates"]
        if parts != set(range(1, max(parts) + 1)):
            _fail("backfill history coverage partitions are not contiguous")
        if min(dates) != file_start or max(dates) != file_end:
            _fail(
                "backfill history coverage partition group differs from its date endpoints"
            )
    return covered


def _validate_common_receipt(
    receipt: dict[str, Any],
    *,
    start_date: str,
    end_date: str,
    max_missing_dates: int,
) -> None:
    if receipt.get("schema_version") != RECEIPT_SCHEMA:
        _fail("backfill receipt schema mismatch")
    if receipt.get("requested_start_date") != start_date:
        _fail("backfill receipt start date mismatch")
    if receipt.get("requested_end_date") != end_date:
        _fail("backfill receipt end date mismatch")
    if _native_int(receipt.get("max_missing_dates"), "max_missing_dates", minimum=1) != max_missing_dates:
        _fail("backfill receipt max_missing_dates mismatch")
    if receipt.get("credential_persisted") is not False:
        _fail("backfill receipt must prove credential_persisted=false")


def _validate_up_to_date(
    root: Path,
    receipt: dict[str, Any],
    *,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    expected_keys = {
        "schema_version",
        "status",
        "requested_start_date",
        "requested_end_date",
        "max_missing_dates",
        "evaluated_at_utc",
        "calendar_file",
        "calendar_bytes_sha256",
        "calendar_bytes",
        "calendar_open_dates",
        "covered_signal_dates",
        "target_window_signal_dates",
        "missing_signal_dates",
        "credential_persisted",
    }
    if set(receipt) != expected_keys:
        _fail("up-to-date receipt keys mismatch")
    target_window = _date_list(
        receipt.get("target_window_signal_dates"),
        "target_window_signal_dates",
        allow_empty=False,
    )
    verified_window, _verified_open_dates = _read_calendar(
        root,
        receipt,
        start_date=start_date,
        end_date=end_date,
    )
    if target_window != verified_window:
        _fail("up-to-date receipt window differs from the verified SSE calendar")
    if receipt.get("missing_signal_dates") != []:
        _fail("up-to-date receipt must contain no missing signal dates")
    covered_count = _native_int(
        receipt.get("covered_signal_dates"), "covered_signal_dates"
    )
    covered = _covered_signal_dates(root / HISTORY_PREFIX)
    if covered_count != len(covered):
        _fail("up-to-date receipt covered count mismatch")
    if not set(target_window).issubset(covered):
        _fail("up-to-date receipt target window is not fully covered")
    live_capacity = len(target_window) - WALKFORWARD_WARMUP_DATES
    if live_capacity != TARGET_INDEPENDENT_OOS_DATES:
        _fail("up-to-date live independent OOS capacity mismatch")
    return {
        "status": "up_to_date",
        "target_window_signal_dates": len(target_window),
        "covered_signal_dates": len(covered),
        "live_independent_oos_capacity": live_capacity,
        "expected_dirty_paths": [CALENDAR_PATH.as_posix()],
    }


def _validate_produced(
    root: Path,
    receipt: dict[str, Any],
    *,
    start_date: str,
    end_date: str,
    max_missing_dates: int,
) -> dict[str, Any]:
    if set(receipt) != {
        "schema_version",
        "status",
        "requested_start_date",
        "requested_end_date",
        "max_missing_dates",
        "credential_persisted",
        "manifest",
    }:
        _fail("produced receipt keys mismatch")
    manifest = receipt.get("manifest")
    if not isinstance(manifest, dict):
        _fail("produced receipt manifest must be an object")
    if set(manifest) != MANIFEST_KEYS:
        _fail("backfill manifest keys mismatch")
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        _fail("backfill manifest schema mismatch")

    persisted_path = root / HISTORY_PREFIX / "manifest_latest.json"
    persisted = _read_json(persisted_path, "backfill persisted manifest")
    if canonical_json_bytes(persisted) != canonical_json_bytes(manifest):
        _fail("backfill receipt and persisted manifest differ")

    generated_at = _strict_utc_seconds(
        manifest.get("generated_at_utc"), "generated_at_utc"
    )
    evaluated_at = _strict_utc_seconds(
        manifest.get("evaluated_at_utc"), "evaluated_at_utc"
    )
    if generated_at != evaluated_at:
        _fail("generated_at_utc and evaluated_at_utc must be identical")
    if manifest.get("requested_start_date") != start_date:
        _fail("backfill manifest start date mismatch")
    if manifest.get("requested_end_date") != end_date:
        _fail("backfill manifest end date mismatch")
    manifest_maximum = _native_int(
        manifest.get("max_missing_dates"), "manifest.max_missing_dates", minimum=1
    )
    if manifest_maximum != max_missing_dates:
        _fail("backfill manifest max_missing_dates mismatch")
    verified_window, verified_open_dates = _read_calendar(
        root,
        manifest,
        start_date=start_date,
        end_date=end_date,
    )
    target_window = _date_list(
        manifest.get("target_window_signal_dates"),
        "target_window_signal_dates",
        allow_empty=False,
    )
    if target_window != verified_window:
        _fail("backfill target window differs from the verified SSE calendar")
    target_dates = _date_list(
        manifest.get("target_signal_dates"),
        "target_signal_dates",
        allow_empty=False,
    )
    if not set(target_dates).issubset(target_window):
        _fail("target signal dates are outside the target window")
    if len(target_dates) > manifest_maximum:
        _fail("target signal dates exceed max_missing_dates")
    if _native_int(manifest.get("target_signal_date_count"), "target_signal_date_count", minimum=1) != len(target_dates):
        _fail("target signal date count mismatch")
    if _native_int(manifest.get("produced_signal_dates"), "produced_signal_dates", minimum=1) != len(target_dates):
        _fail("produced signal date count mismatch")
    if _native_int(manifest.get("target_window_open_sessions"), "target_window_open_sessions", minimum=1) != len(target_window):
        _fail("target window open-session count mismatch")
    if manifest.get("target_window_start") != target_window[0] or manifest.get("target_window_end") != target_window[-1]:
        _fail("target window boundary mismatch")
    if _native_int(manifest.get("target_history_sessions"), "target_history_sessions") != TARGET_HISTORY_DATES:
        _fail("target history sessions mismatch")
    if _native_int(manifest.get("walkforward_warmup_sessions"), "walkforward_warmup_sessions") != WALKFORWARD_WARMUP_DATES:
        _fail("walkforward warmup sessions mismatch")
    if _native_int(manifest.get("target_independent_dates"), "target_independent_dates") != TARGET_INDEPENDENT_OOS_DATES:
        _fail("target independent dates mismatch")
    if manifest.get("calendar_source") != "tushare:trade_cal:SSE" or manifest.get("strict_calendar") is not True:
        _fail("strict SSE calendar provenance mismatch")
    if manifest.get("failures") != []:
        _fail("production backfill receipt contains endpoint failures")
    if manifest.get("credential_persisted") is not False:
        _fail("backfill manifest must prove credential_persisted=false")

    exit_policy = manifest.get("exit_policy")
    expected_exit_policy = {
        "version": EXIT_POLICY_VERSION,
        "take_profit_pct": EXIT_TAKE_PROFIT_PCT,
        "stop_loss_pct": EXIT_STOP_LOSS_PCT,
        "latest_exit_time": EXIT_LATEST_TIME,
        "requires_intraday_truth": False,
    }
    if canonical_json_bytes(exit_policy) != canonical_json_bytes(expected_exit_policy):
        _fail("backfill exit policy mismatch")

    raw_output = manifest.get("output_file")
    if type(raw_output) is not str:
        _fail("backfill output_file must be text")
    relative = PurePosixPath(raw_output)
    expected_name = f"training_{target_dates[0]}_{target_dates[-1]}.csv"
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative.parent != HISTORY_PREFIX
        or relative.name != expected_name
    ):
        _fail("backfill output_file is outside the exact history namespace")
    output_path = _require_no_symlink_components(root, relative)
    frame, exact = _read_history(output_path)
    output_bytes = output_path.read_bytes()
    bytes_sha = hashlib.sha256(output_bytes).hexdigest()
    canonical_sha = _sha256_frame(frame)
    for key, actual in (
        ("output_bytes_sha256", bytes_sha),
        ("output_sha256", canonical_sha),
        ("output_canonical_sha256", canonical_sha),
    ):
        value = manifest.get(key)
        if type(value) is not str or SHA256_RE.fullmatch(value) is None or value != actual:
            _fail(f"{key} mismatch")
    if _native_int(manifest.get("output_bytes"), "output_bytes", minimum=1) != len(output_bytes):
        _fail("output byte count mismatch")

    for column in exact.columns:
        if column in HISTORY_TEXT_COLUMNS:
            if not exact[column].map(lambda value: value == value.strip()).all():
                _fail(f"history CSV {column} contains surrounding whitespace")
            if (
                column in HISTORY_REQUIRED_TEXT_COLUMNS
                and exact[column].eq("").any()
            ):
                _fail(f"history CSV {column} must not be empty")
            continue
        allow_empty = column in HISTORY_NULLABLE_NUMERIC_COLUMNS
        for row_number, value in enumerate(exact[column].tolist(), start=2):
            number = _finite_text_number(
                value,
                f"history CSV row {row_number} {column}",
                allow_empty=allow_empty,
            )
            if (
                number is not None
                and column in HISTORY_INTEGER_COLUMNS
                and not number.is_integer()
            ):
                _fail(
                    f"history CSV row {row_number} {column} must be an integer"
                )
            if (
                number is not None
                and column in HISTORY_INTEGER_COLUMNS
                and number < 0.0
            ):
                _fail(
                    f"history CSV row {row_number} {column} must be nonnegative"
                )
            if (
                number is not None
                and column
                in HISTORY_FLOAT_BINARY_COLUMNS | HISTORY_NULLABLE_BINARY_COLUMNS
                and number not in {0.0, 1.0}
            ):
                _fail(
                    f"history CSV row {row_number} {column} must be binary"
                )
            if (
                number is not None
                and column in HISTORY_UNIT_INTERVAL_COLUMNS
                and not 0.0 <= number <= 1.0
            ):
                _fail(
                    f"history CSV row {row_number} {column} must be within [0, 1]"
                )

    signal_dates = exact["signal_date"]
    for column in ("signal_date", "buy_date", "target_exit_date", "actual_exit_date"):
        for row_number, value in enumerate(exact[column].tolist(), start=2):
            _ymd(value, f"history CSV row {row_number} {column}")
    if sorted(signal_dates.unique().tolist()) != target_dates:
        _fail("history CSV signal dates differ from the receipt")
    if exact.duplicated(["signal_date", "ts_code"]).any():
        _fail("history CSV contains duplicate signal_date/ts_code rows")
    if not exact["source_rank"].str.fullmatch(r"[1-9][0-9]*").all():
        _fail("history CSV source_rank must be a canonical positive integer")
    if not exact["exit_delay_days"].str.fullmatch(r"(?:0|[1-9][0-9]*)").all():
        _fail("history CSV exit_delay_days must be a canonical nonnegative integer")
    source_rank = exact["source_rank"].astype(int)
    if exact.duplicated(["signal_date", "source_rank"]).any():
        _fail("history CSV contains a duplicate signal_date/source_rank")
    if not exact["ts_code"].map(lambda value: CODE_RE.fullmatch(value) is not None).all():
        _fail("history CSV contains a noncanonical ts_code")
    actual_order = list(zip(signal_dates.tolist(), source_rank.tolist(), exact["ts_code"].tolist()))
    if actual_order != sorted(actual_order):
        _fail("history CSV rows are not stably sorted")
    open_positions = {
        value: index for index, value in enumerate(verified_open_dates)
    }
    config = AuctionV3Config()
    for row_index, row in exact.iterrows():
        label = f"history CSV row {row_index + 2}"
        signal_date = row["signal_date"]
        buy_date = row["buy_date"]
        target_exit_date = row["target_exit_date"]
        actual_exit_date = row["actual_exit_date"]
        try:
            signal_position = open_positions[signal_date]
            buy_position = open_positions[buy_date]
            target_position = open_positions[target_exit_date]
            actual_position = open_positions[actual_exit_date]
        except KeyError as exc:
            raise BackfillArtifactError(
                f"{label} references a date outside the verified open calendar"
            ) from exc
        if buy_position != signal_position + 1 or target_position != buy_position + 1:
            _fail(f"{label} violates the D/T/T+1 calendar sequence")
        delay = int(row["exit_delay_days"])
        if actual_position < target_position or actual_position - target_position != delay:
            _fail(f"{label} exit_delay_days differs from the verified calendar")
        for column in HISTORY_EXACT_BINARY_COLUMNS:
            if row[column] not in {"0", "1"}:
                _fail(f"{label} {column} must be exact binary text")
        if row["public_market_buyable"] != row["market_fill"]:
            _fail(f"{label} public market buyability differs from market_fill")
        if row["actual_order_fill_observed"] != "0" or row["actual_order_fill"] != "":
            _fail(f"{label} must not claim an unverified actual order fill")
        if row["exit_on_time"] != ("1" if delay == 0 else "0"):
            _fail(f"{label} exit_on_time differs from exit_delay_days")
        stage_match = re.fullmatch(r"([1-9][0-9]*)→([1-9][0-9]*)", row["stage"])
        if stage_match is None or int(stage_match.group(2)) != int(stage_match.group(1)) + 1:
            _fail(f"{label} stage is noncanonical")
        limit_times = float(row["limit_times"])
        if not limit_times.is_integer() or int(limit_times) != int(stage_match.group(1)):
            _fail(f"{label} stage differs from limit_times")
        gross_return = float(row["gross_return"])
        net_return = float(row["net_return"])
        if gross_return <= -1.0 or not math.isclose(
            net_return,
            gross_return - config.cost_rate,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            _fail(f"{label} return targets are inconsistent")
        if row["profit_hit"] != ("1" if net_return > 0.0 else "0"):
            _fail(f"{label} profit_hit differs from net_return")
        if row["big_loss_hit"] != (
            "1" if net_return <= config.big_loss_threshold else "0"
        ):
            _fail(f"{label} big_loss_hit differs from net_return")
        if not math.isclose(
            float(row["actual_buy_gap"]),
            float(row["proposed_gap"]),
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            _fail(f"{label} proposed_gap differs from actual_buy_gap")
        for column in ("d_close", "buy_open", "auction_vwap", "exit_open"):
            if float(row[column]) <= 0.0:
                _fail(f"{label} {column} must be positive")
        if float(row["auction_amount"]) < 0.0:
            _fail(f"{label} auction_amount must be nonnegative")
        mechanism = float(row["mechanism_limit_pct"])
        if mechanism <= 0.0 or mechanism > config.max_mechanism_limit_pct:
            _fail(
                f"{label} mechanism_limit_pct is outside the production universe"
            )
    if not exact["history_source"].eq("tushare_compact_backfill").all():
        _fail("history source provenance mismatch")
    if not exact["history_contract_version"].eq(HISTORY_CONTRACT_VERSION).all():
        _fail("history contract version mismatch")
    if not exact["exit_policy_version"].eq(EXIT_POLICY_VERSION).all():
        _fail("history exit policy version mismatch")
    expected_take_profit = "" if EXIT_TAKE_PROFIT_PCT is None else str(EXIT_TAKE_PROFIT_PCT)
    expected_stop_loss = "" if EXIT_STOP_LOSS_PCT is None else str(EXIT_STOP_LOSS_PCT)
    if not exact["take_profit_pct"].eq(expected_take_profit).all():
        _fail("history take-profit policy mismatch")
    if not exact["stop_loss_pct"].eq(expected_stop_loss).all():
        _fail("history stop-loss policy mismatch")
    if not exact["latest_exit_time"].eq(EXIT_LATEST_TIME).all():
        _fail("history latest-exit policy mismatch")
    if "backfill_generated_at_utc" in exact.columns:
        _fail("history CSV must not contain a volatile per-row timestamp")

    produced_rows = _native_int(manifest.get("produced_rows"), "produced_rows", minimum=1)
    if produced_rows != len(frame):
        _fail("produced row count mismatch")
    official_rows = int(
        exact["auction_truth_source"].eq("tushare_stk_auction_o").sum()
    )
    if official_rows != len(frame):
        _fail("every backfill row must have official Tushare auction truth")
    if _native_int(manifest.get("official_auction_truth_rows"), "official_auction_truth_rows", minimum=1) != official_rows:
        _fail("official auction truth row count mismatch")
    coverage = manifest.get("auction_truth_coverage")
    if type(coverage) is not float or not math.isfinite(coverage) or coverage != 1.0:
        _fail("auction truth coverage must be the native float 1.0")
    endpoint_rows = manifest.get("endpoint_rows")
    expected_endpoints = {"daily", "stk_limit", "daily_basic", "limit_list_d", "stk_auction_o"}
    if not isinstance(endpoint_rows, dict) or set(endpoint_rows) != expected_endpoints:
        _fail("endpoint row counters mismatch")
    for name, value in endpoint_rows.items():
        _native_int(value, f"endpoint_rows.{name}")
    if endpoint_rows["daily"] <= 0 or endpoint_rows["stk_limit"] <= 0 or endpoint_rows["stk_auction_o"] <= 0:
        _fail("required endpoint row counters must be positive")

    covered = _covered_signal_dates(root / HISTORY_PREFIX)
    if _native_int(manifest.get("total_compact_signal_dates"), "total_compact_signal_dates", minimum=1) != len(covered):
        _fail("total compact signal-date count mismatch")
    if not set(target_window).issubset(covered):
        _fail("target window is not fully covered after backfill")
    live_capacity = len(target_window) - WALKFORWARD_WARMUP_DATES
    if live_capacity != TARGET_INDEPENDENT_OOS_DATES:
        _fail("produced live independent OOS capacity mismatch")
    return {
        "status": "produced",
        "output_file": raw_output,
        "produced_rows": len(frame),
        "produced_signal_dates": len(target_dates),
        "output_bytes_sha256": bytes_sha,
        "output_canonical_sha256": canonical_sha,
        "history_columns_sha256": EXPECTED_HISTORY_COLUMNS_SHA256,
        "live_independent_oos_capacity": live_capacity,
        "expected_dirty_paths": [
            CALENDAR_PATH.as_posix(),
            (HISTORY_PREFIX / "manifest_latest.json").as_posix(),
            raw_output,
        ],
    }


def validate_backfill_artifacts(
    root: Path,
    receipt_path: Path,
    *,
    start_date: str,
    end_date: str,
    max_missing_dates: int,
) -> dict[str, Any]:
    root = root.resolve()
    receipt = _read_json(receipt_path.absolute(), "backfill receipt")
    max_missing_dates = _native_int(
        max_missing_dates,
        "requested max_missing_dates",
        minimum=1,
    )
    _validate_common_receipt(
        receipt,
        start_date=_ymd(start_date, "start_date"),
        end_date=_ymd(end_date, "end_date"),
        max_missing_dates=max_missing_dates,
    )
    status_value = receipt.get("status")
    if status_value == "up_to_date":
        result = _validate_up_to_date(
            root,
            receipt,
            start_date=start_date,
            end_date=end_date,
        )
    elif status_value == "produced":
        result = _validate_produced(
            root,
            receipt,
            start_date=start_date,
            end_date=end_date,
            max_missing_dates=max_missing_dates,
        )
    else:
        _fail("backfill receipt status is not publishable")
    return {
        "schema_version": "decision_v11_backfill_validation_v1",
        "validated": True,
        **result,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate exact owner-scoped Decision V11 backfill artifacts"
    )
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--max-missing-dates", required=True, type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = validate_backfill_artifacts(
            Path(args.root),
            Path(args.receipt),
            start_date=args.start_date,
            end_date=args.end_date,
            max_missing_dates=args.max_missing_dates,
        )
    except BackfillArtifactError as exc:
        print(
            json.dumps(
                {
                    "schema_version": "decision_v11_backfill_validation_v1",
                    "status": "fail",
                    "reason": str(exc),
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
