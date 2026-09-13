"""Dormant v4 formal-entry candidate with native v3 price/capacity split. No production dispatch, writer, or CLI.

This *preparation* consumes the research canonical codec to check economic
parity. It cannot produce a formal frozen selection or a settled ledger row.
Any future production implementation needs a separately reviewed source loader,
immutable D-time policy assignment, version dispatch, and exit-truth integration.
Calling the preview is not activation, even when all entry evidence is valid.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from work.profit_1000_upgrade import auction_truth_v3 as auction_truth, policy_v3

POLICY_ID = "dc20_primary_profit_shadow_canonical_price_capacity_split_v4"
POLICY_PATH = Path(__file__).with_name("FORMAL_ENTRY_V4_POLICY.json")
PREVIEW_SCHEMA = "dc20_formal_shadow_entry_v4_preparation_preview"
SHANGHAI = ZoneInfo("Asia/Shanghai")
NOTIONAL = Decimal("100000")
PARTICIPATION = Decimal("0.01")
COST_RATE = Decimal("0.0045")
EXIT_POLICY_ID = "dc20_exit_1000_limit_hold_20260912_v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_CODE = re.compile(r"\d{6}\.(SH|SZ)\Z")


class PreparationError(ValueError):
    """An invalid preparation contract, never permission to alter old data."""


def _require(condition, reason):
    if not condition:
        raise PreparationError(reason)


def _date(value):
    _require(isinstance(value, str) and re.fullmatch(r"20\d{6}", value) is not None,
             "INVALID_DATE")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError:
        raise PreparationError("INVALID_DATE") from None
    return value


def _stamp(value):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        _require(stamp.tzinfo is not None, "TIMESTAMP_REQUIRES_TIMEZONE")
        return stamp.astimezone(SHANGHAI)
    except (ValueError, TypeError, AttributeError):
        raise PreparationError("INVALID_TIMESTAMP") from None


def _number(value, *, positive=False):
    _require(not isinstance(value, bool) and isinstance(value, (str, int, float)), "INVALID_NUMBER")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise PreparationError("INVALID_NUMBER") from None
    _require(number.is_finite() and number.adjusted() < 24 and
             (number > 0 if positive else number >= 0), "INVALID_NUMBER")
    return number


def _cent(value):
    return _number(value, positive=True).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            _require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    def constant(_):
        raise PreparationError("NONFINITE_JSON")
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError):
        raise PreparationError("INVALID_JSON") from None


def policy_binding():
    """Require the complete dormant definition, not a mutable activation flag."""
    raw = POLICY_PATH.read_bytes()
    policy = _json(raw)
    expected = {
        "schema_version": "dc20_formal_shadow_entry_policy_v4_preparation",
        "policy_id": POLICY_ID, "status": "PREPARED_NOT_ACTIVE",
        "effective_signal_date": None, "production_integrated": False,
        "entry_priority": ["TUSHARE_STK_AUCTION_PRICE", "EXACT_T_DAILY_OPEN_PROXY_AFTER_VALID_UNAVAILABLE_RECEIPT"],
        "excluded_source": "TUSHARE_STK_AUCTION_O_ALL_PRICES_AND_AMOUNT",
        "invalid_or_conflicting_source": "PENDING_NOT_NO_FILL_NOT_ZERO",
        "missing_source_without_receipt": "PENDING_NOT_FALLBACK",
        "frozen_cap_required": False,
        "opening_limit_up_rule": "NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED_PROXY_ONLY",
        "zero_canonical_volume_rule": "NO_FILL_CANONICAL_AUCTION_ZERO_VOLUME",
        "capacity_rule": "QUALIFIED_CANONICAL_AMOUNT_1_PERCENT_ELSE_PRICE_ONLY_CAPACITY_UNKNOWN",
        "shadow_notional_cny": 100000, "maximum_auction_participation": 0.01,
        "round_trip_cost_rate": 0.0045, "cost_version": "dc20_shadow_cost_v1_45bp",
        "exit_policy_id": EXIT_POLICY_ID, "ordinary_exit_decision_time": "10:00:00",
        "limit_up_hold": "HOLD_WHILE_OBSERVED_SEALED_USING_EACH_SESSION_LIMIT",
        "missing_exit_truth": "PENDING_NO_DAILY_OPEN_FALLBACK",
        "always_record_frozen_top1_top2": True, "profit_threshold_skip_allowed": False,
        "existing_frozen_records": "KEEP_ORIGINAL_POLICY_AND_BYTES",
        "activation_scope": "FUTURE_NEW_NATURAL_D_FREEZE_ONLY",
        "research_economic_reference": policy_v3.ENTRY_POLICY_ID,
        "research_source_reference": auction_truth.SOURCE_POLICY_ID,
        "price_qualification": "NATIVE_V3_POSTHOC_DAILY_OPEN_CENT_MATCH_REPORTED_PRICE_PRESERVED",
        "unqualified_amount": "CAPACITY_UNKNOWN_NEVER_NO_FILL_CAPACITY",
        "zero_daily_volume": "REQUIRES_FLAT_OHLC_AT_CENT",
        "provider_minute_timestamp_semantics_confirmed": False,
        "actual_order_fill_claimed": False, "promotion_model_changed": False,
    }
    # JSON equality alone accepts False == 0. Compare canonical encodings too.
    encode = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    _require(encode(policy) == encode(expected), "DORMANT_POLICY_DEFINITION_CHANGED")
    return {"path": "work/profit_1000_upgrade/FORMAL_ENTRY_V4_POLICY.json",
            "sha256": hashlib.sha256(raw).hexdigest(), "policy_id": POLICY_ID}


def activation_check(*, signal_date, frozen_at_utc, existing_signal_dates=(),
                     proposed_effective_signal_date=None, proposed_activation_at_utc=None):
    """Report blockers only. This candidate has no successful activation path.

    The proposed cutover is a design input, not authority. Even a valid future
    date remains blocked until separately reviewed production integration.
    Never dispatch old records using the newest policy file's presence.
    """
    policy_binding()
    day, frozen = _date(signal_date), _stamp(frozen_at_utc)
    existing = tuple(_date(value) for value in existing_signal_dates)
    blockers = ["PREPARATION_ONLY_NO_PRODUCTION_DISPATCH",
                "SOURCE_FREEZE_SUCCESSOR_REVIEW_NOT_INSTALLED",
                "FORMAL_MINUTE_TIME_SEMANTICS_NOT_CONFIRMED",
                "RESEARCH_V3_FULL_REPLAY_AND_FORWARD_ACCEPTANCE_REQUIRED"]
    if day in existing:
        blockers.append("EXISTING_FROZEN_D_MUST_KEEP_ORIGINAL_POLICY")
    if existing and day <= max(existing):
        blockers.append("NOT_A_NEW_FORWARD_D")
    if day <= "20260911":
        blockers.append("LEGACY_D_20260910_20260911_AND_EARLIER_FORBIDDEN")
    if proposed_effective_signal_date is None or proposed_activation_at_utc is None:
        blockers.append("NO_APPROVED_CUTOVER_BOUNDARY")
    else:
        effective = _date(proposed_effective_signal_date)
        activated = _stamp(proposed_activation_at_utc)
        # Approval must precede the new D session, not be backdated after seeing
        # that day's ranking, auction, or outcome.
        if effective <= activated.strftime("%Y%m%d") or day < effective or frozen <= activated:
            blockers.append("RETROSPECTIVE_OR_PRE_ACTIVATION_D_FORBIDDEN")
    if frozen.strftime("%Y%m%d") < day:
        blockers.append("INVALID_D_FREEZE_TIMESTAMP")
    return {"can_activate": False, "policy_id": POLICY_ID,
            "effective_signal_date": None, "blockers": blockers,
            "existing_frozen_records_modified": False}


def _bound_table(root, binding, day, kind):
    _require(isinstance(binding, Mapping) and set(binding) == {"path", "sha256"}, "MISSING_SOURCE_BINDING")
    expected = f"data/market/raw/{day[:4]}/{day}/{kind}.csv"
    _require(binding["path"] == expected and isinstance(binding["sha256"], str)
             and _SHA.fullmatch(binding["sha256"]) is not None, "INVALID_SOURCE_BINDING")
    path = root / expected
    _require(not any(part.is_symlink() for part in (path, *path.parents)) and
             root in path.resolve().parents and path.is_file(), "UNSAFE_OR_MISSING_SOURCE")
    raw = path.read_bytes()
    _require(len(raw) <= 8_000_000 and hashlib.sha256(raw).hexdigest() == binding["sha256"], "SOURCE_SHA_MISMATCH")
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        fields = reader.fieldnames
        required = ({"ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol"}
                    if kind == "daily" else {"ts_code", "trade_date", "up_limit", "down_limit"})
        _require(fields is not None and len(fields) == len(set(fields)) and required <= set(fields), "INVALID_SOURCE_FIELDS")
        rows = {}
        for row in reader:
            code = row.get("ts_code")
            _require(None not in row and all(value is not None for value in row.values())
                     and isinstance(code, str) and re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code) is not None
                     and row.get("trade_date") == day and code not in rows, "SOURCE_ROW_IDENTITY_OR_SHAPE_INVALID")
            rows[code] = row
        return rows
    except (UnicodeError, csv.Error):
        raise PreparationError("INVALID_SOURCE_CSV") from None


def _market_values(daily, limits):
    _require(daily is not None and limits is not None, "MISSING_DAILY_OR_LIMIT_ROW")
    # Match labels_v3's float parsing and complete market-range predicate.
    # The 1e-8 allowance applies ONLY to high/up and low/down comparisons;
    # OHLC ordering remains exact, and no reported auction price is changed.
    def finite(value, *, positive=False):
        _require(not isinstance(value, bool), "INVALID_NUMBER")
        number = float(value)
        _require(math.isfinite(number) and (not positive or number > 0), "INVALID_NUMBER")
        return number
    values = {key: finite(daily.get(key), positive=True) for key in ("open", "high", "low", "close", "pre_close")}
    volume = finite(daily.get("vol"))
    up = finite(limits.get("up_limit"), positive=True)
    down = finite(limits.get("down_limit"), positive=True)
    _require(not (volume < 0 or not values["low"] <= min(values["open"], values["close"])
                  <= max(values["open"], values["close"]) <= values["high"] or down >= up
                  or values["high"] > up + 1e-8 or values["low"] < down - 1e-8),
             "INVALID_OHLC_OR_LIMIT_RANGE")
    _require(volume != 0 or all(_cent(str(values[key])) == _cent(str(values["close"]))
             for key in ("open", "high", "low")), "ZERO_DAILY_VOLUME_NONFLAT_OHLC_CONFLICT")
    return values, volume, up


def preview_top2(root, *, signal_date, exec_date, exit_date, open_dates,
                 ranked_rows, as_of_utc, daily_binding=None, limits_binding=None):
    """Read-only economic preview; retain both slots including loss/pending.

    ranked_rows are already ordered P1 projection rows. Their natural freeze
    provenance is deliberately NOT certified here. The future formal bridge
    must validate the complete P0/P1 frozen bundle and bind this policy there.
    Source bindings are rehashed; only the isolated canonical research source
    path is read. No old formal selection, verification, or outcome is loaded.
    """
    policy = policy_binding()
    day, t, t1 = (_date(value) for value in (signal_date, exec_date, exit_date))
    _require(isinstance(open_dates, Sequence) and not isinstance(open_dates, (str, bytes)), "INVALID_CALENDAR")
    dates = [_date(value) for value in open_dates]
    _require(dates == sorted(set(dates)) and day in dates, "INVALID_CALENDAR")
    index = dates.index(day)
    _require(dates[index:index + 3] == [day, t, t1], "NON_ADJACENT_D_T_T1")
    _require(isinstance(ranked_rows, list) and len(ranked_rows) <= 10, "INVALID_RANKED_ROWS")
    seen = set()
    for rank, row in enumerate(ranked_rows, 1):
        _require(isinstance(row, Mapping) and type(row.get("executable_profit_research_rank")) is int
                 and row["executable_profit_research_rank"] == rank
                 and isinstance(row.get("ts_code"), str) and _CODE.fullmatch(row["ts_code"]) is not None
                 and row["ts_code"] not in seen and row.get("stage_transition") in ("2->3", "3->4"),
                 "INVALID_FROZEN_RANK_IDENTITY")
        seen.add(row["ts_code"])
    stamp = _stamp(as_of_utc)
    rows = []
    for slot in (1, 2):
        candidate = ranked_rows[slot - 1] if slot <= len(ranked_rows) else None
        rows.append({"shadow_slot": slot, "ts_code": candidate["ts_code"] if candidate else None,
                     "stage_transition": candidate["stage_transition"] if candidate else None,
                     "status": "PENDING_T_NOT_REACHED" if candidate else "NO_CANDIDATE_SLOT",
                     "proxy_fill": None, "entry_price": None, "entry_price_source": None,
                     "capacity_proxy_verified": False, "actual_capacity_verified": False,
                     "entry_price_evidence": None, "entry_qualification_status": None,
                     "price_qualified": False, "capacity_amount": None,
                     "capacity_reason": "ENTRY_NOT_OBSERVED", "capacity_evidence": "UNKNOWN",
                     "reported_auction_amount": None, "amount_identity_delta": None,
                     "daily_open_cent_match": None, "slot_net_return": None, "net_return": None,
                     "terminal_settlement": False, "production_ledger_eligible": False})
    output = {"schema_version": PREVIEW_SCHEMA, "status": "PREPARATION_ONLY_NOT_FORMAL_VERIFICATION",
              "signal_date": day, "exec_date": t, "scheduled_exit_date": t1,
              "entry_policy": policy, "exit_policy_id": EXIT_POLICY_ID,
              "round_trip_cost_rate": float(COST_RATE), "shadow_notional_cny": int(NOTIONAL),
              "rows": rows, "source_files": [], "production_integrated": False,
              "production_activation_allowed": False, "natural_freeze_verified": False,
              "actual_execution_claimed": False, "old_records_loaded_or_modified": False,
              "provider_minute_timestamp_semantics_confirmed": False,
              "research_only": True, "basis": policy_v3.BASIS, "known_before_0925": False,
              "price_reporting_precision_confirmed": False, "reported_price_preserved": True,
              "source_authority_issued": False}
    if stamp < datetime.strptime(t + " 15:00:00", "%Y%m%d %H:%M:%S").replace(tzinfo=SHANGHAI):
        return output
    active = [row for row in rows if row["ts_code"] is not None]
    if not active:
        return output
    try:
        root = Path(root)
        _require(not any(path.is_symlink() for path in (root, *root.parents)), "UNSAFE_SOURCE_ROOT")
        root = root.resolve(strict=True)
        daily_rows = _bound_table(root, daily_binding, t, "daily")
        limit_rows = _bound_table(root, limits_binding, t, "stk_limit")
        output["source_files"] = [dict(daily_binding), dict(limits_binding)]
        # Formal future entry never uses the research pre-2025 absence shortcut.
        # A physically absent or corrupt receipt is PENDING, even if _o exists.
        source = auction_truth.load(root, t)
        output["source_files"].extend(dict(item) for item in source.source_files)
    except auction_truth.AuctionSourceMissing:
        for row in active:
            row["status"] = "PENDING_CANONICAL_ATTEMPT_REQUIRED"
        return output
    except (ValueError, OSError, TypeError, KeyError):
        for row in active:
            row["status"] = "PENDING_INVALID_ENTRY_SOURCE"
        return output
    for row in active:
        try:
            values, volume, up = _market_values(daily_rows.get(row["ts_code"]), limit_rows.get(row["ts_code"]))
            price = auction_truth.entry_price(source, t, row["ts_code"], float(values["open"]),
                                              daily_source_binding=daily_binding)
            row.update(entry_price=price["price"], entry_price_source=price["entry_price_source"],
                       capacity_proxy_verified=price["capacity_proxy_verified"], entry_price_evidence=price,
                       entry_qualification_status=price["status"], price_qualified=price["price_qualified"],
                       capacity_amount=price["capacity_amount"], capacity_reason=price["capacity_reason"],
                       capacity_evidence=price["capacity_evidence"],
                       reported_auction_amount=price["reported_auction_amount"],
                       amount_identity_delta=price["amount_identity_delta"],
                       daily_open_cent_match=price["daily_open_cent_match"])
            # Native v3 returns candidate-local pending qualifications, not an
            # exception. Neither a daily-open fallback nor a no-fill is allowed.
            if str(price["status"]).startswith("PENDING_CANONICAL_"):
                row["status"] = "PENDING_ENTRY_" + price["status"].removeprefix("PENDING_CANONICAL_")
                continue
            _require(price["status"] in {"CANONICAL_PRICE_OBSERVED",
                     "DAILY_OPEN_PRICE_PROXY_CAPACITY_UNKNOWN", "OBSERVED_NO_AUCTION_TRADE"},
                     "UNKNOWN_ENTRY_QUALIFICATION")
            if volume == 0 and price["auction_trade_observed"] is True:
                row["status"] = "PENDING_ENTRY_SOURCE_CONFLICT"
                continue
            if price["status"] == "OBSERVED_NO_AUCTION_TRADE":
                status = "NO_FILL_CANONICAL_AUCTION_ZERO_VOLUME"
            elif volume == 0:
                status = "NO_FILL_SUSPENDED"
            elif _cent(price["price"]) == _cent(str(up)):
                status = "NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED"
            # Match native labels_v3 arithmetic, including its float-comparison
            # epsilon. Raw reported amount is never eligible capacity evidence.
            elif (price["capacity_proxy_verified"] is True and price["capacity_amount"] is not None
                  and price["capacity_amount"] * float(PARTICIPATION) + 1e-9 < float(NOTIONAL)):
                status = "NO_FILL_CAPACITY"
            else:
                row.update(status="ENTRY_PROXY_OBSERVED_EXIT_PENDING", proxy_fill=1)
                continue
            row.update(status=status, proxy_fill=0, slot_net_return=0.0)
        except auction_truth.AuctionSourceError as exc:
            row["status"] = ("PENDING_ENTRY_SOURCE_CONFLICT" if str(exc) == "CANONICAL_PRICE_DAILY_OPEN_CONFLICT"
                             else "PENDING_INVALID_ENTRY_SOURCE")
        except (ValueError, OSError, TypeError, KeyError):
            row["status"] = "PENDING_INVALID_ENTRY_SOURCE"
    # Recheck all bound bytes after resolving rows. Even no-fill previews cannot
    # become zero outcomes if source bytes changed during the read-only pass.
    try:
        for binding in output["source_files"]:
            path = root / binding["path"]
            _require(not any(part.is_symlink() for part in (path, *path.parents))
                     and root in path.resolve().parents
                     and hashlib.sha256(path.read_bytes()).hexdigest() == binding["sha256"], "SOURCE_CHANGED_DURING_PREVIEW")
    except (ValueError, OSError, TypeError):
        for row in active:
            row.update(status="PENDING_SOURCE_CHANGED_DURING_PREVIEW", proxy_fill=None,
                       slot_net_return=None, net_return=None)
    return output
