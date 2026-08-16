from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any


OBSERVATION_START_EXEC_DATE = "20260721"
OBSERVATION_TOP_N = 10
FOCUS_TRANSITIONS = {"2→3", "3→4"}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any, default: int = 0) -> int:
    number = _number(value)
    return int(number) if number is not None else default


def _text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def canonical_transition(value: Any) -> str:
    text = _text(value).replace("->", "→").replace("－", "→").replace("-", "→")
    return text if text in FOCUS_TRANSITIONS else ""


def observation_price_contract(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return a frozen, non-trading observation cap without weakening BUY gates."""
    d_close = _number(row.get("d_close"))
    up_limit = _number(row.get("estimated_up_limit"))
    formal_price = _number(row.get("recommended_max_price"))
    stored_price = _number(row.get("observation_max_price"))
    diagnostic_gap = _number(row.get("diagnostic_gap"))

    price: float | None = None
    basis = "unavailable"
    if formal_price is not None and formal_price > 0:
        price = formal_price
        basis = "formal_safe_cap"
    elif stored_price is not None and stored_price > 0:
        price = stored_price
        basis = _text(row.get("observation_price_basis")) or "frozen_observation_cap"
    elif d_close is not None and d_close > 0 and diagnostic_gap is not None:
        price = d_close * (1.0 + diagnostic_gap)
        basis = "model_diagnostic_cap"
    elif d_close is not None and d_close > 0:
        # Predictions frozen before observation-v1 did not persist diagnostic_gap.
        # D close is a conservative, reproducible cap and does not use future prices.
        price = d_close
        basis = "legacy_d_close_cap"

    if price is not None and up_limit is not None and up_limit > 0:
        price = min(price, up_limit - 0.01)
    price = round(max(0.01, price) + 1e-9, 2) if price is not None and price > 0 else None
    gap_pct = (
        round((price / d_close - 1.0) * 100.0, 4)
        if price is not None and d_close is not None and d_close > 0
        else None
    )
    return {
        "observation_max_price": price,
        "observation_auction_change_pct": gap_pct,
        "observation_price_basis": basis,
        "observation_price_is_formal": basis == "formal_safe_cap",
    }


def rank_observation_rows(
    rows: Iterable[Mapping[str, Any]],
    limit: int = OBSERVATION_TOP_N,
) -> tuple[list[dict[str, Any]], int]:
    focused: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        transition = canonical_transition(
            row.get("stage_transition") or row.get("stage")
        )
        mechanism_limit = _number(
            row.get("mechanism_limit_pct", row.get("decision_limit_pct"))
        )
        if not transition or (mechanism_limit is not None and mechanism_limit > 10.0):
            continue
        row["stage_transition"] = transition
        row.update(observation_price_contract(row))
        focused.append(row)

    def risk_tier(row: Mapping[str, Any]) -> int:
        if _integer(row.get("risk_gate_pass")) == 1:
            return 0
        big_loss = _number(row.get("predicted_big_loss_probability"))
        lower_bound = _number(row.get("predicted_return_lcb"))
        exit_probability = _number(row.get("predicted_exit_probability"))
        formal_cap = _number(row.get("max_big_loss_probability"))
        relaxed_cap = max(0.30, 2.0 * (formal_cap if formal_cap is not None else 0.15))
        if (
            big_loss is not None
            and big_loss <= relaxed_cap
            and lower_bound is not None
            and lower_bound >= -0.03
            and (exit_probability is None or exit_probability >= 0.75)
        ):
            return 1
        return 2

    def sort_key(row: Mapping[str, Any]) -> tuple[int, float, float, float, float, int, str]:
        continuation = _number(row.get("predicted_continuation_limit_up_probability"))
        big_loss = _number(row.get("predicted_big_loss_probability"))
        conservative = _number(row.get("conservative_ev"))
        lower_bound = _number(row.get("predicted_return_lcb"))
        source_rank = _integer(row.get("rank", row.get("source_rank")), 999999)
        return (
            risk_tier(row),
            big_loss if big_loss is not None else 2.0,
            -(conservative if conservative is not None else -1.0),
            -(continuation if continuation is not None else -1.0),
            -(lower_bound if lower_bound is not None else -1.0),
            source_rank,
            _text(row.get("ts_code")),
        )

    focused.sort(key=sort_key)
    total = len(focused)
    selected = focused[: max(0, int(limit))]
    for rank, row in enumerate(selected, start=1):
        tier = risk_tier(row)
        row["observation_rank"] = rank
        row["stage_watch_rank"] = rank
        row["observation_pool_size"] = total
        row["observation_selected"] = 1
        row["observation_risk_tier"] = tier
        row["observation_risk_label"] = {
            0: "正式安全门槛",
            1: "观察风险可控",
            2: "高风险观察",
        }[tier]
        row["watch_label"] = "正式买入" if row.get("action") == "BUY" else "仅观察"
    return selected, total


__all__ = [
    "FOCUS_TRANSITIONS",
    "OBSERVATION_START_EXEC_DATE",
    "OBSERVATION_TOP_N",
    "canonical_transition",
    "observation_price_contract",
    "rank_observation_rows",
]
