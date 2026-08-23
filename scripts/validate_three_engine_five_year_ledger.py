#!/usr/bin/env python3
"""Fail-closed validation for DC20's five-year three-engine ledger.

This validator establishes that the owned ledger is large enough, internally
consistent, and label-compatible with the previously frozen Tushare history.
``market_fill`` is deliberately treated only as a public-market feasibility
proxy.  It is never reported as an observed order fill and disagreement with
the legacy proxy is diagnostic rather than a hard gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = (
    ROOT / "data" / "decision_three_engines" / "five_year_supervised_ledger.csv.gz"
)
DEFAULT_MANIFEST = (
    ROOT / "data" / "decision_three_engines" / "five_year_ledger_manifest.json"
)
DEFAULT_LEGACY = ROOT / "models" / "decision_v12_frozen_history_20260805.csv.gz"
DEFAULT_OUTPUT = ROOT / "models" / "decision_three_engine_data_validation.json"

REPORT_SCHEMA = "dc20_three_engine_five_year_data_validation_v1"
EXPECTED_OWNER = "njedu2023-prog/DC20"
EXPECTED_RUNTIME_FEATURE_VERSION = "dc20_daily_candidate_d_close_v1"
EXPECTED_RUNTIME_FEATURE_COLUMNS = (
    "returns_1d",
    "high_low_range",
    "candle_body",
    "gap_open",
    "vol",
    "volume_ratio",
    "volatility_5d",
    "volatility_10d",
    "volatility_20d",
    "atr",
    "ret_2d",
    "ret_5d",
    "ret_10d",
    "bid_ask_proxy",
    "spread_proxy",
)
ALLOWED_STAGES = (2, 3)
ALLOWED_BOARDS = ("SH_MAIN", "SZ_MAIN")
IDENTITY_COLUMNS = ("signal_date", "ts_code")
LABEL_CONTRACTS = {
    "promotion_hit": "continuation_limit_up_hit",
    "big_loss_hit": "big_loss_hit",
    "profit_hit": "profit_hit",
}
LEDGER_REQUIRED_COLUMNS = {
    *IDENTITY_COLUMNS,
    "stage",
    "board",
    "mechanism_limit_pct",
    "promotion_hit",
    "big_loss_hit",
    "profit_hit",
    "market_fill",
    "d_open",
    "d_close",
    "d_high",
    "d_low",
    "t_open",
    "t_close",
    "t_high",
    "t_low",
    "tplus1_open",
    *EXPECTED_RUNTIME_FEATURE_COLUMNS,
}
LEGACY_REQUIRED_COLUMNS = {
    *IDENTITY_COLUMNS,
    "continuation_limit_up_hit",
    "big_loss_hit",
    "profit_hit",
    "market_fill",
}


class LedgerValidationError(RuntimeError):
    """Raised when inputs cannot be evaluated against the validation contract."""


@dataclass(frozen=True)
class ValidationThresholds:
    min_signal_dates: int = 1100
    min_rows: int = 10000
    min_class_rows: int = 200
    min_price_coverage: float = 0.98
    min_legacy_overlap_rows: int = 7000
    min_promotion_agreement: float = 0.99
    min_return_label_agreement: float = 0.95


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_text(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"


def _atomic_json(path: Path, payload: Any) -> None:
    rendered = _json_text(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(rendered)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _require_columns(frame: pd.DataFrame, required: Iterable[str], name: str) -> None:
    missing = sorted(set(required) - set(frame.columns))
    if missing:
        raise LedgerValidationError(f"{name} missing required columns: {missing}")


def _gate(*, passed: bool, actual: Any, requirement: str) -> dict[str, Any]:
    return {
        "passed": bool(passed),
        "actual": actual,
        "requirement": requirement,
    }


def _positive_price_coverage(frame: pd.DataFrame, columns: list[str]) -> float:
    numeric = frame[columns].apply(pd.to_numeric, errors="coerce")
    complete = numeric.notna().all(axis=1) & numeric.gt(0.0).all(axis=1)
    return float(complete.mean()) if len(frame) else 0.0


def _label_stats(frame: pd.DataFrame, column: str) -> dict[str, int]:
    original = frame[column]
    numeric = pd.to_numeric(original, errors="coerce")
    valid = numeric.isin((0, 1))
    return {
        "zero": int(numeric.eq(0).sum()),
        "one": int(numeric.eq(1).sum()),
        "missing": int(numeric.isna().sum()),
        "invalid": int((original.notna() & ~valid).sum()),
        "labeled": int(valid.sum()),
    }


def _agreement(frame: pd.DataFrame, left: str, right: str) -> dict[str, Any]:
    left_values = pd.to_numeric(frame[left], errors="coerce")
    right_values = pd.to_numeric(frame[right], errors="coerce")
    comparable = left_values.isin((0, 1)) & right_values.isin((0, 1))
    sample_count = int(comparable.sum())
    match_count = int(left_values.loc[comparable].eq(right_values.loc[comparable]).sum())
    rate = float(match_count / sample_count) if sample_count else None
    return {
        "comparable_rows": sample_count,
        "matches": match_count,
        "mismatches": sample_count - match_count,
        "agreement": rate,
    }


def _load_inputs(
    ledger_path: Path,
    manifest_path: Path,
    legacy_path: Path,
) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    for path, name in (
        (ledger_path, "ledger"),
        (manifest_path, "manifest"),
        (legacy_path, "legacy history"),
    ):
        if not path.is_file():
            raise LedgerValidationError(f"{name} input does not exist: {path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LedgerValidationError(f"cannot read ledger manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise LedgerValidationError("ledger manifest must be a JSON object")
    date_code_types = {"signal_date": "string", "ts_code": "string"}
    try:
        ledger = pd.read_csv(ledger_path, dtype=date_code_types, low_memory=False)
        legacy = pd.read_csv(legacy_path, dtype=date_code_types, low_memory=False)
    except (OSError, ValueError) as exc:
        raise LedgerValidationError(f"cannot read CSV inputs: {exc}") from exc
    _require_columns(ledger, LEDGER_REQUIRED_COLUMNS, "ledger")
    _require_columns(legacy, LEGACY_REQUIRED_COLUMNS, "legacy history")
    return ledger, manifest, legacy


def validate_three_engine_five_year_ledger(
    ledger_path: Path | str = DEFAULT_LEDGER,
    manifest_path: Path | str = DEFAULT_MANIFEST,
    legacy_path: Path | str = DEFAULT_LEGACY,
    *,
    thresholds: ValidationThresholds | None = None,
) -> dict[str, Any]:
    """Return a strict-JSON validation report without mutating input artifacts."""

    ledger_path = Path(ledger_path).resolve()
    manifest_path = Path(manifest_path).resolve()
    legacy_path = Path(legacy_path).resolve()
    thresholds = thresholds or ValidationThresholds()
    ledger, manifest, legacy = _load_inputs(ledger_path, manifest_path, legacy_path)

    actual_ledger_sha = _sha256(ledger_path)
    manifest_ledger_sha = str(manifest.get("ledger_sha256") or "").lower()
    owner = manifest.get("owner")
    runtime_dependency = manifest.get("runtime_dependency_on_top10_decision")
    runtime_feature_contract = manifest.get("runtime_feature_contract")
    runtime_feature_contract = (
        runtime_feature_contract
        if isinstance(runtime_feature_contract, dict)
        else {}
    )
    runtime_feature_columns = runtime_feature_contract.get("columns")
    runtime_feature_columns = (
        runtime_feature_columns if isinstance(runtime_feature_columns, list) else []
    )
    runtime_feature_coverage = {
        column: float(pd.to_numeric(ledger[column], errors="coerce").notna().mean())
        for column in EXPECTED_RUNTIME_FEATURE_COLUMNS
    }
    source_contract = manifest.get("source")
    source_contract = source_contract if isinstance(source_contract, dict) else {}
    event_inventory = source_contract.get("event_source_inventory")
    event_inventory = event_inventory if isinstance(event_inventory, dict) else {}
    canonical_files = event_inventory.get("canonical_prediction_files")
    canonical_files = canonical_files if isinstance(canonical_files, list) else []
    inventory_rows = 0
    inventory_dates: list[str] = []
    inventory_valid = True
    for item in canonical_files:
        if not isinstance(item, dict):
            inventory_valid = False
            continue
        path = str(item.get("path") or "")
        date = str(item.get("signal_date") or "")
        digest = str(item.get("sha256") or "")
        eligible_rows = item.get("eligible_rows")
        source_rows = item.get("source_rows")
        match = re.fullmatch(
            r"outputs/auction_v3/predictions/pred_(20\d{6})\.csv", path
        )
        if (
            match is None
            or match.group(1) != date
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or type(eligible_rows) is not int
            or type(source_rows) is not int
            or not (0 <= eligible_rows <= source_rows)
        ):
            inventory_valid = False
            continue
        inventory_rows += eligible_rows
        inventory_dates.append(date)
    inventory_valid = bool(
        inventory_valid
        and event_inventory.get("canonical_prediction_file_count")
        == len(canonical_files)
        and event_inventory.get("new_eligible_rows_discovered") == inventory_rows
        and inventory_dates == sorted(set(inventory_dates))
        and all(
            date > str(event_inventory.get("seed_end_signal_date") or "")
            for date in inventory_dates
        )
        and re.fullmatch(
            r"[0-9a-f]{64}", str(event_inventory.get("seed_sha256") or "")
        )
        is not None
    )

    signal_dates = ledger["signal_date"].fillna("").astype(str).str.strip()
    codes = ledger["ts_code"].fillna("").astype(str).str.strip().str.upper()
    identity_duplicate_rows = int(ledger.duplicated(list(IDENTITY_COLUMNS), keep=False).sum())
    invalid_signal_dates = int((~signal_dates.str.fullmatch(r"[0-9]{8}").fillna(False)).sum())
    invalid_codes = int((~codes.str.fullmatch(r"[0-9]{6}\.(?:SH|SZ)").fillna(False)).sum())
    signal_date_count = int(signal_dates[signal_dates.ne("")].nunique())

    stage_values = pd.to_numeric(ledger["stage"], errors="coerce")
    observed_stages = sorted(
        int(value)
        for value in stage_values.dropna().unique()
        if math.isfinite(float(value)) and float(value).is_integer()
    )
    invalid_stage_rows = int((~stage_values.isin(ALLOWED_STAGES)).sum())

    boards = ledger["board"].fillna("").astype(str).str.strip().str.upper()
    observed_boards = sorted(boards[boards.ne("")].unique().tolist())
    invalid_board_rows = int((~boards.isin(ALLOWED_BOARDS)).sum())
    mechanism = pd.to_numeric(ledger["mechanism_limit_pct"], errors="coerce")
    invalid_mechanism_rows = int((mechanism.isna() | mechanism.sub(10.0).abs().gt(1e-9)).sum())

    d_price_coverage = _positive_price_coverage(
        ledger, ["d_open", "d_close", "d_high", "d_low"]
    )
    t_price_coverage = _positive_price_coverage(
        ledger, ["t_open", "t_close", "t_high", "t_low"]
    )
    tplus1_price_coverage = _positive_price_coverage(ledger, ["tplus1_open"])

    label_stats = {
        column: _label_stats(ledger, column) for column in LABEL_CONTRACTS
    }

    legacy_duplicate_rows = int(
        legacy.duplicated(list(IDENTITY_COLUMNS), keep=False).sum()
    )
    legacy_projection_columns = [
        *IDENTITY_COLUMNS,
        "continuation_limit_up_hit",
        "big_loss_hit",
        "profit_hit",
        "market_fill",
    ]
    for optional in (
        "history_source",
        "actual_order_fill_observed",
        "actual_order_fill",
    ):
        if optional in legacy.columns:
            legacy_projection_columns.append(optional)
    legacy_projection = legacy[legacy_projection_columns].rename(
        columns={
            "continuation_limit_up_hit": "legacy_promotion_hit",
            "big_loss_hit": "legacy_big_loss_hit",
            "profit_hit": "legacy_profit_hit",
            "market_fill": "legacy_market_fill",
        }
    )
    ledger_projection = ledger[
        [
            *IDENTITY_COLUMNS,
            "promotion_hit",
            "big_loss_hit",
            "profit_hit",
            "market_fill",
        ]
    ].rename(
        columns={
            "promotion_hit": "ledger_promotion_hit",
            "big_loss_hit": "ledger_big_loss_hit",
            "profit_hit": "ledger_profit_hit",
            "market_fill": "ledger_market_fill",
        }
    )
    overlap = ledger_projection.merge(
        legacy_projection,
        on=list(IDENTITY_COLUMNS),
        how="inner",
        validate="one_to_one" if not (identity_duplicate_rows or legacy_duplicate_rows) else None,
    )
    overlap_rows = int(len(overlap))
    agreements = {
        "promotion_hit": _agreement(
            overlap, "ledger_promotion_hit", "legacy_promotion_hit"
        ),
        "big_loss_hit": _agreement(
            overlap, "ledger_big_loss_hit", "legacy_big_loss_hit"
        ),
        "profit_hit": _agreement(
            overlap, "ledger_profit_hit", "legacy_profit_hit"
        ),
    }

    ledger_fill = pd.to_numeric(overlap["ledger_market_fill"], errors="coerce")
    legacy_fill = pd.to_numeric(overlap["legacy_market_fill"], errors="coerce")
    fill_comparable = ledger_fill.isin((0, 1)) & legacy_fill.isin((0, 1))
    fill_comparable_rows = int(fill_comparable.sum())
    fill_conflict_rows = int(
        ledger_fill.loc[fill_comparable].ne(legacy_fill.loc[fill_comparable]).sum()
    )
    fill_conflict_rate = (
        float(fill_conflict_rows / fill_comparable_rows)
        if fill_comparable_rows
        else None
    )
    actual_order_observed_positive = 0
    actual_order_labeled = 0
    if "actual_order_fill_observed" in overlap.columns:
        observed = pd.to_numeric(
            overlap["actual_order_fill_observed"], errors="coerce"
        )
        actual_order_observed_positive = int(observed.eq(1).sum())
    if "actual_order_fill" in overlap.columns:
        actual = pd.to_numeric(overlap["actual_order_fill"], errors="coerce")
        actual_order_labeled = int(actual.isin((0, 1)).sum())

    gates: dict[str, dict[str, Any]] = {
        "owner_is_dc20": _gate(
            passed=owner == EXPECTED_OWNER,
            actual=owner,
            requirement=f"owner == {EXPECTED_OWNER}",
        ),
        "no_top10_decision_runtime_dependency": _gate(
            passed=runtime_dependency is False,
            actual=runtime_dependency,
            requirement="runtime_dependency_on_top10_decision is exactly false",
        ),
        "ledger_sha256_matches_manifest": _gate(
            passed=manifest_ledger_sha == actual_ledger_sha,
            actual={"manifest": manifest_ledger_sha, "computed": actual_ledger_sha},
            requirement="manifest ledger_sha256 equals ledger bytes SHA-256",
        ),
        "runtime_feature_manifest_exact": _gate(
            passed=(
                runtime_feature_contract.get("version")
                == EXPECTED_RUNTIME_FEATURE_VERSION
                and runtime_feature_columns
                == list(EXPECTED_RUNTIME_FEATURE_COLUMNS)
                and runtime_feature_contract.get("available_by_d_close") is True
                and runtime_feature_contract.get("future_columns_used") == []
            ),
            actual={
                "version": runtime_feature_contract.get("version"),
                "columns": runtime_feature_columns,
                "available_by_d_close": runtime_feature_contract.get(
                    "available_by_d_close"
                ),
                "future_columns_used": runtime_feature_contract.get(
                    "future_columns_used"
                ),
            },
            requirement=(
                f"exact {EXPECTED_RUNTIME_FEATURE_VERSION} D-close runtime feature contract"
            ),
        ),
        "runtime_feature_coverage": _gate(
            passed=all(
                coverage >= thresholds.min_price_coverage
                for coverage in runtime_feature_coverage.values()
            ),
            actual=runtime_feature_coverage,
            requirement=(
                f"every runtime-aligned D-close feature coverage >= "
                f"{thresholds.min_price_coverage}"
            ),
        ),
        "owned_event_source_inventory": _gate(
            passed=inventory_valid,
            actual={
                "seed_end_signal_date": event_inventory.get(
                    "seed_end_signal_date"
                ),
                "canonical_prediction_file_count": len(canonical_files),
                "new_eligible_rows_discovered": inventory_rows,
                "dates": inventory_dates,
            },
            requirement=(
                "hashed canonical pred_YYYYMMDD DC20 event files, unique and newer than seed"
            ),
        ),
        "promotion_prior_truth_is_strictly_lagged": _gate(
            passed=(
                source_contract.get("prior_grid_truth_cutoff_rule")
                == "strictly_before_signal_date"
            ),
            actual=source_contract.get("prior_grid_truth_cutoff_rule"),
            requirement="prior_grid_truth_cutoff_rule == strictly_before_signal_date",
        ),
        "signal_date_code_identity_unique": _gate(
            passed=(
                identity_duplicate_rows == 0
                and invalid_signal_dates == 0
                and invalid_codes == 0
            ),
            actual={
                "duplicate_rows": identity_duplicate_rows,
                "invalid_signal_dates": invalid_signal_dates,
                "invalid_codes": invalid_codes,
            },
            requirement="every (signal_date, ts_code) is valid and unique",
        ),
        "exact_stage_2_and_3_universe": _gate(
            passed=invalid_stage_rows == 0 and observed_stages == list(ALLOWED_STAGES),
            actual={
                "observed_stages": observed_stages,
                "invalid_rows": invalid_stage_rows,
            },
            requirement="only stages 2 and 3 are present, with both represented",
        ),
        "standard_10pct_main_board_only": _gate(
            passed=invalid_board_rows == 0 and invalid_mechanism_rows == 0,
            actual={
                "observed_boards": observed_boards,
                "invalid_board_rows": invalid_board_rows,
                "invalid_mechanism_rows": invalid_mechanism_rows,
            },
            requirement="SH_MAIN/SZ_MAIN rows with mechanism_limit_pct == 10",
        ),
        "minimum_signal_dates": _gate(
            passed=signal_date_count >= thresholds.min_signal_dates,
            actual=signal_date_count,
            requirement=f">= {thresholds.min_signal_dates}",
        ),
        "minimum_rows": _gate(
            passed=len(ledger) >= thresholds.min_rows,
            actual=int(len(ledger)),
            requirement=f">= {thresholds.min_rows}",
        ),
        "d_price_coverage": _gate(
            passed=d_price_coverage >= thresholds.min_price_coverage,
            actual=d_price_coverage,
            requirement=f">= {thresholds.min_price_coverage}",
        ),
        "t_price_coverage": _gate(
            passed=t_price_coverage >= thresholds.min_price_coverage,
            actual=t_price_coverage,
            requirement=f">= {thresholds.min_price_coverage}",
        ),
        "tplus1_price_coverage": _gate(
            passed=tplus1_price_coverage >= thresholds.min_price_coverage,
            actual=tplus1_price_coverage,
            requirement=f">= {thresholds.min_price_coverage}",
        ),
        "legacy_identity_unique": _gate(
            passed=legacy_duplicate_rows == 0,
            actual={"duplicate_rows": legacy_duplicate_rows},
            requirement="legacy (signal_date, ts_code) identities are unique",
        ),
        "minimum_legacy_overlap": _gate(
            passed=overlap_rows >= thresholds.min_legacy_overlap_rows,
            actual=overlap_rows,
            requirement=f">= {thresholds.min_legacy_overlap_rows}",
        ),
    }
    for label, stats in label_stats.items():
        gates[f"{label}_dual_class"] = _gate(
            passed=(
                stats["invalid"] == 0
                and stats["zero"] >= thresholds.min_class_rows
                and stats["one"] >= thresholds.min_class_rows
            ),
            actual=stats,
            requirement=(
                f"both binary classes >= {thresholds.min_class_rows}; invalid == 0"
            ),
        )
    for label, agreement in agreements.items():
        threshold = (
            thresholds.min_promotion_agreement
            if label == "promotion_hit"
            else thresholds.min_return_label_agreement
        )
        rate = agreement["agreement"]
        gates[f"{label}_legacy_agreement"] = _gate(
            passed=rate is not None and rate >= threshold,
            actual=agreement,
            requirement=f">= {threshold} on mutually labeled overlap rows",
        )

    failed_gates = sorted(name for name, gate in gates.items() if not gate["passed"])
    legacy_sources: dict[str, int] = {}
    if "history_source" in overlap.columns:
        legacy_sources = {
            str(name): int(count)
            for name, count in overlap["history_source"]
            .fillna("<missing>")
            .value_counts()
            .sort_index()
            .items()
        }
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if not failed_gates else "FAIL",
        "valid": not failed_gates,
        "inputs": {
            "ledger": {
                "path": _display_path(ledger_path),
                "sha256": actual_ledger_sha,
            },
            "manifest": {
                "path": _display_path(manifest_path),
                "sha256": _sha256(manifest_path),
            },
            "legacy_history": {
                "path": _display_path(legacy_path),
                "sha256": _sha256(legacy_path),
            },
        },
        "independence": {
            "owner": owner,
            "runtime_dependency_on_top10_decision": runtime_dependency,
        },
        "runtime_feature_contract": {
            "version": runtime_feature_contract.get("version"),
            "columns": runtime_feature_columns,
            "coverage": runtime_feature_coverage,
            "available_by_d_close": runtime_feature_contract.get(
                "available_by_d_close"
            ),
            "future_columns_used": runtime_feature_contract.get(
                "future_columns_used"
            ),
        },
        "thresholds": asdict(thresholds),
        "coverage": {
            "rows": int(len(ledger)),
            "signal_dates": signal_date_count,
            "codes": int(codes[codes.ne("")].nunique()),
            "start_signal_date": signal_dates[signal_dates.ne("")].min()
            if signal_date_count
            else None,
            "end_signal_date": signal_dates[signal_dates.ne("")].max()
            if signal_date_count
            else None,
            "d_price": d_price_coverage,
            "t_price": t_price_coverage,
            "tplus1_price": tplus1_price_coverage,
        },
        "labels": label_stats,
        "legacy_overlap": {
            "rows": overlap_rows,
            "history_sources": legacy_sources,
            "agreements": agreements,
        },
        "market_fill_diagnostic": {
            "semantic": "public_market_feasibility_proxy_not_actual_order_fill",
            "ledger_contract": manifest.get("target_contract", {}).get(
                "market_fill"
            )
            if isinstance(manifest.get("target_contract"), dict)
            else None,
            "comparable_rows": fill_comparable_rows,
            "conflict_rows": fill_conflict_rows,
            "conflict_rate": fill_conflict_rate,
            "conflict_is_hard_gate": False,
            "actual_order_fill_observed_positive_rows": actual_order_observed_positive,
            "actual_order_fill_labeled_rows": actual_order_labeled,
            "actual_order_claimed_by_this_report": False,
            "note": (
                "Proxy disagreement is reported only. It is not evidence of an "
                "actual submitted or filled order."
            ),
        },
        "hard_gates": gates,
        "failed_gates": failed_gates,
    }
    # Exercise the strict serializer here so callers cannot receive a report
    # that the CLI later fails to persist because of NaN or Infinity.
    _json_text(report)
    return report


def _failure_report(exc: Exception) -> dict[str, Any]:
    report = {
        "schema_version": REPORT_SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "FAIL",
        "valid": False,
        "error": {
            "type": type(exc).__name__,
            "message": str(exc),
        },
        "market_fill_diagnostic": {
            "semantic": "public_market_feasibility_proxy_not_actual_order_fill",
            "actual_order_claimed_by_this_report": False,
        },
        "failed_gates": ["input_contract"],
    }
    _json_text(report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate DC20's owned five-year three-engine supervised ledger"
    )
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--legacy-history", default=str(DEFAULT_LEGACY))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = validate_three_engine_five_year_ledger(
            args.ledger,
            args.manifest,
            args.legacy_history,
        )
    except (LedgerValidationError, OSError, ValueError) as exc:
        report = _failure_report(exc)
    _atomic_json(Path(args.output).resolve(), report)
    print(_json_text(report), end="")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
