from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Iterable

import numpy as np
import pandas as pd


MAX_ALLOWED_LIMIT_PCT = 10.0
RULE_VERSION = "a_share_price_limit_le_10_v2"
RECENT_LISTING_GUARD_DAYS = 14


def normalize_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    digits = "".join(ch for ch in text.split(".", 1)[0] if ch.isdigit())
    if len(digits) < 6:
        digits = "".join(ch for ch in text if ch.isdigit())
    return digits[-6:] if len(digits) >= 6 else ""


def _finite(value: Any) -> float:
    try:
        number = float(value)
    except Exception:
        return float("nan")
    return number if math.isfinite(number) else float("nan")


def _as_limit_pct(value: Any) -> float:
    number = _finite(value)
    if not math.isfinite(number) or number <= 0:
        return float("nan")
    return number * 100.0 if number <= 1.0 else number


def _text_has_no_limit(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    compact = re.sub(r"[\s_-]+", "", text)
    return any(token in compact for token in ("nolimit", "unlimited", "notlimited", "无涨跌幅", "不设涨跌幅"))


def _ymd(value: Any) -> datetime | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())[:8]
    if len(digits) != 8:
        return None
    try:
        return datetime.strptime(digits, "%Y%m%d")
    except ValueError:
        return None


def _recent_listing_guard(row: pd.Series) -> bool:
    trade_date = next((dt for key in ("trade_date", "signal_date") if (dt := _ymd(row.get(key))) is not None), None)
    list_date = next((dt for key in ("list_date", "listing_date") if (dt := _ymd(row.get(key))) is not None), None)
    if trade_date is None or list_date is None:
        return False
    age = (trade_date - list_date).days
    return 0 <= age <= RECENT_LISTING_GUARD_DAYS


def _board_contract(code: str, name: str) -> tuple[str, float, str]:
    if code.startswith(("300", "301")):
        return "CHINEXT", 20.0, "growth_board_20pct"
    if code.startswith(("688", "689")):
        return "STAR", 20.0, "star_market_20pct"
    if code.startswith(("4", "8", "920")):
        return "BSE", 30.0, "beijing_exchange_30pct"
    if "ST" in str(name or "").upper():
        return "ST", 5.0, "st_5pct"
    if code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return "MAIN", 10.0, "main_board_10pct"
    return "UNKNOWN", 10.0, "unknown_code_conservative_10pct"


def _explicit_limit_pct(row: pd.Series, columns: Iterable[str]) -> tuple[float, str]:
    for column in columns:
        if column not in row.index:
            continue
        pct = _as_limit_pct(row.get(column))
        if math.isfinite(pct):
            return pct, f"column:{column}"

    up_limit = _finite(row.get("up_limit"))
    pre_close = _finite(row.get("pre_close"))
    if not math.isfinite(pre_close) or pre_close <= 0:
        pre_close = _finite(row.get("pre_close_est"))
    if up_limit > 0 and pre_close > 0:
        pct = (up_limit / pre_close - 1.0) * 100.0
        if 1.0 <= pct <= 100.0:
            return pct, "derived:up_limit/pre_close"
    return float("nan"), ""


def annotate_standard_limit_universe(
    frame: pd.DataFrame,
    *,
    code_col: str = "ts_code",
    name_col: str = "name",
    explicit_limit_cols: Iterable[str] = (
        "decision_limit_pct",
        "limit_ratio",
        "price_limit_ratio",
        "price_limit_pct",
        "limit_pct",
    ),
) -> pd.DataFrame:
    """Attach one auditable <=10% price-limit eligibility decision per row."""
    out = frame.copy()
    if out.empty:
        out["decision_board"] = pd.Series(dtype="string")
        out["decision_limit_pct"] = pd.Series(dtype=float)
        out["decision_universe_eligible"] = pd.Series(dtype=int)
        out["decision_universe_reason"] = pd.Series(dtype="string")
        out["decision_universe_rule"] = RULE_VERSION
        return out

    records: list[tuple[str, float, int, str]] = []
    for _, row in out.iterrows():
        code = normalize_code(row.get(code_col))
        name = str(row.get(name_col, "") or "")
        board, board_pct, board_reason = _board_contract(code, name)

        no_limit_col = next(
            (
                column
                for column in ("limit_type", "limit_type_t1", "price_limit_type", "listing_limit_type")
                if column in row.index and _text_has_no_limit(row.get(column))
            ),
            "",
        )
        explicit_pct, explicit_source = _explicit_limit_pct(row, explicit_limit_cols)

        # Exchange prices are rounded to cents. A nominal 10% main-board limit
        # can therefore look like 10.05%-10.5% when derived from two prices.
        # Normalize only the derived ratio; explicit mechanism fields remain
        # authoritative and are never relaxed.
        if (
            explicit_source == "derived:up_limit/pre_close"
            and board in {"MAIN", "ST"}
            and math.isfinite(explicit_pct)
            and explicit_pct <= board_pct + 1.0
        ):
            explicit_pct = board_pct
            explicit_source = f"{explicit_source}:tick_normalized"

        # Board rules are authoritative for 20%/30% venues. Explicit daily limits
        # catch IPO/no-limit and other exceptional mechanisms on main boards.
        effective_pct = max(board_pct, explicit_pct) if math.isfinite(explicit_pct) else board_pct
        if no_limit_col:
            eligible = 0
            reason = f"excluded_no_price_limit:{no_limit_col}"
            effective_pct = max(effective_pct, 100.0)
        elif _recent_listing_guard(row):
            eligible = 0
            reason = "excluded_recent_listing_unrestricted_window"
            effective_pct = max(effective_pct, 100.0)
        elif effective_pct > MAX_ALLOWED_LIMIT_PCT + 0.05:
            eligible = 0
            source = explicit_source or board_reason
            reason = f"excluded_limit_gt_10:{effective_pct:.2f}%:{source}"
        elif not code:
            eligible = 0
            reason = "excluded_invalid_code"
        else:
            eligible = 1
            reason = f"eligible_limit_le_10:{effective_pct:.2f}%:{explicit_source or board_reason}"
        records.append((board, effective_pct, eligible, reason))

    out["decision_board"] = [item[0] for item in records]
    out["decision_limit_pct"] = np.asarray([item[1] for item in records], dtype=float)
    out["decision_universe_eligible"] = np.asarray([item[2] for item in records], dtype=int)
    out["decision_universe_reason"] = [item[3] for item in records]
    out["decision_universe_rule"] = RULE_VERSION
    return out


def filter_standard_limit_universe(
    frame: pd.DataFrame,
    **kwargs: Any,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    annotated = annotate_standard_limit_universe(frame, **kwargs)
    eligible = annotated[annotated["decision_universe_eligible"].eq(1)].copy()
    rejected = annotated[annotated["decision_universe_eligible"].ne(1)].copy()
    reasons = rejected["decision_universe_reason"].value_counts(dropna=False).to_dict()
    audit = {
        "rule": RULE_VERSION,
        "max_allowed_limit_pct": MAX_ALLOWED_LIMIT_PCT,
        "input_rows": int(len(annotated)),
        "eligible_rows": int(len(eligible)),
        "rejected_rows": int(len(rejected)),
        "rejected_reasons": {str(key): int(value) for key, value in reasons.items()},
    }
    return eligible.reset_index(drop=True), audit


__all__ = [
    "MAX_ALLOWED_LIMIT_PCT",
    "RULE_VERSION",
    "annotate_standard_limit_universe",
    "filter_standard_limit_universe",
    "normalize_code",
]
