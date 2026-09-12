"""Registered research source policy; not permission to activate a live model."""
from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

ENTRY_POLICY_ID = "research_canonical_auction_or_open_no_cap_v2"
AUCTION_SOURCE_POLICY_ID = "dc20_research_canonical_stk_auction_20260912_v2"
MINUTE_SOURCE_POLICY_ID = "dc20_research_stk_mins_query_0931_v1"
MINUTE_TIME_SEMANTICS = "RESEARCH_BAR_END_ASSUMPTION_NOT_PROVIDER_CONFIRMED"
CONTRACT = MappingProxyType({
    "entry_policy_id": ENTRY_POLICY_ID,
    "auction_source_policy_id": AUCTION_SOURCE_POLICY_ID,
    "minute_source_policy_id": MINUTE_SOURCE_POLICY_ID,
    "minute_time_semantics": MINUTE_TIME_SEMANTICS,
})
NO_FILL_STATUSES = frozenset({
    "NO_FILL_CANONICAL_AUCTION_ZERO_VOLUME", "NO_FILL_SUSPENDED",
    "NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED", "NO_FILL_CAPACITY",
})


def source_policy_contract() -> dict[str, str]:
    """A fresh JSON-serializable copy; callers cannot mutate the registry."""
    return dict(CONTRACT)


def validate_contract(value: Mapping, *, exact: bool = False) -> dict[str, str]:
    """Require all four fields, including the unresolved timing qualification.

    Row/manifest payloads may have other fields; use exact=True for a standalone
    source_policy_contract. A v2 entry ID alone is never enough provenance.
    """
    if not isinstance(value, Mapping):
        raise ValueError("research v2 source policy must be a mapping")
    if exact and set(value) != set(CONTRACT):
        raise ValueError("research v2 source policy field set changed")
    for key, expected in CONTRACT.items():
        if type(value.get(key)) is not str or value[key] != expected:
            raise ValueError("research v2 source policy mismatch: " + key)
    nested = value.get("source_policy_contract")
    if "source_policy_contract" in value:
        if not isinstance(nested, Mapping) or set(nested) != set(CONTRACT):
            raise ValueError("research v2 nested source policy changed")
        for key, expected in CONTRACT.items():
            if type(nested.get(key)) is not str or nested[key] != expected:
                raise ValueError("research v2 nested source policy mismatch: " + key)
    return source_policy_contract()


def has_v2_policy(value: Mapping) -> bool:
    """Recognize intent only; callers must subsequently validate_contract."""
    return isinstance(value, Mapping) and value.get("entry_policy_id") == ENTRY_POLICY_ID


def validate_label_contract(value: Mapping) -> dict[str, str]:
    """Reject stale-source or invented-observation labels before any fit/NAV."""
    result = validate_contract(value)
    observed = value.get("minute_source_observed")
    if type(observed) is not bool:
        raise ValueError("research v2 minute observation flag required")
    status = str(value.get("label_status", ""))
    if status == "SETTLED_1000_LIMIT_HOLD_MINUTE_PROXY" and not observed:
        raise ValueError("research v2 settled label lacks observed minute source")
    if status.startswith("NO_FILL_") and observed:
        raise ValueError("research v2 no-fill label cannot claim observed exit minutes")
    if status.startswith("NO_FILL_") and status not in NO_FILL_STATUSES:
        raise ValueError("research v2 unknown no-fill, source uncertainty or old cap cannot settle as no-fill")
    if value.get("entry_price_source") not in (None, "TUSHARE_STK_AUCTION", "DAILY_OPEN_PROXY"):
        raise ValueError("research v2 legacy auction price source forbidden")
    return result
