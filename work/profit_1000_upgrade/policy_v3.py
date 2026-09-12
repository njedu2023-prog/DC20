"""Strict isolated v3 label contract; never grants model activation permission."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import math
from types import MappingProxyType

ENTRY_POLICY_ID = "research_canonical_price_capacity_split_no_cap_v3"
AUCTION_SOURCE_POLICY_ID = "dc20_research_canonical_price_capacity_split_20260912_v3"
MINUTE_SOURCE_POLICY_ID = "dc20_research_stk_mins_query_0931_v1"
MINUTE_TIME_SEMANTICS = "RESEARCH_BAR_END_ASSUMPTION_NOT_PROVIDER_CONFIRMED"
BASIS = "POSTHOC_ENTRY_PRICE_CHECK_NOT_PRE0925_FEATURE"
CONTRACT = MappingProxyType({"entry_policy_id": ENTRY_POLICY_ID,
    "auction_source_policy_id": AUCTION_SOURCE_POLICY_ID,
    "minute_source_policy_id": MINUTE_SOURCE_POLICY_ID, "minute_time_semantics": MINUTE_TIME_SEMANTICS})
NO_FILL_STATUSES = frozenset({"NO_FILL_CANONICAL_AUCTION_ZERO_VOLUME", "NO_FILL_SUSPENDED",
                            "NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED", "NO_FILL_CAPACITY"})
SETTLED = "SETTLED_1000_LIMIT_HOLD_MINUTE_PROXY"


def source_policy_contract():
    return dict(CONTRACT)


def validate_contract(value: Mapping, *, exact=False):
    if not isinstance(value, Mapping) or (exact and set(value) != set(CONTRACT)):
        raise ValueError("research v3 source policy field set changed")
    if value.get("plan_version", "v3") != "v3":
        raise ValueError("research v3 plan version required")
    for key, expected in CONTRACT.items():
        if type(value.get(key)) is not str or value[key] != expected:
            raise ValueError("research v3 source policy mismatch: " + key)
    if "source_policy_contract" in value:
        nested = value["source_policy_contract"]
        if not isinstance(nested, Mapping) or dict(nested) != dict(CONTRACT):
            raise ValueError("research v3 nested source policy changed")
    if any(isinstance(key, str) and key.endswith("source_policy_id") and key not in CONTRACT for key in value):
        raise ValueError("research v3 unknown source policy field")
    return source_policy_contract()


def has_v3_policy(value):
    return isinstance(value, Mapping) and value.get("entry_policy_id") == ENTRY_POLICY_ID


def _number(value, *, positive=False):
    if isinstance(value, bool) or value is None:
        raise ValueError("research v3 invalid numeric label")
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("research v3 invalid numeric label") from None
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError("research v3 invalid numeric label")
    return result


def validate_label_contract(value: Mapping):
    contract = validate_contract(value)
    flags = {"research_only": True, "actual_execution_claimed": False, "actual_capacity_verified": False,
             "production_activation_allowed": False, "known_before_0925": False,
             "price_reporting_precision_confirmed": False, "reported_price_preserved": True}
    if any(value.get(key) is not expected for key, expected in flags.items()) or value.get("basis") != BASIS:
        raise ValueError("research v3 posthoc/nonexecution qualification missing")
    for key in ("minute_source_observed", "auction_request_receipt_observed", "capacity_proxy_verified", "price_qualified"):
        if type(value.get(key)) is not bool:
            raise ValueError("research v3 required evidence flag missing: " + key)
    if value.get("auction_trade_observed") is not None and type(value["auction_trade_observed"]) is not bool:
        raise ValueError("research v3 invalid auction observation")
    if value.get("entry_price_source") not in (None, "TUSHARE_STK_AUCTION", "DAILY_OPEN_PROXY"):
        raise ValueError("research v3 legacy entry source forbidden")
    if value.get("shadow_max_price") is not None or value.get("round_trip_cost_rate") != .0045:
        raise ValueError("research v3 frozen cap or changed cost forbidden")
    if not isinstance(value.get("capacity_reason"), str) or not value["capacity_reason"]:
        raise ValueError("research v3 capacity qualification required")
    cap = value["capacity_proxy_verified"]
    if cap:
        _number(value.get("capacity_amount"), positive=True)
        if (value.get("capacity_evidence") != "CANONICAL_AMOUNT_ARITHMETIC_ONLY_NOT_ORDER_CAPACITY"
                or value["capacity_reason"] != "PRICE_VOLUME_AMOUNT_ARITHMETIC_CONSISTENT"
                or not value["price_qualified"]):
            raise ValueError("research v3 capacity arithmetic unqualified")
    elif value.get("capacity_amount") is not None or value.get("capacity_evidence") != "UNKNOWN":
        raise ValueError("research v3 unknown capacity cannot supply arithmetic amount")
    evidence = value.get("entry_price_evidence")
    if evidence is not None:
        if not isinstance(evidence, Mapping) or evidence.get("source_policy_id") != AUCTION_SOURCE_POLICY_ID:
            raise ValueError("research v3 entry evidence source mismatch")
        for key in ("capacity_proxy_verified", "capacity_amount", "price_qualified", "capacity_reason", "capacity_evidence"):
            if evidence.get(key) != value.get(key):
                raise ValueError("research v3 entry/label qualification mismatch: " + key)
        if evidence.get("status") != value.get("entry_qualification_status") or evidence.get("price") != value.get("entry_price"):
            raise ValueError("research v3 entry status/price mismatch")
        if evidence.get("entry_price_source") != value.get("entry_price_source"):
            raise ValueError("research v3 entry source mismatch")
        if evidence.get("price_qualified") is not (evidence.get("status") == "CANONICAL_PRICE_OBSERVED"):
            raise ValueError("research v3 price qualification/status mismatch")
        if evidence.get("entry_price_source") == "TUSHARE_STK_AUCTION" and evidence.get("loaded_source_qualification_claimed") is not True:
            raise ValueError("research v3 canonical entry lacks loaded-source qualification")
        if evidence.get("actual_capacity_verified") is not False or evidence.get("actual_execution_claimed") is not False or evidence.get("basis") != BASIS:
            raise ValueError("research v3 entry actual-execution claim forbidden")
        if not cap and evidence.get("amount") is not None:
            raise ValueError("research v3 invalid capacity amount leaked into label")
        if cap and _number(evidence.get("amount"), positive=True) != _number(value["capacity_amount"], positive=True):
            raise ValueError("research v3 capacity amount mismatch")
    elif cap or value["price_qualified"]:
        raise ValueError("research v3 qualification lacks entry evidence")
    status = value.get("label_status")
    if not isinstance(status, str) or not (status == SETTLED or status.startswith("PENDING_") or status in NO_FILL_STATUSES):
        raise ValueError("research v3 unknown status/uncertainty cannot become no-fill")
    minute_dates = value.get("minute_source_observed_dates")
    if not isinstance(minute_dates, list) or minute_dates != sorted(set(minute_dates)) or bool(minute_dates) != value["minute_source_observed"]:
        raise ValueError("research v3 minute observation dates inconsistent")
    returns = [value.get(key) for key in ("net_return", "conditional_net_return", "slot_net_return")]
    if status == SETTLED:
        if type(value.get("proxy_fill")) is not int or value["proxy_fill"] != 1 or not value["minute_source_observed"]:
            raise ValueError("research v3 settled label lacks fill/minute evidence")
        _number(value.get("entry_price"), positive=True)
        numeric = [_number(item) for item in returns]
        exit_evidence = value.get("exit_evidence")
        if len(set(numeric)) != 1 or not isinstance(exit_evidence, Mapping) or not math.isclose(
            _number(exit_evidence.get("gross_return")) - .0045, numeric[0], rel_tol=0, abs_tol=1e-12):
            raise ValueError("research v3 return or single fee mismatch")
        if value.get("actual_exit_date") not in minute_dates or value.get("label_available_date") != value.get("actual_exit_date"):
            raise ValueError("research v3 actual settlement maturity mismatch")
        available = datetime.strptime(value["actual_exit_date"], "%Y%m%d").strftime("%Y-%m-%dT15:00:00+08:00")
        if (value.get("label_available_at") != available or value.get("label_maturity_at") != value.get("actual_exit_time")
                or value.get("actual_exit_time") != exit_evidence.get("actual_exit_time")
                or evidence is None or value.get("entry_qualification_status") not in {"CANONICAL_PRICE_OBSERVED", "DAILY_OPEN_PRICE_PROXY_CAPACITY_UNKNOWN"}):
            raise ValueError("research v3 settled source or timestamp contract invalid")
    elif status in NO_FILL_STATUSES:
        if type(value.get("proxy_fill")) is not int or value["proxy_fill"] != 0 or returns[:2] != [None, None] or returns[2] != 0 or isinstance(returns[2], bool):
            raise ValueError("research v3 no-fill cannot have conditional return")
        if value["minute_source_observed"] or str(value.get("entry_qualification_status", "")).startswith("PENDING_"):
            raise ValueError("research v3 source uncertainty cannot become no-fill")
        if evidence is None or value.get("entry_qualification_status") not in {"CANONICAL_PRICE_OBSERVED", "DAILY_OPEN_PRICE_PROXY_CAPACITY_UNKNOWN", "OBSERVED_NO_AUCTION_TRADE"}:
            raise ValueError("research v3 no-fill lacks eligible entry evidence")
        if value.get("t_daily_volume") == 0 and value.get("t_daily_ohlc_flat_at_cent") is not True:
            raise ValueError("research v3 zero-volume no-fill lacks flat T OHLC evidence")
        if status == "NO_FILL_CAPACITY" and (not cap or _number(value["capacity_amount"]) * .01 + 1e-9 >= 100_000):
            raise ValueError("research v3 unknown capacity cannot become no-fill")
        if status == "NO_FILL_CANONICAL_AUCTION_ZERO_VOLUME" and (value.get("entry_qualification_status") != "OBSERVED_NO_AUCTION_TRADE" or value.get("auction_trade_observed") is not False or cap):
            raise ValueError("research v3 no-auction-trade evidence missing")
        if status == "NO_FILL_SUSPENDED" and value.get("t_daily_volume") != 0:
            raise ValueError("research v3 suspended no-fill lacks zero T volume")
        if status == "NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED" and value.get("t_opening_limit_up_observed") is not True:
            raise ValueError("research v3 opening-limit no-fill lacks T evidence")
        expected_available = datetime.strptime(value["exec_date"], "%Y%m%d").strftime("%Y-%m-%dT15:00:00+08:00")
        expected_maturity = datetime.strptime(value["exec_date"], "%Y%m%d").strftime("%Y-%m-%dT09:25:00+08:00")
        if value.get("label_available_date") != value["exec_date"] or value.get("label_available_at") != expected_available or value.get("label_maturity_at") != expected_maturity:
            raise ValueError("research v3 no-fill timing mismatch")
    elif any(item is not None for item in returns) or (value.get("proxy_fill") is not None and (type(value["proxy_fill"]) is not int or value["proxy_fill"] != 1)):
        raise ValueError("research v3 pending cannot be zero or terminal")
    return contract
