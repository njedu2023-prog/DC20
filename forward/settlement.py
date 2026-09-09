"""Pure, exact-session verification for frozen forward candidates.

The caller supplies the last *closed and source-verified* SSE session. This
module neither fetches prices nor guesses that today's trading has finished.
Prices and returns are explicitly daily-open observations, not actual fills.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any


DATE_RE = re.compile(r"20\d{6}")
CODE_RE = re.compile(r"\d{6}\.(?:SH|SZ|BJ)")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
PRICE_FIELDS = ("open", "high", "low", "close", "up_limit", "down_limit")
REQUIRED_MARKET_FIELDS = {"ts_code", "trade_date", *PRICE_FIELDS, "vol"}


def _date(value: Any, label: str) -> str:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be exact YYYYMMDD")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"{label} is not a calendar date") from exc
    return value


def _number(value: Any, label: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric, not boolean")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be finite numeric data") from exc
    if not math.isfinite(result) or (result < 0 if allow_zero else result <= 0):
        raise ValueError(f"{label} must be finite and {'nonnegative' if allow_zero else 'positive'}")
    return result


def _hash(value: Any) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("truth evidence must contain finite JSON values") from exc
    return hashlib.sha256(raw).hexdigest()


def _calendar(open_dates: Sequence[str]) -> list[str]:
    if not isinstance(open_dates, Sequence) or isinstance(open_dates, (str, bytes)):
        raise ValueError("open_dates must be ordered strict SSE sessions")
    dates = [_date(value, "SSE session") for value in open_dates]
    if not dates or dates != sorted(set(dates)):
        raise ValueError("open_dates must be nonempty, unique and strictly increasing")
    return dates


class _Market:
    """Read and hash a partition only when a due frozen candidate needs it."""

    def __init__(self, market_by_date: Mapping[str, Any], as_of_date: str):
        if not isinstance(market_by_date, Mapping):
            raise ValueError("market_by_date must be a date-to-rows mapping")
        self.source = market_by_date
        self.as_of_date = as_of_date
        self.cache: dict[str, tuple[dict[str, dict], str | None]] = {}

    def get(self, date: str, code: str) -> tuple[dict | None, dict]:
        if date > self.as_of_date:
            raise ValueError("future market truth is forbidden")
        if date not in self.cache:
            raw = self.source.get(date)
            if raw is None:
                self.cache[date] = ({}, None)
            else:
                if not isinstance(raw, list):
                    raise ValueError(f"market partition {date} must be a list")
                mapped: dict[str, dict] = {}
                for original in raw:
                    if not isinstance(original, dict) or not REQUIRED_MARKET_FIELDS.issubset(original):
                        raise ValueError(f"market partition {date} missing required fields")
                    if _date(original["trade_date"], "market trade_date") != date:
                        raise ValueError(f"mixed market dates in partition {date}")
                    symbol = original["ts_code"]
                    if not isinstance(symbol, str) or CODE_RE.fullmatch(symbol) is None:
                        raise ValueError(f"invalid market stock code in {date}")
                    if symbol in mapped:
                        raise ValueError(f"duplicate market code {symbol} in {date}")
                    values = {field: _number(original[field], f"{date}/{symbol}/{field}")
                              for field in PRICE_FIELDS}
                    values["vol"] = _number(original["vol"], f"{date}/{symbol}/vol", allow_zero=True)
                    if not (values["down_limit"] < values["up_limit"]
                            and values["down_limit"] <= values["low"]
                            <= min(values["open"], values["close"])
                            <= max(values["open"], values["close"])
                            <= values["high"] <= values["up_limit"]):
                        raise ValueError(f"inconsistent OHLC or price limits for {date}/{symbol}")
                    mapped[symbol] = dict(values, row_sha256=_hash(original))
                self.cache[date] = (mapped, _hash(raw))
        mapped, partition_sha = self.cache[date]
        row = mapped.get(code)
        evidence = {
            "trade_date": date,
            "ts_code": code,
            "status": "MISSING_PARTITION" if partition_sha is None else "MISSING_ROW" if row is None else "OBSERVED",
            "partition_sha256": partition_sha,
            "row_sha256": row["row_sha256"] if row else None,
        }
        return row, evidence


def verify_day(day: Mapping[str, Any], market_by_date: Mapping[str, Any],
               open_dates: Sequence[str], *, as_of_date: str, costs_bps: float = 45.0,
               corporate_action_evidence: Mapping[str, Any] | None = None) -> dict:
    """Verify all frozen members without mutating inputs or reading future data.

Missing partitions or absent stock rows remain MISSING. Structurally invalid
partitions (including mixed dates, duplicates and impossible prices) reject the
event, so contaminated data cannot become a failed prediction or zero return.

Settlement additionally requires hash-addressed evidence that no corporate
action occurred between entry and exit. An adjusted-price label alone is not
sufficient; comparable adjusted input partitions are not yet supported here.
Without that assurance, promotion truth remains usable but returns stay null.
"""
    if not isinstance(day, Mapping):
        raise ValueError("frozen day must be an object")
    d = _date(day.get("signal_date"), "signal_date")
    t = _date(day.get("exec_date"), "exec_date")
    t1 = _date(day.get("exit_date"), "exit_date")
    as_of = _date(as_of_date, "as_of_date")
    dates = _calendar(open_dates)
    if d not in dates or as_of not in dates or as_of < d:
        raise ValueError("D and as_of_date must be strict SSE sessions with as_of >= D")
    offset = dates.index(d)
    if dates[offset + 1:offset + 3] != [t, t1]:
        raise ValueError("D/T/T+1 must be adjacent strict SSE sessions")
    freeze_sha = day.get("freeze_sha256")
    if not isinstance(freeze_sha, str) or SHA256_RE.fullmatch(freeze_sha) is None:
        raise ValueError("day.freeze_sha256 must bind an admitted immutable day")
    frozen = day.get("rows")
    if not isinstance(frozen, list) or len(frozen) > 10:
        raise ValueError("frozen rows must contain 0..10 actual candidates")
    codes = []
    for row in frozen:
        code = row.get("ts_code") if isinstance(row, Mapping) else None
        if not isinstance(code, str) or CODE_RE.fullmatch(code) is None:
            raise ValueError("frozen candidate code is invalid")
        codes.append(code)
    if len(codes) != len(set(codes)):
        raise ValueError("frozen candidate codes must be unique")
    if corporate_action_evidence is not None:
        if not isinstance(corporate_action_evidence, Mapping):
            raise ValueError("corporate_action_evidence must be a code-to-evidence mapping")
        if set(corporate_action_evidence) - set(codes):
            raise ValueError("corporate action evidence must bind only frozen members")
    cost = _number(costs_bps, "costs_bps", allow_zero=True)
    market = _Market(market_by_date, as_of)
    exit_sessions = dates[offset + 2:dates.index(as_of) + 1]
    rows = []
    for code in codes:
        result = {
            "ts_code": code, "t_status": "PENDING", "t1_status": "PENDING",
            "net_return": None, "slot_return": None, "entry_price": None,
            "exit_price": None, "actual_exit_date": None,
            "price_basis": "daily_open_proxy", "truth_evidence": [],
            "t_evidence": [], "t1_evidence": [],
            "corporate_action_evidence": None, "settlement_blocker": None,
            "blocked_exit_sessions": 0,
        }
        rows.append(result)
        if t > as_of:
            continue
        entry, evidence = market.get(t, code)
        result["truth_evidence"].append(evidence)
        result["t_evidence"].append(dict(evidence))
        if entry is None:
            result["t_status"] = "MISSING"
            if t1 <= as_of:
                result["t1_status"] = "MISSING"
            continue
        result["t_status"] = ("PROMOTED" if entry["vol"] > 0 and entry["close"] >= entry["up_limit"]
                              else "NOT_PROMOTED")
        fill = entry["vol"] > 0 and entry["open"] < entry["up_limit"]
        if fill:
            result["entry_price"] = entry["open"]
        if t1 > as_of:
            continue
        if not fill:
            result.update(t1_status="NO_FILL", slot_return=0.0)
            continue
        for session in exit_sessions:
            exit_row, evidence = market.get(session, code)
            result["truth_evidence"].append(evidence)
            result["t1_evidence"].append(dict(evidence))
            if exit_row is None:
                result["t1_status"] = "MISSING"
                break
            if exit_row["vol"] == 0 or exit_row["open"] <= exit_row["down_limit"]:
                result["blocked_exit_sessions"] += 1
                result["t1_status"] = "EXIT_BLOCKED"
                continue
            gross = exit_row["open"] / entry["open"] - 1.0
            net = gross - cost / 10000.0
            if not math.isfinite(gross) or not math.isfinite(net):
                raise ValueError(f"nonfinite return for frozen candidate {code}")
            assurance = (corporate_action_evidence.get(code)
                         if corporate_action_evidence is not None else None)
            if assurance is not None:
                required = {"ts_code", "entry_date", "through_date", "basis", "source_sha256"}
                if not isinstance(assurance, Mapping) or set(assurance) != required:
                    raise ValueError("corporate action evidence must have the exact evidence schema")
                through = _date(assurance["through_date"], "corporate action through_date")
                if (assurance["ts_code"] != code or assurance["entry_date"] != t
                        or through not in dates or not t <= through <= as_of):
                    raise ValueError("corporate action evidence has inconsistent stock or session binding")
                if assurance["basis"] != "NO_CORPORATE_ACTION_IN_WINDOW":
                    raise ValueError("only proven no-corporate-action windows are currently supported")
                source_sha = assurance["source_sha256"]
                if not isinstance(source_sha, str) or SHA256_RE.fullmatch(source_sha) is None:
                    raise ValueError("corporate action evidence must bind a source SHA256")
                result["corporate_action_evidence"] = dict(assurance, evidence_sha256=_hash(assurance))
            if assurance is None or through < session:
                result.update(t1_status="MISSING", settlement_blocker="CORPORATE_ACTION_REVIEW_REQUIRED")
                break
            result.update(t1_status="SETTLED", net_return=net, slot_return=net,
                          exit_price=exit_row["open"], actual_exit_date=session)
            break
    return {
        "schema_version": "dc20_forward_verification_v1",
        "signal_date": d, "exec_date": t, "exit_date": t1,
        "as_of_date": as_of, "freeze_sha256": freeze_sha,
        "members_sha256": _hash(codes), "costs_bps": cost,
        "price_basis": "daily_open_proxy", "research_only": True, "rows": rows,
    }
