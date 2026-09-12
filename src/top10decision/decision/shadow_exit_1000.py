"""Causal, after-close replay of the 10:00 / limit-up-hold exit policy.

This module has no I/O. Callbacks supply already provenance-checked exact-day
rows. One-minute bars use END timestamps: a decision on the bar ending 10:00
can only use the OPEN of the following bar (ending 10:01). This is explicitly
a minute-price proxy, never an observed order fill or an exact tick execution.
Existing opening-exit records must be preserved by the caller, not replayed
through this different policy.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

EXIT_POLICY_ID = "dc20_exit_1000_limit_hold_20260912_v1"
MINUTE_SCHEMA = "dc20_exit_1000_minutes_v1"
SETTLED_STATUS = "SETTLED_EXIT_1000_MINUTE_PROXY"
_CODE = re.compile(r"\d{6}\.(?:SH|SZ|BJ)\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_DATE = re.compile(r"20\d{6}\Z")
_EPS = 1e-8


class _DataError(ValueError):
    def __init__(self, status: str):
        self.status = status
        super().__init__(status)


def _date(value: Any) -> str:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise ValueError("date must be YYYYMMDD")
    datetime.strptime(value, "%Y%m%d")
    return value


def _number(value: Any, *, zero: bool = False) -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError("invalid finite number")
    result = float(value)
    if not math.isfinite(result) or not math.isfinite(result * 100) or (result < 0 if zero else result <= 0):
        raise ValueError("invalid positive number")
    return result


def _same(a: float, b: float) -> bool:
    return _at_limit(a, b)


def _at_limit(price: float, limit: float) -> bool:
    # Half-up cent ticks avoid treating an actual one-cent break as still sealed.
    return int(math.floor(price * 100 + 0.5)) == int(math.floor(limit * 100 + 0.5))


def _identity(row: Any, day: str, code: str, label: str) -> Mapping[str, Any]:
    if not isinstance(row, Mapping):
        raise _DataError(f"PENDING_EXIT_MISSING_{label}")
    if str(row.get("trade_date")) != day or row.get("ts_code") != code:
        raise _DataError(f"PENDING_EXIT_{label}_IDENTITY_CONFLICT")
    return row


def _daily(row: Mapping[str, Any]) -> dict[str, float]:
    try:
        out = {key: _number(row.get(key)) for key in ("open", "high", "low", "close", "pre_close")}
        out["vol"] = _number(row.get("vol"), zero=True)
    except (TypeError, ValueError, OverflowError) as exc:
        raise _DataError("PENDING_EXIT_INVALID_DAILY") from exc
    if out["low"] > min(out["open"], out["close"]) + _EPS or out["high"] + _EPS < max(out["open"], out["close"]) or out["low"] > out["high"]:
        raise _DataError("PENDING_EXIT_INVALID_DAILY")
    if out["vol"] == 0 and not all(_same(out["close"], out[k]) for k in ("open", "high", "low")):
        raise _DataError("PENDING_EXIT_ZERO_VOLUME_PRICE_CONFLICT")
    return out


def _limits(row: Mapping[str, Any], daily: Mapping[str, float], previous_close: float) -> tuple[float, float]:
    try:
        up, down = _number(row.get("up_limit")), _number(row.get("down_limit"))
        if up <= down:
            raise ValueError("inverted limits")
        limit_pre = row.get("pre_close")
        if limit_pre is not None and str(limit_pre).strip():
            if not _same(_number(limit_pre), daily["pre_close"]):
                raise _DataError("PENDING_EXIT_CORPORATE_ACTION_UNRESOLVED")
        elif not _same(previous_close, daily["pre_close"]):
            # A jump may be an adjustment, but a lone changed number does not
            # establish a comparable basis. Require the independent limit row.
            raise _DataError("PENDING_EXIT_CORPORATE_ACTION_UNRESOLVED")
    except _DataError:
        raise
    except (TypeError, ValueError, OverflowError) as exc:
        raise _DataError("PENDING_EXIT_INVALID_LIMITS") from exc
    if daily["vol"] > 0 and (daily["high"] > up + _EPS or daily["low"] < down - _EPS):
        raise _DataError("PENDING_EXIT_DAILY_LIMIT_CONFLICT")
    return up, down


def _expected_bar_ends(day: str) -> list[str]:
    result = []
    for start in ("09:31", "13:01"):
        first = datetime.strptime(day + " " + start, "%Y%m%d %H:%M")
        result.extend((first + timedelta(minutes=offset)).strftime("%Y-%m-%d %H:%M:%S") for offset in range(120))
    return result


def _iso_shanghai(value: str) -> str:
    return value.replace(" ", "T") + "+08:00"


def _minutes(payload: Any, day: str, code: str, daily: Mapping[str, float], up: float, down: float) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if payload is None:
        raise _DataError("PENDING_EXIT_MISSING_MINUTES")
    if not isinstance(payload, Mapping) or any(payload.get(key) != value for key, value in {
        "schema_version": MINUTE_SCHEMA, "ts_code": code, "trade_date": day,
        "timezone": "Asia/Shanghai", "timestamp_semantics": "BAR_END",
        "interval_seconds": 60, "complete_session": True,
    }.items()):
        raise _DataError("PENDING_EXIT_MINUTE_CONTRACT_INVALID")
    if type(payload.get("interval_seconds")) is not int or payload.get("complete_session") is not True:
        raise _DataError("PENDING_EXIT_MINUTE_CONTRACT_INVALID")
    rows = payload.get("rows")
    expected = _expected_bar_ends(day)
    if not isinstance(rows, list) or len(rows) != 240 or any(not isinstance(row, Mapping) for row in rows):
        raise _DataError("PENDING_EXIT_INCOMPLETE_MINUTES")
    if [row.get("bar_end") for row in rows] != expected:
        raise _DataError("PENDING_EXIT_MINUTE_TIME_CONFLICT")
    bindings = payload.get("source_files")
    if not isinstance(bindings, list) or not bindings:
        raise _DataError("PENDING_EXIT_MINUTE_PROVENANCE_MISSING")
    files: list[dict[str, str]] = []
    for item in bindings:
        if not isinstance(item, Mapping) or set(item) != {"path", "sha256"}:
            raise _DataError("PENDING_EXIT_MINUTE_PROVENANCE_INVALID")
        path = item["path"]
        if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path or any(part in ("", ".", "..") for part in path.split("/")) or not isinstance(item["sha256"], str) or not _SHA.fullmatch(item["sha256"]):
            raise _DataError("PENDING_EXIT_MINUTE_PROVENANCE_INVALID")
        if any(old["path"] == path for old in files):
            raise _DataError("PENDING_EXIT_MINUTE_PROVENANCE_INVALID")
        files.append(dict(item))
    normalized = []
    previous_minute_close = daily["open"]
    for source in rows:
        if "ts_code" in source and source["ts_code"] != code or "trade_date" in source and str(source["trade_date"]) != day:
            raise _DataError("PENDING_EXIT_MINUTE_IDENTITY_CONFLICT")
        try:
            row = {key: _number(source.get(key)) for key in ("open", "high", "low", "close")}
            row["vol"] = _number(source.get("vol"), zero=True)
            if "amount" in source:
                _number(source["amount"], zero=True)
        except (TypeError, ValueError, OverflowError) as exc:
            raise _DataError("PENDING_EXIT_INVALID_MINUTE_PRICE") from exc
        if row["low"] > min(row["open"], row["close"]) + _EPS or row["high"] + _EPS < max(row["open"], row["close"]) or row["low"] > row["high"] or row["high"] > up + _EPS or row["low"] < down - _EPS:
            raise _DataError("PENDING_EXIT_INVALID_MINUTE_PRICE")
        if row["vol"] == 0 and not all(_same(row["close"], row[key]) for key in ("open", "high", "low")):
            raise _DataError("PENDING_EXIT_ZERO_VOLUME_PRICE_CONFLICT")
        if row["vol"] == 0 and not _same(row["close"], previous_minute_close):
            raise _DataError("PENDING_EXIT_ZERO_VOLUME_PRICE_CONFLICT")
        row["bar_end"] = source["bar_end"]
        row["bar_start"] = (datetime.strptime(source["bar_end"], "%Y-%m-%d %H:%M:%S") - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        normalized.append(row)
        previous_minute_close = row["close"]
    if sum(row["vol"] for row in normalized) <= 0 or not _same(normalized[-1]["close"], daily["close"]) or max(row["high"] for row in normalized) > daily["high"] + _EPS or min(row["low"] for row in normalized) < daily["low"] - _EPS:
        raise _DataError("PENDING_EXIT_DAILY_MINUTE_CONFLICT")
    return normalized, files


def resolve_exit_1000(
    open_dates: Sequence[str], scheduled_exit_date: str, as_of_date: str,
    code: str, entry_price: float, t_close_price: float,
    load_daily: Callable[[str], Mapping[str, Any] | None],
    load_limits: Callable[[str], Mapping[str, Any] | None],
    load_minutes: Callable[[str], Mapping[str, Any] | None],
) -> tuple[dict[str, Any] | None, str]:
    """Return immutable-ready exit truth or an explicit pending status.

    ``as_of_date`` is the caller's last completed session cutoff, never a live
    intraday timestamp. Missing/invalid evidence blocks replay at that day; a
    later complete day cannot bridge it. Prices are gross: the caller applies
    its separately versioned costs exactly once. Official daily close/pre-close
    wealth links handle comparable-price adjustments, with limit pre-close
    cross-checks on every changed price basis. Provenance bytes are checked by
    the callbacks; this pure function validates identities/shape/price timing.
    """
    scheduled_exit_date, as_of_date = _date(scheduled_exit_date), _date(as_of_date)
    dates = list(open_dates)
    if not dates or dates != sorted(set(dates)) or any(_date(day) != day for day in dates):
        raise ValueError("open_dates must be a unique ordered strict calendar")
    if scheduled_exit_date not in dates or not isinstance(code, str) or not _CODE.fullmatch(code):
        raise ValueError("scheduled exit must be a trading date with exact code")
    entry, previous_close = _number(entry_price), _number(t_close_price)
    wealth = previous_close / entry
    if not math.isfinite(wealth) or wealth <= 0:
        raise ValueError("invalid entry wealth basis")
    if as_of_date < scheduled_exit_date:
        return None, "PENDING_T1"
    if as_of_date > dates[-1]:
        return None, "PENDING_EXIT_CALENDAR_COVERAGE"

    pending_decision: str | None = None
    reason = ""
    held = blocked = suspended = 0
    minute_files: dict[str, str] = {}
    wealth_chain: list[dict[str, Any]] = []
    start_index = dates.index(scheduled_exit_date)
    for day_index, day in enumerate(dates[start_index:], start=start_index):
        if day > as_of_date:
            break
        try:
            raw_daily = _identity(load_daily(day), day, code, "DAILY")
            daily = _daily(raw_daily)
            raw_limits = _identity(load_limits(day), day, code, "LIMITS")
            up, down = _limits(raw_limits, daily, previous_close)
            day_link = {"trade_date": day, "previous_close": previous_close,
                        "pre_close": daily["pre_close"], "close": daily["close"]}
            if daily["vol"] == 0:
                suspended += 1
                wealth *= daily["close"] / daily["pre_close"]
                previous_close = daily["close"]
                wealth_chain.append(dict(day_link, status="ZERO_VOLUME_NO_TRADE"))
                if not math.isfinite(wealth) or wealth <= 0:
                    raise _DataError("PENDING_EXIT_INVALID_WEALTH_CHAIN")
                continue
            bars, source_files = _minutes(load_minutes(day), day, code, daily, up, down)
        except _DataError as exc:
            return None, exc.status
        except (ValueError, OSError) as exc:
            # Only expected malformed/missing source failures are converted.
            # Software errors such as AttributeError/RuntimeError still surface.
            return None, "PENDING_EXIT_SOURCE_INVALID"
        for item in source_files:
            if item["path"] in minute_files and minute_files[item["path"]] != item["sha256"]:
                return None, "PENDING_EXIT_SOURCE_CHANGED"
            minute_files[item["path"]] = item["sha256"]
        seen_seal = False  # A different trading day has a different limit.
        held_at_1000 = False
        attempted_but_blocked = False
        for bar in bars:
            if pending_decision is not None:
                # An earlier decision cannot be cancelled by resealing. Do not
                # infer an executable OPEN from this bar's later high/low.
                if bar["vol"] > 0 and not _at_limit(bar["open"], down) and bar["open"] > down:
                    gross = wealth * bar["open"] / daily["pre_close"] - 1
                    if not math.isfinite(gross) or gross <= -1:
                        return None, "PENDING_EXIT_INVALID_WEALTH_CHAIN"
                    result = {
                        "exit_policy_id": EXIT_POLICY_ID, "exit_price": bar["open"],
                        "scheduled_exit_date": scheduled_exit_date,
                        "actual_exit_date": day, "actual_exit_time": _iso_shanghai(bar["bar_start"]),
                        "decision_time": _iso_shanghai(pending_decision),
                        "execution_bar_start": _iso_shanghai(bar["bar_start"]), "execution_bar_end": _iso_shanghai(bar["bar_end"]),
                        "timezone": "Asia/Shanghai",
                        "exit_time_semantics": "NEXT_BAR_OPEN_MINUTE_PROXY",
                        "exit_reason": reason, "gross_return": gross,
                        "price_basis": "OFFICIAL_DAILY_CLOSE_PRE_CLOSE_WEALTH_CHAIN",
                        "wealth_chain": wealth_chain + [dict(day_link, status="EXIT_MINUTE_PRICE", exit_price=bar["open"])],
                        "held_limit_up_sessions": held, "blocked_exit_sessions": blocked,
                        "suspended_exit_sessions": suspended,
                        "delayed_trading_days": day_index - start_index,
                        "minute_source_files": [{"path": path, "sha256": sha} for path, sha in sorted(minute_files.items())],
                        "research_only": True, "actual_execution_claimed": False,
                        "exit_capacity_verified": False,
                    }
                    return result, SETTLED_STATUS
                attempted_but_blocked = True
                continue
            sealed_at_end = _at_limit(bar["close"], up)
            # Prior completed-bar seal -> a later bar trading below limit is a
            # causal break even if that bar reseals by its close. A first bar's
            # high touching limit alone cannot prove the within-bar sequence.
            if seen_seal and bar["vol"] > 0 and bar["low"] < up and not _at_limit(bar["low"], up):
                pending_decision = bar["bar_end"]
                reason = "LIMIT_UP_BREAK_NEXT_BAR_OPEN"
            elif bar["bar_end"][11:16] == "10:00":
                if sealed_at_end:
                    held_at_1000 = True
                else:
                    pending_decision = bar["bar_end"]
                    reason = "TIME_1000_NEXT_BAR_OPEN"
            if sealed_at_end and bar["vol"] > 0:
                seen_seal = True
        if pending_decision is not None and attempted_but_blocked:
            blocked += 1
        elif held_at_1000:
            # A first break on the closing 15:00 bar has no next execution
            # bar today. This day was held because of its 10:00 seal; count
            # it once as held, not as an unobserved liquidity failure.
            held += 1
        wealth *= daily["close"] / daily["pre_close"]
        previous_close = daily["close"]
        wealth_chain.append(dict(day_link, status="PENDING_SELL" if pending_decision else "LIMIT_UP_HELD"))
        if not math.isfinite(wealth) or wealth <= 0:
            return None, "PENDING_EXIT_INVALID_WEALTH_CHAIN"
    if pending_decision is not None:
        return None, "PENDING_EXIT_UNSELLABLE"
    if held:
        return None, "PENDING_EXIT_LIMIT_UP_HELD"
    if suspended:
        return None, "PENDING_EXIT_SUSPENDED"
    return None, "PENDING_EXIT_UNRESOLVED"


__all__ = ["EXIT_POLICY_ID", "MINUTE_SCHEMA", "SETTLED_STATUS", "resolve_exit_1000"]
