"""Conditional BAR_END, fixed-decision +60-second execution sensitivity.

This is NOT a BAR_START interpretation or timestamp-confirmation test. It
compares the pinned exit resolver with later executable minute openings after
the SAME decision. No source mutation, training, NAV or activation is allowed.
Callbacks own provenance verification; this module snapshots their payloads and
checks that every consumed source remains identical through the comparison.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Mapping, Sequence

KERNEL_SHA256 = "23d1e693bbf4e38771b107f434aab7cd40f82855e0f480e391d819778e05c5af"
KERNEL_PATH = Path(__file__).resolve().parents[2] / "src/top10decision/decision/shadow_exit_1000.py"
LATENCY_POLICY_ID = "dc20_research_fixed_decision_exit_latency_60s_20260912_v1"
LATENCY_SECONDS = 60
_FLAGS = {"research_only": True, "timestamp_confirmed": False,
          "production_activation_allowed": False, "actual_execution_claimed": False}


class LatencySourceChanged(ValueError):
    """A pair has no valid interpretation when its shared source changes."""


def _kernel() -> ModuleType:
    body = KERNEL_PATH.read_bytes()
    if hashlib.sha256(body).hexdigest() != KERNEL_SHA256:
        raise ValueError("latency baseline kernel SHA256 mismatch")
    # Load precisely the bytes just verified, not a possibly cached import.
    module = ModuleType("_dc20_latency_pinned_exit_kernel")
    exec(compile(body, str(KERNEL_PATH), "exec"), module.__dict__)
    return module


def _payload_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


class _Snapshots:
    def __init__(self, callbacks: Mapping[str, Callable]):
        self.callbacks = callbacks
        self.payloads: dict[tuple[str, str], tuple[bytes, Any]] = {}
        self.changed = False

    def read(self, kind: str, day: str) -> Any:
        key = (kind, day)
        value = copy.deepcopy(self.callbacks[kind](day))
        body = _payload_bytes(value)
        if key in self.payloads and body != self.payloads[key][0]:
            self.changed = True
            raise LatencySourceChanged(f"source changed between latency arms: {kind} {day}")
        if key not in self.payloads:
            self.payloads[key] = (body, value)
        return copy.deepcopy(self.payloads[key][1])

    def callback(self, kind: str) -> Callable:
        return lambda day: self.read(kind, day)

    def check(self) -> None:
        if self.changed:
            raise LatencySourceChanged("source changed between latency arms")
        for kind, day in tuple(self.payloads):
            self.read(kind, day)

    def bindings(self) -> list[dict[str, Any]]:
        return [{"kind": kind, "trade_date": day, "payload_sha256": hashlib.sha256(body).hexdigest(),
                 "present": value is not None}
                for (kind, day), (body, value) in sorted(self.payloads.items())]


def _delayed(kernel, dates, scheduled, as_of, code, entry, t_close, baseline, sources):
    decision = datetime.fromisoformat(baseline["decision_time"])
    cutoff = decision + timedelta(seconds=LATENCY_SECONDS)
    previous_close = kernel._number(t_close)
    wealth = previous_close / kernel._number(entry)
    chain, minute_files = [], {}
    held = blocked = suspended = 0
    start = dates.index(scheduled)
    for index, day in enumerate(dates[start:], start=start):
        if day > as_of:
            break
        try:
            daily = kernel._daily(kernel._identity(sources.read("daily", day), day, code, "DAILY"))
            limits = kernel._identity(sources.read("limits", day), day, code, "LIMITS")
            up, down = kernel._limits(limits, daily, previous_close)
            day_link = {"trade_date": day, "previous_close": previous_close,
                        "pre_close": daily["pre_close"], "close": daily["close"]}
            if daily["vol"] == 0:
                suspended += 1
                wealth *= daily["close"] / daily["pre_close"]
                previous_close = daily["close"]
                chain.append(dict(day_link, status="ZERO_VOLUME_NO_TRADE"))
                if not math.isfinite(wealth) or wealth <= 0:
                    return None, "PENDING_EXIT_INVALID_WEALTH_CHAIN"
                continue
            bars, files = kernel._minutes(sources.read("minutes", day), day, code, daily, up, down)
        except kernel._DataError as exc:
            return None, exc.status
        except (ValueError, OSError):
            if sources.changed:
                raise LatencySourceChanged("source changed between latency arms")
            return None, "PENDING_EXIT_SOURCE_INVALID"
        for item in files:
            path, sha = item["path"], item["sha256"]
            if path in minute_files and minute_files[path] != sha:
                return None, "PENDING_EXIT_SOURCE_CHANGED"
            minute_files[path] = sha
        held_at_1000 = attempted_but_blocked = False
        for bar in bars:
            bar_end = datetime.fromisoformat(kernel._iso_shanghai(bar["bar_end"]))
            bar_start = datetime.fromisoformat(kernel._iso_shanghai(bar["bar_start"]))
            if bar_end < decision and bar["bar_end"][11:16] == "10:00":
                held_at_1000 = kernel._at_limit(bar["close"], up)
            if bar_start < cutoff:
                continue
            # The original sell decision is irrevocable: resealing does not
            # restart a 10:00 decision, and a liquidity delay is not added twice.
            if bar["vol"] > 0 and not kernel._at_limit(bar["open"], down) and bar["open"] > down:
                gross = wealth * bar["open"] / daily["pre_close"] - 1
                if not math.isfinite(gross) or gross <= -1:
                    return None, "PENDING_EXIT_INVALID_WEALTH_CHAIN"
                result = {
                    "exit_policy_id": LATENCY_POLICY_ID, "baseline_exit_policy_id": kernel.EXIT_POLICY_ID,
                    "exit_price": bar["open"], "scheduled_exit_date": scheduled,
                    "actual_exit_date": day, "actual_exit_time": kernel._iso_shanghai(bar["bar_start"]),
                    "decision_time": baseline["decision_time"],
                    "earliest_execution_time": cutoff.isoformat(), "execution_delay_seconds": LATENCY_SECONDS,
                    "execution_bar_start": kernel._iso_shanghai(bar["bar_start"]),
                    "execution_bar_end": kernel._iso_shanghai(bar["bar_end"]), "timezone": "Asia/Shanghai",
                    "exit_time_semantics": "FIXED_DECISION_PLUS_60_SECONDS_ELIGIBLE_BAR_OPEN_PROXY",
                    "exit_reason": baseline["exit_reason"], "gross_return": gross,
                    "price_basis": "OFFICIAL_DAILY_CLOSE_PRE_CLOSE_WEALTH_CHAIN",
                    "wealth_chain": chain + [dict(day_link, status="EXIT_MINUTE_PRICE", exit_price=bar["open"])],
                    "held_limit_up_sessions": held, "blocked_exit_sessions": blocked,
                    "suspended_exit_sessions": suspended, "delayed_trading_days": index - start,
                    "minute_source_files": [{"path": path, "sha256": sha} for path, sha in sorted(minute_files.items())],
                    "exit_capacity_verified": False, **_FLAGS,
                }
                return result, kernel.SETTLED_STATUS
            attempted_but_blocked = True
        if attempted_but_blocked:
            blocked += 1
        elif held_at_1000:
            held += 1
        wealth *= daily["close"] / daily["pre_close"]
        previous_close = daily["close"]
        chain.append(dict(day_link, status="PENDING_SELL" if day >= decision.strftime("%Y%m%d") else "LIMIT_UP_HELD"))
        if not math.isfinite(wealth) or wealth <= 0:
            return None, "PENDING_EXIT_INVALID_WEALTH_CHAIN"
    return None, "PENDING_EXIT_UNSELLABLE"


def _arm(result: dict | None, status: str) -> dict:
    return {"status": status, "result": result,
            "net_return_45bp": None if result is None else result["gross_return"] - 0.0045,
            "net_return_90bp": None if result is None else result["gross_return"] - 0.009,
            "cost_application": "GROSS_RETURN_MINUS_SINGLE_TOTAL_COST", **_FLAGS}


def compare_exit_latency(
    open_dates: Sequence[str], scheduled_exit_date: str, as_of_date: str,
    code: str, entry_price: float, t_close_price: float,
    load_daily: Callable[[str], Mapping[str, Any] | None],
    load_limits: Callable[[str], Mapping[str, Any] | None],
    load_minutes: Callable[[str], Mapping[str, Any] | None],
) -> dict[str, Any]:
    """Return matched baseline/stress arms; unresolved prices remain ``None``.

    Callbacks may be invoked more than once to verify immutable evidence. Bad
    calendars/arguments and changing sources raise; the pinned kernel's explicit
    missing/invalid market-data statuses remain pending in both arms. A settled
    baseline and unresolved stress remain a pending pair, never a zero return.
    """
    kernel = _kernel()
    dates = list(open_dates)
    sources = _Snapshots({"daily": load_daily, "limits": load_limits, "minutes": load_minutes})
    baseline, status = kernel.resolve_exit_1000(
        dates, scheduled_exit_date, as_of_date, code, entry_price, t_close_price,
        sources.callback("daily"), sources.callback("limits"), sources.callback("minutes"))
    if sources.changed:
        raise LatencySourceChanged("source changed during baseline")
    if baseline is None:
        delayed, delayed_status = None, status
    else:
        delayed, delayed_status = _delayed(kernel, dates, scheduled_exit_date, as_of_date,
                                          code, entry_price, t_close_price, baseline, sources)
    sources.check()
    base_arm, delay_arm = _arm(baseline, status), _arm(delayed, delayed_status)
    both = baseline is not None and delayed is not None
    result = {"schema_version": LATENCY_POLICY_ID, "baseline_kernel_sha256": KERNEL_SHA256,
              "status": "COMPLETE_PAIR" if both else "PENDING_PAIR", "baseline": base_arm, "delayed": delay_arm,
              "latency_seconds": LATENCY_SECONDS, "timestamp_semantics": "BAR_END_CONDITIONAL_ASSUMPTION",
              "bar_start_interpretation_tested": False,
              "sign_flip_definition": "STRICT_OPPOSITE_SIGNS_ZERO_EXCLUDED",
              "source_snapshots": sources.bindings(),
              "source_snapshots_verified_unchanged": True, "nav_computed": False, **_FLAGS}
    for bp in (45, 90):
        left, right = base_arm[f"net_return_{bp}bp"], delay_arm[f"net_return_{bp}bp"]
        result[f"delta_net_return_{bp}bp"] = right - left if both else None
        result[f"sign_flip_{bp}bp"] = (left * right < 0) if both else None
    return result


__all__ = ["compare_exit_latency", "LatencySourceChanged", "LATENCY_POLICY_ID", "KERNEL_SHA256"]
