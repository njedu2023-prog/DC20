from __future__ import annotations

from pathlib import Path

import pandas as pd

from top10decision.auction_v3.config import AuctionV3Config
from top10decision.auction_v3.engine import (
    LEGACY_PROFIT_PRIVATE_RUNTIME_ENV,
    LEGACY_PROFIT_PRIVATE_RUNTIME_ROOT,
    AuctionV3Engine,
)
from top10decision.decision.legacy_profit_relative_research import (
    SEALED_VALIDATION_PATH,
    score_legacy_profit_relative_rows,
)
from top10decision.decision.three_engine_models import (
    _feature_snapshot_sha256,
    _normalize_inference_pool,
    attach_runtime_promotion_priors,
    load_research_only_legacy_three_engine_snapshot,
    top10_members_sha256,
)


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/run_decision_daily.yml"


def test_private_pre_overlay_runtime_pool_uses_fixed_unpublished_root(
    tmp_path: Path,
) -> None:
    engine = AuctionV3Engine(AuctionV3Config(root=tmp_path))
    frame = pd.DataFrame(
        [
            {"signal_date": "20260824", "ts_code": "002412.SZ", "stage": 3},
            {"signal_date": "20260824", "ts_code": "000710.SZ", "stage": 2},
        ]
    )
    path = engine._persist_private_legacy_profit_runtime_features(
        "20260824",
        frame,
    )
    assert path == (
        tmp_path
        / LEGACY_PROFIT_PRIVATE_RUNTIME_ROOT
        / "runtime_features_20260824.csv"
    )
    restored = pd.read_csv(path)
    assert restored["ts_code"].tolist() == ["000710.SZ", "002412.SZ"]
    assert not (tmp_path / "outputs/decision/legacy_profit_relative_research").exists()
    assert not (tmp_path / "data/decision").exists()


def test_private_runtime_pool_accepts_zero_candidates_with_header(
    tmp_path: Path,
) -> None:
    engine = AuctionV3Engine(AuctionV3Config(root=tmp_path))
    # This is the shape returned by ``_current_base`` on a true zero-pool D.
    empty = pd.DataFrame()
    path = engine._persist_private_legacy_profit_runtime_features(
        "20260824",
        empty,
    )
    assert path.is_file() and path.stat().st_size > 0
    restored = pd.read_csv(path)
    assert restored.empty
    assert list(restored.columns) == ["signal_date", "ts_code"]


def test_daily_workflow_copies_replay_evidence_and_only_public_sidecar_outputs() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert ".dc20-private/" in (ROOT / ".gitignore").read_text(
        encoding="utf-8"
    ).splitlines()
    assert f'{LEGACY_PROFIT_PRIVATE_RUNTIME_ENV}: "1"' in text
    assert "--exclude='.dc20-private'" in text
    assert "project_decision_legacy_profit_relative_research.py" in text
    assert "validate_repository_chain(source_root)" in text
    assert "validate_repository_chain(target_root)" in text
    assert (
        "data/decision/legacy_profit_relative/runtime_features_${signal_date}.csv"
        in text
    )
    assert '--runtime-features-csv "${canonical_features}"' in text
    assert "private_runtime_copied': False" in text
    assert "Private legacy-profit runtime pool escaped isolation" in text
    assert text.count(
        "outputs/decision/legacy_profit_relative_research/projection_20??????.json"
    ) == 2
    assert text.count(
        "outputs/decision/legacy_profit_relative_research/projection_20??????.csv"
    ) == 2
    assert text.count(
        "outputs/decision/legacy_profit_relative_research/index.json"
    ) == 2
    allowed_lines = [line for line in text.splitlines() if "allowed=(" in line]
    assert len(allowed_lines) == 2
    for line in allowed_lines:
        assert ".dc20-private" not in line
        assert "legacy_profit_relative_research/projection_20??????.json" in line
        assert "legacy_profit_relative_research/projection_20??????.csv" in line
        assert "legacy_profit_relative_research/index.json" in line


def test_zero_candidate_research_score_is_valid_and_never_padded() -> None:
    loaded = load_research_only_legacy_three_engine_snapshot(
        ROOT / SEALED_VALIDATION_PATH,
        root=ROOT,
    )
    empty = pd.DataFrame(columns=["signal_date", "ts_code", "stage", "board"])
    normalized = _normalize_inference_pool(empty, "20260824")
    normalized = attach_runtime_promotion_priors(
        normalized,
        loaded.runtime_prior_ledger,
        signal_date="20260824",
    )
    feature_snapshot = _feature_snapshot_sha256(
        normalized,
        loaded.payloads["profit"]["bundle"].feature_builder,
    )
    three_rank = {
        "signal_date": "20260824",
        "promotion_pool_size": 0,
        "feature_snapshot_sha256": feature_snapshot,
        "top10_members_sha256": top10_members_sha256("20260824", []),
        "rows": [],
    }
    rows, actual_snapshot = score_legacy_profit_relative_rows(
        ROOT,
        signal_date="20260824",
        runtime_candidates=empty,
        three_rank=three_rank,
        loaded=loaded,
    )
    assert rows == []
    assert actual_snapshot == feature_snapshot


def test_private_runtime_env_is_checked_before_frozen_prediction_returns() -> None:
    text = (
        ROOT / "src/top10decision/auction_v3/engine.py"
    ).read_text(encoding="utf-8")
    env_position = text.index("private_runtime_mode = os.environ.get(")
    first_frozen_return = text.index("return frozen", env_position)
    persist_position = text.index(
        "self._persist_private_legacy_profit_runtime_features(",
        env_position,
    )
    assert env_position < persist_position < first_frozen_return
