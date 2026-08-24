from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

import pytest


WORK = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(
    os.environ.get("DC20_EVIDENCE_REPO_ROOT", str(WORK.parents[1]))
).resolve()
STATE_PATH = WORK / "research_state.json"
if str(WORK) not in sys.path:
    sys.path.insert(0, str(WORK))

import validate_research_state as validator  # noqa: E402


def _state() -> dict:
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def _write_state(tmp_path: Path, state: dict) -> Path:
    path = tmp_path / "research_state.json"
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _validate(path: Path) -> dict:
    return validator.validate_research_state(path, repo_root=REPO_ROOT)


def test_current_rejected_research_evidence_is_valid() -> None:
    result = _validate(STATE_PATH)
    assert result["valid"] is True
    assert result["status"] == "RESEARCH_NOT_READY"
    assert result["front_end_allowed"] is False
    assert result["official_trade_action_allowed"] is False
    assert result["model_publish_allowed"] is False
    assert result["promotion_model_touched"] is False
    assert {item["candidate"] for item in result["candidates"]} == {
        "lr_distribution",
        "hgb_distribution",
    }
    assert all(item["rejected"] is True for item in result["candidates"])


def test_tampered_research_status_fails_closed(tmp_path: Path) -> None:
    state = _state()
    state["status"] = "HISTORICAL_READY_FORWARD_SHADOW"
    with pytest.raises(validator.ValidationError, match="RESEARCH_NOT_READY"):
        _validate(_write_state(tmp_path, state))


@pytest.mark.parametrize(
    "field",
    [
        "front_end_allowed",
        "official_trade_action_allowed",
        "model_publish_allowed",
        "production_model_selected",
        "formal_model_artifact_created",
        "release_validator_or_publish_mode_exists",
    ],
)
def test_any_publish_or_release_flag_fails_closed(
    tmp_path: Path, field: str
) -> None:
    state = _state()
    state["publication_boundary"][field] = True
    with pytest.raises(validator.ValidationError, match="must remain false|must not exist"):
        _validate(_write_state(tmp_path, state))


def test_tampered_input_hash_fails_closed(tmp_path: Path) -> None:
    state = _state()
    state["inputs"]["historical_top10_ledger"]["sha256"] = "0" * 64
    with pytest.raises(validator.ValidationError, match="hash drifted"):
        _validate(_write_state(tmp_path, state))


def test_absolute_or_external_input_path_fails_closed(tmp_path: Path) -> None:
    state = _state()
    state["inputs"]["frozen_three_engine_oof"]["path"] = "/tmp/duplicate-oof.csv.gz"
    with pytest.raises(validator.ValidationError, match="repo-relative"):
        _validate(_write_state(tmp_path, state))


def test_tampered_report_hash_fails_closed(tmp_path: Path) -> None:
    state = _state()
    state["candidate_evidence"]["lr_distribution"]["validation_report"][
        "sha256"
    ] = "f" * 64
    with pytest.raises(validator.ValidationError, match="hash drifted"):
        _validate(_write_state(tmp_path, state))


def test_retrospective_window_cannot_be_relabelled_confirmation(
    tmp_path: Path,
) -> None:
    state = _state()
    state["information_state"]["independent_untouched_confirmation_set"] = True
    with pytest.raises(validator.ValidationError, match="untouched confirmation"):
        _validate(_write_state(tmp_path, state))


def test_promotion_touched_claim_fails_closed(tmp_path: Path) -> None:
    state = _state()
    state["promotion_isolation"]["promotion_model_touched"] = True
    with pytest.raises(validator.ValidationError, match="promotion touched"):
        _validate(_write_state(tmp_path, state))


def test_negative_research_fact_cannot_be_flipped_positive(tmp_path: Path) -> None:
    state = _state()
    state["candidate_evidence"]["hgb_distribution"][
        "observed_rejection_metrics"
    ]["top2_mean_return"] = 0.01
    with pytest.raises(validator.ValidationError, match="mean_return drifted"):
        _validate(_write_state(tmp_path, state))
