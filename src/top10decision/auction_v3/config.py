from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from top10decision.decision.contracts import (
    EXIT_LATEST_TIME,
    EXIT_POLICY_VERSION,
    EXIT_STOP_LOSS_PCT,
    EXIT_TAKE_PROFIT_PCT,
)
from top10decision.decision.observation import (
    OBSERVATION_START_EXEC_DATE,
    OBSERVATION_TOP_N,
)


TARGET_INDEPENDENT_OOS_DATES = 500
# The first walk-forward block can be unavailable while the market-fill
# training sample is still below its minimum row count. Keep enough history
# for 500 independently scored dates even when that block is skipped.
WALKFORWARD_WARMUP_DATES = 200
TARGET_HISTORY_DATES = TARGET_INDEPENDENT_OOS_DATES + WALKFORWARD_WARMUP_DATES


@dataclass(frozen=True)
class AuctionV3Config:
    """Runtime and governance settings for manual auction guidance."""

    root: Path = Path(".")
    max_candidates: int = 0
    max_positions: int = 3
    max_observation_candidates: int = OBSERVATION_TOP_N
    observation_validation_start_date: str = OBSERVATION_START_EXEC_DATE
    forward_shadow_start_signal_date: str = "20260728"
    top1_promotion_start_signal_date: str = "20260807"
    round_trip_cost_bps: float = 35.0
    slippage_bps_each_side: float = 5.0
    order_amount_cny: float = 100_000.0
    max_auction_participation: float = 0.01
    min_edge: float = 0.001
    min_fill_probability: float = 0.55
    min_exit_probability: float = 0.90
    min_profit_probability: float = 0.52
    continuation_score_weight: float = 0.005
    fill_score_weight: float = 0.001
    sentiment_min_brier_improvement: float = 0.0005
    sentiment_min_relative_brier_improvement: float = 0.005
    sentiment_min_daily_win_rate: float = 0.55
    max_big_loss_probability: float = 0.15
    big_loss_threshold: float = -0.03
    min_return_lcb: float = 0.0
    expected_return_confidence_z: float = 1.645
    min_expected_return_margin: float = 0.002
    tail_risk_aversion: float = 1.00
    blocked_exit_loss: float = 0.05
    min_tail_mean_return: float = -0.05
    take_profit_pct: float | None = EXIT_TAKE_PROFIT_PCT
    stop_loss_pct: float | None = EXIT_STOP_LOSS_PCT
    latest_exit_time: str = EXIT_LATEST_TIME
    exit_policy_version: str = EXIT_POLICY_VERSION
    require_intraday_exit_truth: bool = False
    max_mechanism_limit_pct: float = 10.0
    min_train_dates: int = 60
    min_train_rows: int = 1_000
    calibration_fraction: float = 0.20
    calibration_min_dates: int = 16
    calibration_embargo_dates: int = 1
    calibration_fit_fraction: float = 0.60
    probability_min_brier_skill: float = 0.002
    probability_min_daily_win_rate: float = 0.52
    probability_max_ece: float = 0.08
    probability_min_eval_dates: int = 5
    return_min_relative_rmse_improvement: float = 0.01
    return_min_daily_win_rate: float = 0.52
    conformal_min_cohort_rows: int = 30
    policy_tuning_fraction: float = 0.10
    policy_tuning_min_dates: int = 12
    policy_min_signal_dates: int = 8
    policy_min_filled_trades: int = 20
    policy_min_signal_date_ratio: float = 0.05
    policy_max_no_signal_streak: int = 30
    policy_min_exit_probability: float = 0.90
    policy_fill_probability_grid: tuple[float, ...] = (0.05, 0.10, 0.20)
    policy_big_loss_probability_grid: tuple[float, ...] = (0.35, 0.45, 0.55)
    policy_mean_return_lcb_grid: tuple[float, ...] = (-0.05, -0.03, -0.01)
    policy_conservative_ev_grid: tuple[float, ...] = (-0.02, -0.005, 0.0)
    policy_score_quantiles: tuple[float, ...] = (0.50, 0.70, 0.85)
    policy_position_grid: tuple[int, ...] = (1, 2, 3)
    policy_max_realized_big_loss_rate: float = 0.25
    policy_min_tail_mean_return: float = -0.05
    promotion_min_dates: int = TARGET_HISTORY_DATES
    promotion_min_oos_dates: int = TARGET_INDEPENDENT_OOS_DATES
    promotion_min_filled_trades: int = 200
    promotion_min_stage_focus_filled_trades: int = 60
    promotion_min_market_regimes: int = 3
    min_oos_signal_date_ratio: float = 0.05
    max_oos_no_signal_streak: int = 30
    embargo_dates: int = 2
    backtest_block_dates: int = 10
    backtest_max_refits: int = 6
    fill_max_training_rows: int = 20_000
    gap_grid_min: float = -0.05
    gap_grid_max: float = 0.08
    gap_grid_step: float = 0.005
    lower_confidence_quantile: float = 0.10
    prediction_interval_upper_quantile: float = 0.90
    model_version: str = "auction_v13_five_year_promotion_blend_oos_1"

    @property
    def output_root(self) -> Path:
        return self.root / "outputs" / "auction_v3"

    @property
    def prediction_root(self) -> Path:
        return self.output_root / "predictions"

    @property
    def truth_root(self) -> Path:
        return self.output_root / "truth"

    @property
    def historical_training_root(self) -> Path:
        return (
            self.root
            / "data"
            / "auction_v3"
            / "history"
            / self.exit_policy_version
        )

    @property
    def verification_root(self) -> Path:
        return self.output_root / "verification"

    @property
    def metrics_root(self) -> Path:
        return self.output_root / "metrics"

    @property
    def model_root(self) -> Path:
        return self.output_root / "models"

    @property
    def report_root(self) -> Path:
        return self.root / "docs" / "reports"

    @property
    def manual_feedback_path(self) -> Path:
        return self.root / "data" / "auction_v3" / "manual_trade_feedback.csv"

    @property
    def cost_rate(self) -> float:
        return (self.round_trip_cost_bps + 2.0 * self.slippage_bps_each_side) / 10_000.0

    def ensure_directories(self) -> None:
        for path in (
            self.prediction_root,
            self.truth_root,
            self.historical_training_root,
            self.verification_root,
            self.metrics_root,
            self.model_root,
            self.report_root,
            self.manual_feedback_path.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)
