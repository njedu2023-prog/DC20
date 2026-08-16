from __future__ import annotations


ERET_TARGET_COLUMN = "realized_ret_open_to_tplus1_open_0930"
ERET_COMPAT_TARGET_COLUMN = "realized_ret_t1_to_t2"
ERET_TRUTH_VERSION = "eret_truth_v5_tplus1_open0930"
ERET_HOLDING_MODE = "manual_buy_t_0925_auction_to_tplus1_open0930_exit"
DECISION_EXECUTION_CONTRACT = "D_signal_manual_T_0925_auction_guidance_Tplus1_open0930_exit"
PFILL_TRUTH_VERSION = "pfill_truth_v7_public_buyable_explicit"
PFILL_EXECUTION_CONTRACT = "manual_guidance_0925_public_market_fillability_proxy"
PUBLIC_MARKET_BUYABLE_TARGET_COLUMN = "y_public_market_buyable"
ACTUAL_ORDER_FILL_TARGET_COLUMN = "actual_order_fill"
ACTUAL_ORDER_FILL_OBSERVED_COLUMN = "actual_order_fill_observed"
PREOPEN_AUCTION_GATE_CONTRACT_VERSION = (
    "preopen_gate_audit_v1_disabled_no_snapshot_history"
)
PREOPEN_AUCTION_GATE_AUDIT = {
    "contract_version": PREOPEN_AUCTION_GATE_CONTRACT_VERSION,
    "enabled": False,
    "status": "disabled_missing_historical_microstructure_snapshots",
    "decision_deadline": "T 09:24:50 Asia/Shanghai",
    "known_at_rule": "only snapshots timestamped at or before T 09:24:50 are eligible",
    "available_truth_sources": {
        "tushare_stk_auction": (
            "final opening-auction clearing price and volume; truth only, not a "
            "pre-decision feature"
        ),
        "tushare_stk_auction_o": (
            "post-auction OHLCV aggregate; truth only, not a pre-decision feature"
        ),
        "minute_1m": "starts at T 09:30 and is not a pre-decision feature",
    },
    "required_missing_fields": [
        "snapshot_timestamp",
        "indicative_match_price",
        "indicative_match_volume",
        "unmatched_buy_volume",
        "unmatched_sell_volume",
        "order_imbalance",
        "cancel_pressure_0920_092450",
    ],
    "fallback": "D_close_only_ranking_and_manual_frozen_limit",
}
EXIT_POLICY_VERSION = "tplus1_open_0930_v1"
EXIT_TAKE_PROFIT_PCT = None
EXIT_STOP_LOSS_PCT = None
EXIT_LATEST_TIME = "09:30"
HISTORY_CONTRACT_VERSION = "decision_v11_tplus1_open0930_strict_calendar_no_future"


__all__ = [
    "DECISION_EXECUTION_CONTRACT",
    "ERET_COMPAT_TARGET_COLUMN",
    "ERET_HOLDING_MODE",
    "ERET_TARGET_COLUMN",
    "ERET_TRUTH_VERSION",
    "EXIT_LATEST_TIME",
    "EXIT_POLICY_VERSION",
    "EXIT_STOP_LOSS_PCT",
    "EXIT_TAKE_PROFIT_PCT",
    "HISTORY_CONTRACT_VERSION",
    "PFILL_EXECUTION_CONTRACT",
    "PFILL_TRUTH_VERSION",
    "PREOPEN_AUCTION_GATE_AUDIT",
    "PREOPEN_AUCTION_GATE_CONTRACT_VERSION",
    "PUBLIC_MARKET_BUYABLE_TARGET_COLUMN",
    "ACTUAL_ORDER_FILL_TARGET_COLUMN",
    "ACTUAL_ORDER_FILL_OBSERVED_COLUMN",
]
