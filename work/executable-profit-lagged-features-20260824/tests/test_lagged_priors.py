from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest


WORK = Path(__file__).resolve().parents[1]
if str(WORK) not in sys.path:
    sys.path.insert(0, str(WORK))

from lagged_priors import (  # noqa: E402
    LaggedPriorError,
    _deterministic_csv_gzip,
    _resolve_repo_file,
    build_lagged_features,
)


OPEN_DATES = [f"202601{day:02d}" for day in range(1, 13)]


def _history() -> pd.DataFrame:
    # Availability is always the second open session after each source D.
    return pd.DataFrame(
        [
            {
                "signal_date": OPEN_DATES[0],
                "target_exit_date": OPEN_DATES[2],
                "ts_code": "600001.SH",
                "stage": 2,
                "board": "SH_MAIN",
                "market_fill": 1,
                "profit_hit": 1,
                "big_loss_hit": 0,
                "net_return": 0.05,
            },
            {
                "signal_date": OPEN_DATES[1],
                "target_exit_date": OPEN_DATES[3],
                "ts_code": "600002.SH",
                "stage": 2,
                "board": "SH_MAIN",
                "market_fill": 1,
                "profit_hit": 0,
                "big_loss_hit": 1,
                "net_return": -0.08,
            },
            {
                "signal_date": OPEN_DATES[2],
                "target_exit_date": OPEN_DATES[4],
                "ts_code": "600003.SH",
                "stage": 3,
                "board": "SH_MAIN",
                "market_fill": 0,
                "profit_hit": None,
                "big_loss_hit": None,
                "net_return": None,
            },
        ]
    )


def _target() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "signal_date": OPEN_DATES[3],
                "ts_code": "600001.SH",
                "stage": 2,
                "board": "SH_MAIN",
                "promotion_rank": 1,
            }
        ]
    )


def _build(history: pd.DataFrame) -> pd.DataFrame:
    return build_lagged_features(
        history=history,
        targets=_target(),
        open_dates=OPEN_DATES,
        source_kind="full",
        prefix="fullhist",
    )


def test_same_day_and_future_outcomes_cannot_change_current_d_features() -> None:
    original = _history()
    changed = original.copy()
    # These outcomes become available on D itself and after D; neither is legal.
    changed.loc[1, ["market_fill", "profit_hit", "big_loss_hit", "net_return"]] = [
        0,
        None,
        None,
        None,
    ]
    changed.loc[2, ["market_fill", "profit_hit", "big_loss_hit", "net_return"]] = [
        1,
        1,
        0,
        0.50,
    ]
    pd.testing.assert_frame_equal(_build(original), _build(changed), check_exact=True)


def test_strictly_prior_matured_outcome_changes_features() -> None:
    original = _history()
    changed = original.copy()
    changed.loc[0, ["profit_hit", "big_loss_hit", "net_return"]] = [0, 1, -0.08]
    before = _build(original)
    after = _build(changed)
    assert before["fullhist_global_expanding_profit_given_fill_rate"].iloc[0] != after[
        "fullhist_global_expanding_profit_given_fill_rate"
    ].iloc[0]
    assert before["lagged_prior_max_history_exit_date"].iloc[0] == OPEN_DATES[2]
    assert before["lagged_prior_max_history_exit_date"].iloc[0] < OPEN_DATES[3]


def test_non_adjacent_sse_availability_fails_closed() -> None:
    changed = _history()
    changed.loc[0, "target_exit_date"] = OPEN_DATES[1]
    with pytest.raises(LaggedPriorError, match="calendar binding drifted"):
        _build(changed)


def test_nonfill_cannot_carry_conditional_return_truth() -> None:
    changed = _history()
    changed.loc[2, ["profit_hit", "big_loss_hit", "net_return"]] = [1, 0, 0.1]
    with pytest.raises(LaggedPriorError, match="nonfill rows carry"):
        _build(changed)


def test_deterministic_gzip_bytes_and_hash() -> None:
    frame = _build(_history())
    first = _deterministic_csv_gzip(frame)
    second = _deterministic_csv_gzip(frame)
    assert first == second
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


def test_repo_path_escape_and_forbidden_dependency_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.csv"
    outside.write_text("x\n", encoding="utf-8")
    with pytest.raises(LaggedPriorError, match="escaped"):
        _resolve_repo_file(root, "../outside.csv")
    with pytest.raises(LaggedPriorError, match="recovery"):
        _resolve_repo_file(root, "data/recovery/file.csv")
    with pytest.raises(LaggedPriorError, match="top10-decision"):
        _resolve_repo_file(root, "../top10-decision/file.csv")


def test_materialized_manifest_declares_research_only() -> None:
    manifest = json.loads((WORK / "outputs/lagged_priors_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "RESEARCH_ONLY_NOT_A_MODEL_NOT_RELEASED"
    assert manifest["runtime_dependency_on_recovery"] is False
    assert manifest["runtime_dependency_on_top10_decision"] is False
    assert manifest["official_trade_action_allowed"] is False
    assert manifest["model_trained"] is False
