from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_decision_executable_profit_ledger import (  # noqa: E402
    DEFAULT_CONTRACT,
    DEFAULT_LEDGER,
    DEFAULT_MANIFEST,
    build_ledger,
)
from scripts.validate_decision_executable_profit_ledger import (  # noqa: E402
    ExecutableProfitLedgerValidationError,
    validate_ledger,
)


def _contract() -> dict:
    return json.loads((ROOT / DEFAULT_CONTRACT).read_text(encoding="utf-8"))


def _input_paths() -> tuple[Path, Path, Path]:
    identity = _contract()["promotion_identity"]
    return (
        ROOT / identity["source_ledger"]["path"],
        ROOT / identity["oof_top10"]["path"],
        ROOT / identity["calendar"]["path"],
    )


def _validate(*, ledger: Path | None = None, manifest: Path | None = None) -> dict:
    source, oof, calendar = _input_paths()
    return validate_ledger(
        repo_root=ROOT,
        contract_path=DEFAULT_CONTRACT,
        ledger_path=ledger or ROOT / DEFAULT_LEDGER,
        manifest_path=manifest or ROOT / DEFAULT_MANIFEST,
        source_ledger_path=source,
        oof_path=oof,
        calendar_path=calendar,
    )


def test_repository_executable_profit_historical_ledger_is_reproducible() -> None:
    assert _validate() == {
        "valid": True,
        "status": "HISTORICAL_LEDGER_READY_RESEARCH_PROXY",
        "ledger_sha256": (
            "b3addf99a0f30c784b6a2ae190c3bf6f67f9b1b4a64325193b8d962d6ee2dedd"
        ),
        "manifest_sha256": (
            "3fd457dbe8438b28bbd80d0521ebd9a2ba2d17845be019412238b7898cce69f5"
        ),
        "rows": 6753,
        "signal_dates": 910,
        "matured_conditional_return_rows": 5790,
        "model_trained": False,
        "official_trade_action_allowed": False,
    }


def test_repository_ledger_keeps_proxy_and_execution_semantics_separate() -> None:
    ledger = pd.read_csv(ROOT / DEFAULT_LEDGER, low_memory=False)
    manifest = json.loads((ROOT / DEFAULT_MANIFEST).read_text(encoding="utf-8"))
    assert len(ledger) == 6753
    assert ledger["signal_date"].nunique() == 910
    assert not ledger.duplicated(["signal_date", "ts_code"]).any()
    assert "predicted_promotion_probability" not in ledger.columns
    assert "predicted_profit_probability" not in ledger.columns
    assert "predicted_big_loss_probability" not in ledger.columns
    assert "p_fill_shadow_probability" not in ledger.columns
    assert "promotion_rank" not in manifest["feature_contract"]["columns"]
    assert ledger["actual_order_fill_observed"].eq(0).all()
    assert ledger["actual_execution_claimed"].eq(0).all()
    assert ledger["blocked_limit_down_exit_truth_available"].eq(0).all()

    fill = pd.to_numeric(ledger["public_market_buyable_proxy"], errors="coerce")
    not_buyable = fill.eq(0)
    pending_entry = fill.isna()
    pending_exit = fill.eq(1) & ledger["conditional_net_return_after_cost"].isna()
    assert int(fill.eq(1).sum()) == 5797
    assert int(not_buyable.sum()) == 955
    assert int(pending_entry.sum()) == 1
    assert int(pending_exit.sum()) == 7
    assert ledger.loc[not_buyable, "conditional_net_return_after_cost"].isna().all()
    assert ledger.loc[not_buyable, "conditional_profit_hit"].isna().all()
    assert ledger.loc[not_buyable, "strategy_slot_net_return"].eq(0).all()
    assert ledger.loc[pending_entry | pending_exit, "strategy_slot_net_return"].isna().all()
    assert manifest["release"] == {
        "historical_ledger_ready": True,
        "model_trained": False,
        "front_end_shadow_rank_allowed": False,
        "official_trade_action_allowed": False,
        "reason": (
            "Research-proxy ledger is ready; model, independent time validation, "
            "forward Shadow evidence, and blocked-exit truth remain pending."
        ),
    }


def test_same_frozen_inputs_build_byte_identical_outputs(tmp_path: Path) -> None:
    source, oof, calendar = _input_paths()
    first_ledger = tmp_path / "first.csv.gz"
    first_manifest = tmp_path / "first.json"
    second_ledger = tmp_path / "second.csv.gz"
    second_manifest = tmp_path / "second.json"
    for ledger, manifest in (
        (first_ledger, first_manifest),
        (second_ledger, second_manifest),
    ):
        build_ledger(
            repo_root=ROOT,
            contract_path=DEFAULT_CONTRACT,
            source_ledger_path=source,
            oof_path=oof,
            calendar_path=calendar,
            output_ledger_path=ledger,
            output_manifest_path=manifest,
        )
    assert first_ledger.read_bytes() == second_ledger.read_bytes()
    assert first_manifest.read_bytes() == second_manifest.read_bytes()


def test_materialized_ledger_byte_tamper_fails_closed(tmp_path: Path) -> None:
    changed = tmp_path / "changed.csv.gz"
    shutil.copyfile(ROOT / DEFAULT_LEDGER, changed)
    changed.write_bytes(changed.read_bytes() + b"tamper")
    with pytest.raises(
        ExecutableProfitLedgerValidationError,
        match="not reproducible from frozen inputs",
    ):
        _validate(ledger=changed)


def test_manifest_cannot_relabel_proxy_as_actual_execution(tmp_path: Path) -> None:
    changed = tmp_path / "changed.json"
    payload = json.loads((ROOT / DEFAULT_MANIFEST).read_text(encoding="utf-8"))
    payload["label_contract"]["buyability_is_actual_order_fill"] = True
    payload["label_contract"]["actual_execution_claimed"] = True
    changed.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        ExecutableProfitLedgerValidationError,
        match="label contract or execution claim drifted",
    ):
        _validate(manifest=changed)
