"""Pinned 10:00 exit loop with independently verified nontrading-day admission.

Only an exact daily None can consume a private VerifiedNoTradingSession. No
calendar filtering, daily/minute fabrication, wealth mark, cost or sell-time
change is made. The caller still applies its fee once. Missing proof preserves
old pending behavior. Pending results remain None; callers may wrap their
proof lookup to retain the consumed evidence for a separate pending audit.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from top10decision.decision import shadow_exit_1000 as original
from top10decision.decision.shadow_exit_1000 import (
    EXIT_POLICY_ID, SETTLED_STATUS, _CODE, _DataError, _date, _number,
    _identity, _daily, _limits, _minutes, _at_limit, _iso_shanghai,
)
from work.profit_1000_upgrade import nontrading_session_truth as truth

ADAPTER_ID = "dc20_research_nontrading_exit_1000_20260913_v1"
KERNEL_SHA256 = "23d1e693bbf4e38771b107f434aab7cd40f82855e0f480e391d819778e05c5af"
_SELF_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_TRUTH_SHA = hashlib.sha256(Path(truth.__file__).read_bytes()).hexdigest()


def _guard():
    truth._guard()
    for path, expected in ((Path(original.__file__), KERNEL_SHA256),
                           (Path(__file__), _SELF_SHA), (Path(truth.__file__), _TRUTH_SHA)):
        if truth._sha(path) != expected:
            raise ValueError("NONTRADING_EXIT_DEPENDENCY_CHANGED")


def _accept(proof, day, code, as_of, consumed):
    if type(proof) is not truth.VerifiedNoTradingSession or proof not in truth._ISSUED:
        raise _DataError("PENDING_EXIT_NONTRADING_EVIDENCE_INVALID")
    if proof.source_policy_id != truth.SOURCE_POLICY_ID or any(getattr(proof, key) is not expected for key, expected in {
        "market_absence_verified": True, "can_advance_holding_day": True, "research_only": True,
        "production_activation_allowed": False, "actual_execution_claimed": False,
        "actual_capacity_verified": False, "settlement_allowed": False,
        "synthetic_ohlc_created": False, "provider_timestamp_semantics_confirmed": False,
    }.items()):
        raise _DataError("PENDING_EXIT_NONTRADING_EVIDENCE_INVALID")
    if (proof.trade_date, proof.ts_code) != (day, code) or as_of > proof.as_of_date:
        raise _DataError("PENDING_EXIT_NONTRADING_EVIDENCE_IDENTITY_CONFLICT")
    try:
        if not any(p._context is proof._context for p in consumed):
            proof.assert_unchanged()
    except (ValueError, OSError):
        raise _DataError("PENDING_EXIT_NONTRADING_EVIDENCE_INVALID") from None


def resolve_exit_1000_with_nontrading_sessions(
    open_dates, scheduled_exit_date, as_of_date, code, entry_price, t_close_price,
    load_daily, load_limits, load_minutes, *, load_nontrading_session=None,
):
    _guard()
    args = (open_dates, scheduled_exit_date, as_of_date, code, entry_price,
            t_close_price, load_daily, load_limits, load_minutes)
    consumed = []
    try:
        if load_nontrading_session is None:
            result = original.resolve_exit_1000(*args)
        else:
            if not callable(load_nontrading_session):
                raise ValueError("NONTRADING_PROOF_LOOKUP_MUST_BE_CALLABLE")
            result = _resolve(*args, load_nontrading_session=load_nontrading_session, consumed=consumed)
        checked = set()
        for proof in consumed:
            if id(proof._context) not in checked:
                try:
                    proof.assert_unchanged()
                except (ValueError, OSError):
                    return None, "PENDING_EXIT_NONTRADING_EVIDENCE_CHANGED"
                checked.add(id(proof._context))
        return result
    finally:
        _guard()


def _resolve(
    open_dates: Sequence[str], scheduled_exit_date: str, as_of_date: str,
    code: str, entry_price: float, t_close_price: float,
    load_daily: Callable[[str], Mapping[str, Any] | None],
    load_limits: Callable[[str], Mapping[str, Any] | None],
    load_minutes: Callable[[str], Mapping[str, Any] | None],
    *, load_nontrading_session, consumed,
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
            supplied_daily = load_daily(day)
            if supplied_daily is None:
                proof = load_nontrading_session(day)
                if proof is not None:
                    _accept(proof, day, code, as_of_date, consumed)
                    # No invented OHLC/wealth link; previous close and any
                    # irrevocable pending sell decision survive unchanged.
                    consumed.append(proof)
                    continue
            raw_daily = _identity(supplied_daily, day, code, "DAILY")
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
                    if consumed:
                        result.update(nontrading_adapter_id=ADAPTER_ID,
                            verified_nontrading_sessions=len(consumed),
                            nontrading_session_evidence=[p.evidence() for p in consumed],
                            provider_timestamp_semantics_confirmed=False,
                            production_activation_allowed=False)
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
    if suspended or consumed:
        return None, "PENDING_EXIT_SUSPENDED"
    return None, "PENDING_EXIT_UNRESOLVED"


__all__ = ["ADAPTER_ID", "resolve_exit_1000_with_nontrading_sessions"]
