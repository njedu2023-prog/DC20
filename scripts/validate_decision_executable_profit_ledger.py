#!/usr/bin/env python3
"""Fail-closed validation for the executable-profit historical OOF ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_decision_executable_profit_ledger import (
    BUYABILITY_LABEL_VERSION,
    DEFAULT_CONTRACT,
    DEFAULT_LEDGER,
    DEFAULT_MANIFEST,
    EXIT_PROXY_VERSION,
    FEATURE_SNAPSHOT_SCHEMA,
    LEDGER_SCHEMA,
    MANIFEST_SCHEMA,
    OUTPUT_STATUS,
    ExecutableProfitLedgerError,
    _canonical_sha256,
    _deterministic_gzip_csv,
    _load_json,
    _mapping,
    _read_csv,
    _read_open_dates,
    _sha256,
    _validate_source_pins,
    build_historical_frame,
)
from scripts.validate_decision_executable_profit_shadow_contract import (
    validate_contract,
)


class ExecutableProfitLedgerValidationError(RuntimeError):
    """Raised when the materialized independent ledger is not reproducible."""


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise ExecutableProfitLedgerValidationError(message)


def _resolve_input(repo_root: Path, override: Path | None, relative: str) -> Path:
    if override is not None:
        return override.resolve()
    return (repo_root / relative).resolve()


def validate_ledger(
    *,
    repo_root: Path,
    contract_path: Path,
    ledger_path: Path,
    manifest_path: Path,
    source_ledger_path: Path,
    oof_path: Path,
    calendar_path: Path,
    validate_frozen_contract: bool = True,
) -> dict[str, Any]:
    root = repo_root.resolve()
    contract_absolute = contract_path if contract_path.is_absolute() else root / contract_path
    contract = _load_json(contract_absolute)
    if validate_frozen_contract:
        try:
            relative_contract = contract_absolute.relative_to(root)
        except ValueError as exc:
            raise ExecutableProfitLedgerValidationError(
                "validated contract must be inside repo_root"
            ) from exc
        result = validate_contract(root, relative_contract)
        _expect(result.get("valid") is True, "frozen design contract validation failed")

    manifest = _load_json(manifest_path)
    _expect(manifest.get("schema_version") == MANIFEST_SCHEMA, "manifest schema drifted")
    _expect(
        manifest.get("ledger_schema_version") == LEDGER_SCHEMA,
        "ledger schema drifted",
    )
    _expect(manifest.get("status") == OUTPUT_STATUS, "ledger status drifted")
    _expect(
        manifest.get("owner") == "njedu2023-prog/DC20"
        and manifest.get("repository") == "njedu2023-prog/DC20"
        and manifest.get("branch") == "main"
        and manifest.get("runtime_dependency_on_top10_decision") is False,
        "DC20 ownership or independence drifted",
    )
    contract_binding = _mapping(manifest.get("contract"), "manifest contract")
    identity = _mapping(contract.get("promotion_identity"), "promotion_identity")
    expected_contract_binding = {
        "path": DEFAULT_CONTRACT.as_posix(),
        "id": contract.get("contract_id"),
        "schema_version": contract.get("schema_version"),
        "promotion_freeze_id": identity.get("freeze_id"),
        "promotion_artifact_sha256": _mapping(
            identity.get("model"), "promotion model"
        ).get("artifact_sha256"),
    }
    _expect(
        contract_binding == expected_contract_binding,
        "manifest no longer binds the frozen contract",
    )
    pins = _validate_source_pins(
        contract,
        source_ledger_path=source_ledger_path,
        oof_path=oof_path,
        calendar_path=calendar_path,
    )
    expected_inputs = {
        "five_year_source_ledger": {
            "path": _mapping(identity.get("source_ledger"), "source").get("path"),
            "sha256": pins["source_ledger_sha256"],
            "role": "D-known features and outcome truth only",
        },
        "promotion_oof_top10": {
            "path": _mapping(identity.get("oof_top10"), "OOF").get("path"),
            "sha256": pins["oof_top10_sha256"],
            "role": "time-honest historical Top10 membership and audit metadata only",
        },
        "strict_sse_calendar": {
            "path": _mapping(identity.get("calendar"), "calendar").get("path"),
            "sha256": pins["calendar_sha256"],
            "source": "tushare:trade_cal:SSE",
            "strict": True,
        },
    }
    _expect(manifest.get("inputs") == expected_inputs, "manifest input bindings drifted")

    expected, audit = build_historical_frame(
        source=_read_csv(source_ledger_path),
        oof=_read_csv(oof_path),
        open_dates=_read_open_dates(calendar_path),
        contract=contract,
    )
    expected_bytes = _deterministic_gzip_csv(expected)
    expected_sha = hashlib.sha256(expected_bytes).hexdigest()
    actual_sha = _sha256(ledger_path)
    output = _mapping(manifest.get("output"), "manifest output")
    _expect(
        actual_sha == expected_sha == output.get("sha256"),
        "materialized ledger bytes are not reproducible from frozen inputs",
    )
    expected_output = {
        "path": DEFAULT_LEDGER.as_posix(),
        "sha256": expected_sha,
        "bytes": len(expected_bytes),
        "rows": int(len(expected)),
        "signal_dates": int(expected["signal_date"].nunique()),
        "columns": list(expected.columns),
        "compression": "gzip_mtime_0",
        "sort": ["signal_date", "promotion_rank", "ts_code"],
    }
    _expect(output == expected_output, "deterministic output contract drifted")

    ledger = pd.read_csv(
        ledger_path,
        dtype={
            "signal_date": "string",
            "exec_date": "string",
            "scheduled_exit_date": "string",
            "ts_code": "string",
        },
        low_memory=False,
    )
    _expect(list(ledger.columns) == output.get("columns"), "ledger columns drifted")
    _expect(
        len(ledger) == output.get("rows") == audit["coverage"]["rows"]
        and ledger["signal_date"].nunique()
        == output.get("signal_dates")
        == audit["coverage"]["signal_dates"],
        "ledger row/date counts drifted",
    )
    _expect(
        not ledger.duplicated(["signal_date", "ts_code"]).any(),
        "ledger identity keys are not unique",
    )
    ordered = ledger.sort_values(
        ["signal_date", "promotion_rank", "ts_code"], kind="stable"
    ).reset_index(drop=True)
    _expect(ledger.reset_index(drop=True).equals(ordered), "ledger sort order drifted")

    expected_candidate_contract = {
        "scope": "exact frozen promotion OOF Top10 only",
        "hard_stage_scope": ["2_to_3", "3_to_4"],
        "promotion_rank_role": "audit and same-date baseline only; never a model feature",
        "old_profit_big_loss_pfill_predictions_copied": False,
    }
    _expect(
        manifest.get("candidate_contract") == expected_candidate_contract,
        "candidate contract drifted",
    )

    features = _mapping(manifest.get("feature_contract"), "feature contract")
    feature_columns = audit["feature_columns"]
    _expect(isinstance(feature_columns, list) and feature_columns, "feature columns missing")
    expected_feature_contract = {
        "known_at": "D close",
        "columns": feature_columns,
        "columns_sha256": audit["feature_columns_sha256"],
        "raw_columns": audit["raw_feature_columns"],
        "promotion_rank_or_probability_used": False,
        "future_or_cross_head_outputs_used": False,
        "feature_snapshot_schema": FEATURE_SNAPSHOT_SCHEMA,
        "nonnull_fraction": audit["feature_nonnull_fraction"],
    }
    _expect(features == expected_feature_contract, "feature contract drifted")
    _expect(
        features.get("columns_sha256") == _canonical_sha256(feature_columns)
        == audit["feature_columns_sha256"],
        "model feature identity drifted",
    )
    _expect(
        features.get("promotion_rank_or_probability_used") is False
        and features.get("future_or_cross_head_outputs_used") is False,
        "forbidden model feature policy drifted",
    )
    _expect(
        "predicted_promotion_probability" not in ledger.columns
        and "promotion_rank" not in feature_columns,
        "promotion output leaked into model features",
    )

    expected_label_contract = {
        "buyability": BUYABILITY_LABEL_VERSION,
        "buyability_is_actual_order_fill": False,
        "conditional_return_window": "T daily open proxy to T+1 daily open proxy",
        "round_trip_cost_rate": 0.0045,
        "stress_round_trip_cost_rate": 0.009,
        "conditional_return_bucket": {
            "BIG_LOSS": "net_return <= -0.03",
            "NON_PROFIT": "-0.03 < net_return <= 0",
            "PROFIT": "net_return > 0",
        },
        "nonbuyable_conditional_return": None,
        "nonbuyable_strategy_slot_return": 0.0,
        "missing_outcome_policy": "PENDING_NOT_DROPPED",
        "exit_proxy_version": EXIT_PROXY_VERSION,
        "blocked_limit_down_exit_truth_available": False,
        "actual_order_fill_observed": False,
        "actual_execution_claimed": False,
    }
    _expect(
        manifest.get("label_contract") == expected_label_contract,
        "label contract or execution claim drifted",
    )

    for column in (
        "actual_order_fill_observed",
        "actual_execution_claimed",
        "blocked_limit_down_exit_truth_available",
    ):
        values = pd.to_numeric(ledger[column], errors="coerce")
        _expect(values.eq(0).all(), f"{column} must remain false")
    release = _mapping(manifest.get("release"), "release")
    expected_release = {
        "historical_ledger_ready": True,
        "model_trained": False,
        "front_end_shadow_rank_allowed": False,
        "official_trade_action_allowed": False,
        "reason": (
            "Research-proxy ledger is ready; model, independent time validation, "
            "forward Shadow evidence, and blocked-exit truth remain pending."
        ),
    }
    _expect(
        release == expected_release,
        "historical ledger must not imply model or trade release",
    )
    gates = _mapping(manifest.get("quality_gates"), "quality_gates")
    _expect(bool(gates) and all(_mapping(gate, name).get("passed") is True for name, gate in gates.items()), "a published ledger gate is not passing")
    _expect(gates == audit["gates"], "manifest quality gates drifted from rebuilt truth")
    _expect(manifest.get("coverage") == audit["coverage"], "manifest coverage drifted")
    return {
        "valid": True,
        "status": manifest.get("status"),
        "ledger_sha256": actual_sha,
        "manifest_sha256": _sha256(manifest_path),
        "rows": int(len(ledger)),
        "signal_dates": int(ledger["signal_date"].nunique()),
        "matured_conditional_return_rows": int(
            audit["coverage"]["matured_conditional_return_rows"]
        ),
        "model_trained": False,
        "official_trade_action_allowed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate DC20's executable-profit historical OOF ledger."
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--source-ledger", type=Path)
    parser.add_argument("--oof-top10", type=Path)
    parser.add_argument("--calendar", type=Path)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    contract_path = args.contract if args.contract.is_absolute() else root / args.contract
    try:
        contract = _load_json(contract_path)
        identity = _mapping(contract.get("promotion_identity"), "promotion_identity")
        result = validate_ledger(
            repo_root=root,
            contract_path=args.contract,
            ledger_path=(args.ledger.resolve() if args.ledger else root / DEFAULT_LEDGER),
            manifest_path=(
                args.manifest.resolve() if args.manifest else root / DEFAULT_MANIFEST
            ),
            source_ledger_path=_resolve_input(
                root,
                args.source_ledger,
                str(_mapping(identity.get("source_ledger"), "source").get("path")),
            ),
            oof_path=_resolve_input(
                root,
                args.oof_top10,
                str(_mapping(identity.get("oof_top10"), "OOF").get("path")),
            ),
            calendar_path=_resolve_input(
                root,
                args.calendar,
                str(_mapping(identity.get("calendar"), "calendar").get("path")),
            ),
        )
    except (
        ExecutableProfitLedgerError,
        ExecutableProfitLedgerValidationError,
        OSError,
        ValueError,
    ) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
