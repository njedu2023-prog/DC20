#!/usr/bin/env python3
"""Build the independent historical OOF ledger for executable-profit Shadow.

The ledger is deliberately scoped to the frozen promotion OOF Top10.  It is a
research proxy: ``market_fill`` is public daily-bar buyability evidence, not an
observed order fill, and T+1 open is not sufficient to resolve a blocked
one-price limit-down exit.  No model is trained and no trade action is created
by this script.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for import_root in (ROOT, SRC):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from top10decision.decision.three_engine_models import (  # noqa: E402
    FORBIDDEN_FEATURE_COLUMNS,
    _canonical_sha256,
    resolve_d_close_feature_builder,
    top10_members_sha256,
)

from scripts.validate_decision_executable_profit_shadow_contract import (  # noqa: E402
    validate_contract,
)


LEDGER_SCHEMA = "dc20_executable_profit_historical_oof_top10_v1"
MANIFEST_SCHEMA = "dc20_executable_profit_historical_oof_top10_manifest_v1"
OUTPUT_STATUS = "HISTORICAL_LEDGER_READY_RESEARCH_PROXY"
BUYABILITY_LABEL_VERSION = "historical_daily_bar_not_one_price_limit_up_v1"
EXIT_PROXY_VERSION = "tplus1_daily_open_proxy_no_blocked_exit_resolution_v1"
FEATURE_SNAPSHOT_SCHEMA = "dc20_executable_profit_d_feature_row_v1"
DEFAULT_CONTRACT = Path("models/decision_executable_profit_shadow_contract.json")
DEFAULT_LEDGER = Path(
    "data/decision_executable_profit/historical_oof_top10_ledger.csv.gz"
)
DEFAULT_MANIFEST = Path(
    "data/decision_executable_profit/historical_oof_top10_ledger_manifest.json"
)
KEY_COLUMNS = ("signal_date", "ts_code")
AUDIT_COLUMNS = (
    "promotion_pool_size",
    "top10_members_sha256",
    "promotion_rank",
    "promotion_oof_fold",
    "promotion_oof_fold_kind",
    "promotion_oof_train_end",
    "promotion_oof_model_kind",
    "promotion_oof_calibration",
)
COMMON_TEXT_COLUMNS = ("buy_date", "target_exit_date", "board")
COMMON_NUMERIC_COLUMNS = (
    "stage",
    "promotion_hit",
    "market_fill",
    "big_loss_hit",
    "profit_hit",
    "net_return",
)
MIN_ROWS = 6000
MIN_SIGNAL_DATES = 850
MIN_MATURED_CONDITIONAL_ROWS = 5000
MIN_STAGE_MATURED_ROWS = 200
MIN_FEATURE_COVERAGE = 0.95


class ExecutableProfitLedgerError(RuntimeError):
    """Raised when the independent historical ledger cannot be built safely."""


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise ExecutableProfitLedgerError(message)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    _expect(isinstance(value, Mapping), f"{label} must be an object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normal_date(value: Any) -> str:
    if pd.isna(value):
        return ""
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _normal_code(value: Any) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip().upper()
    if "." in text:
        digits, suffix = text.split(".", 1)
        digits = "".join(character for character in digits if character.isdigit())[:6]
        if len(digits) == 6 and suffix in {"SH", "SZ"}:
            return f"{digits}.{suffix}"
    digits = "".join(character for character in text if character.isdigit())[:6]
    if len(digits) != 6:
        return ""
    return f"{digits}.SH" if digits.startswith("6") else f"{digits}.SZ"


def _json_text(payload: Any) -> str:
    return json.dumps(
        _json_safe(payload),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if value is pd.NA:
        return None
    return value


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _atomic_json(path: Path, payload: Any) -> None:
    _atomic_bytes(path, _json_text(payload).encode("utf-8"))


def _deterministic_gzip_csv(frame: pd.DataFrame) -> bytes:
    text = io.StringIO(newline="")
    frame.to_csv(
        text,
        index=False,
        lineterminator="\n",
        na_rep="",
        float_format="%.17g",
    )
    output = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", fileobj=output, compresslevel=9, mtime=0
    ) as compressed:
        compressed.write(text.getvalue().encode("utf-8"))
    return output.getvalue()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutableProfitLedgerError(f"cannot load {path}: {exc}") from exc
    _expect(isinstance(value, dict), f"{path} must contain an object")
    return value


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(
            path,
            dtype={
                "signal_date": "string",
                "buy_date": "string",
                "target_exit_date": "string",
                "ts_code": "string",
            },
            low_memory=False,
        )
    except (OSError, ValueError) as exc:
        raise ExecutableProfitLedgerError(f"cannot load {path}: {exc}") from exc


def _require_columns(frame: pd.DataFrame, required: Iterable[str], label: str) -> None:
    missing = sorted(set(required) - set(frame.columns))
    _expect(not missing, f"{label} missing required columns: {missing}")


def _normalise_identity(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    output = frame.copy()
    _require_columns(output, KEY_COLUMNS, label)
    output["signal_date"] = output["signal_date"].map(_normal_date)
    output["ts_code"] = output["ts_code"].map(_normal_code)
    _expect(output["signal_date"].str.fullmatch(r"\d{8}").all(), f"{label} has invalid dates")
    _expect(
        output["ts_code"].str.fullmatch(r"\d{6}\.(SH|SZ)").all(),
        f"{label} has invalid stock codes",
    )
    duplicates = int(output.duplicated(list(KEY_COLUMNS), keep=False).sum())
    _expect(duplicates == 0, f"{label} has {duplicates} duplicate key rows")
    for column in ("buy_date", "target_exit_date", "promotion_oof_train_end"):
        if column in output.columns:
            output[column] = output[column].map(_normal_date)
    if "board" in output.columns:
        output["board"] = output["board"].fillna("").astype(str).str.strip().str.upper()
    return output


def _validate_source_pins(
    contract: Mapping[str, Any],
    *,
    source_ledger_path: Path,
    oof_path: Path,
    calendar_path: Path,
) -> dict[str, str]:
    identity = _mapping(contract.get("promotion_identity"), "promotion_identity")
    source = _mapping(identity.get("source_ledger"), "promotion source ledger")
    oof = _mapping(identity.get("oof_top10"), "promotion OOF Top10")
    calendar = _mapping(identity.get("calendar"), "promotion calendar")
    actual = {
        "source_ledger_sha256": _sha256(source_ledger_path),
        "oof_top10_sha256": _sha256(oof_path),
        "calendar_sha256": _sha256(calendar_path),
    }
    _expect(
        actual["source_ledger_sha256"] == source.get("sha256"),
        "source five-year ledger SHA256 drifted",
    )
    _expect(
        actual["oof_top10_sha256"] == oof.get("sha256"),
        "promotion OOF Top10 SHA256 drifted",
    )
    _expect(
        actual["calendar_sha256"] == calendar.get("sha256"),
        "strict SSE calendar SHA256 drifted",
    )
    return actual


def _read_open_dates(calendar_path: Path) -> list[str]:
    calendar = pd.read_csv(calendar_path, encoding="utf-8-sig", dtype=str)
    _require_columns(calendar, ("exchange", "cal_date", "is_open"), "SSE calendar")
    calendar["cal_date"] = calendar["cal_date"].map(_normal_date)
    exchange = calendar["exchange"].fillna("").astype(str).str.upper()
    opened = calendar.loc[exchange.eq("SSE") & calendar["is_open"].eq("1"), "cal_date"]
    dates = sorted(set(opened))
    _expect(bool(dates), "strict SSE calendar has no open sessions")
    return dates


def _validate_calendar_binding(frame: pd.DataFrame, open_dates: list[str]) -> None:
    position = {date: index for index, date in enumerate(open_dates)}
    violations: list[str] = []
    for row in frame[["signal_date", "buy_date", "target_exit_date"]].drop_duplicates().itertuples(index=False):
        index = position.get(str(row.signal_date))
        if (
            index is None
            or index + 2 >= len(open_dates)
            or open_dates[index + 1] != str(row.buy_date)
            or open_dates[index + 2] != str(row.target_exit_date)
        ):
            violations.append(
                f"{row.signal_date}:{row.buy_date}:{row.target_exit_date}"
            )
    _expect(
        not violations,
        "strict SSE D/T/T+1 adjacency violations: " + ",".join(violations[:20]),
    )


def _validate_oof_top10(oof: pd.DataFrame) -> None:
    _require_columns(
        oof,
        (
            *KEY_COLUMNS,
            *COMMON_TEXT_COLUMNS,
            *COMMON_NUMERIC_COLUMNS,
            *AUDIT_COLUMNS,
            "top10_selected",
        ),
        "promotion OOF Top10",
    )
    selected = pd.to_numeric(oof["top10_selected"], errors="coerce")
    _expect(selected.eq(1).all(), "promotion OOF contains a non-Top10 row")
    failures: list[str] = []
    for signal_date, group in oof.groupby("signal_date", sort=True):
        pool_values = pd.to_numeric(group["promotion_pool_size"], errors="coerce")
        ranks = sorted(
            pd.to_numeric(group["promotion_rank"], errors="coerce")
            .dropna()
            .astype(int)
            .tolist()
        )
        pool_claims = set(pool_values.dropna().astype(int))
        expected_count = min(10, next(iter(pool_claims))) if len(pool_claims) == 1 else -1
        member_hash = top10_members_sha256(signal_date, group["ts_code"].astype(str))
        hash_claims = set(group["top10_members_sha256"].dropna().astype(str))
        if (
            len(group) != expected_count
            or ranks != list(range(1, expected_count + 1))
            or hash_claims != {member_hash}
        ):
            failures.append(str(signal_date))
    _expect(not failures, f"promotion OOF Top10 integrity failed on {failures[:20]}")
    train_end = oof["promotion_oof_train_end"].map(_normal_date)
    _expect(
        train_end.str.fullmatch(r"\d{8}").all()
        and train_end.lt(oof["signal_date"]).all(),
        "promotion OOF fold train_end is not strictly before signal_date",
    )


def _validate_common_truth(oof: pd.DataFrame, source: pd.DataFrame) -> None:
    right = source[[*KEY_COLUMNS, *COMMON_TEXT_COLUMNS, *COMMON_NUMERIC_COLUMNS]]
    joined = oof[[*KEY_COLUMNS, *COMMON_TEXT_COLUMNS, *COMMON_NUMERIC_COLUMNS]].merge(
        right,
        on=list(KEY_COLUMNS),
        how="left",
        suffixes=("_oof", "_source"),
        validate="one_to_one",
        indicator=True,
    )
    _expect(joined["_merge"].eq("both").all(), "OOF keys are missing from source ledger")
    for column in COMMON_TEXT_COLUMNS:
        left = joined[f"{column}_oof"].fillna("").astype(str)
        right_values = joined[f"{column}_source"].fillna("").astype(str)
        _expect(left.eq(right_values).all(), f"OOF/source {column} truth drifted")
    for column in COMMON_NUMERIC_COLUMNS:
        left = pd.to_numeric(joined[f"{column}_oof"], errors="coerce")
        right_values = pd.to_numeric(joined[f"{column}_source"], errors="coerce")
        both_missing = left.isna() & right_values.isna()
        equal = np.isclose(left, right_values, rtol=0.0, atol=1e-12, equal_nan=True)
        _expect(bool(np.asarray(equal | both_missing).all()), f"OOF/source {column} truth drifted")


def _safe_feature_value(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    return value


def _feature_snapshot_hash(row: pd.Series, feature_columns: list[str]) -> str:
    return _canonical_sha256(
        {
            "schema": FEATURE_SNAPSHOT_SCHEMA,
            "signal_date": str(row["signal_date"]),
            "ts_code": str(row["ts_code"]),
            "features": {
                column: _safe_feature_value(row[column]) for column in feature_columns
            },
        }
    )


def build_historical_frame(
    *,
    source: pd.DataFrame,
    oof: pd.DataFrame,
    open_dates: list[str],
    contract: Mapping[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    source = _normalise_identity(source, "source five-year ledger")
    oof = _normalise_identity(oof, "promotion OOF Top10")
    _validate_oof_top10(oof)
    _validate_common_truth(oof, source)

    identity = _mapping(contract.get("promotion_identity"), "promotion_identity")
    expected_oof = _mapping(identity.get("oof_top10"), "promotion OOF identity")
    _expect(len(oof) == int(expected_oof.get("rows", -1)), "promotion OOF row count drifted")
    _expect(
        oof["signal_date"].nunique() == int(expected_oof.get("dates", -1)),
        "promotion OOF date count drifted",
    )

    source_columns = [
        *KEY_COLUMNS,
        "buy_date",
        "target_exit_date",
        "stage",
        "board",
        "fill_reason",
        "market_fill",
        "t_open",
        "tplus1_open",
        "gross_return",
        "net_return",
        "profit_hit",
        "big_loss_hit",
    ]
    _require_columns(source, source_columns, "source five-year ledger")
    joined = oof[[*KEY_COLUMNS, *AUDIT_COLUMNS]].merge(
        source,
        on=list(KEY_COLUMNS),
        how="left",
        validate="one_to_one",
        indicator=True,
    )
    _expect(joined["_merge"].eq("both").all(), "OOF/source join is not complete")
    joined = joined.drop(columns="_merge")
    _validate_calendar_binding(joined, open_dates)

    feature_builder = resolve_d_close_feature_builder(source)
    feature_columns = list(feature_builder.feature_names)
    raw_feature_columns = list(feature_builder.numeric_columns)
    expected_feature_sha = _mapping(identity.get("features"), "promotion features").get(
        "feature_columns_sha256"
    )
    actual_feature_sha = _canonical_sha256(feature_columns)
    _expect(actual_feature_sha == expected_feature_sha, "frozen D feature identity drifted")
    _expect(
        not set(feature_columns).intersection(FORBIDDEN_FEATURE_COLUMNS),
        "forbidden future/target feature entered executable-profit ledger",
    )
    features = feature_builder.transform(joined)
    feature_coverage = {
        column: float(pd.to_numeric(features[column], errors="coerce").notna().mean())
        for column in feature_columns
    }
    _expect(
        min(feature_coverage.values()) >= MIN_FEATURE_COVERAGE,
        "one or more frozen D features fall below the 95% coverage gate",
    )

    fill = pd.to_numeric(joined["market_fill"], errors="coerce")
    entry = pd.to_numeric(joined["t_open"], errors="coerce")
    exit_price = pd.to_numeric(joined["tplus1_open"], errors="coerce")
    gross = pd.to_numeric(joined["gross_return"], errors="coerce")
    net = pd.to_numeric(joined["net_return"], errors="coerce")
    source_profit = pd.to_numeric(joined["profit_hit"], errors="coerce")
    source_big_loss = pd.to_numeric(joined["big_loss_hit"], errors="coerce")
    invalid_fill = fill.notna() & ~fill.isin((0.0, 1.0))
    _expect(not invalid_fill.any(), "market_fill proxy contains non-binary values")
    matured = fill.eq(1) & net.notna() & gross.notna()
    not_buyable = fill.eq(0)
    profit = net.gt(0).astype(float).where(matured)
    big_loss = net.le(-0.03).astype(float).where(matured)
    _expect(
        np.isclose(source_profit[matured], profit[matured], rtol=0.0, atol=0.0).all(),
        "conditional profit labels conflict with net return",
    )
    _expect(
        np.isclose(source_big_loss[matured], big_loss[matured], rtol=0.0, atol=0.0).all(),
        "conditional big-loss labels conflict with net return",
    )
    _expect(
        np.isclose((gross[matured] - net[matured]), 0.0045, rtol=0.0, atol=5e-11).all(),
        "45bp cost formula conflicts with source truth",
    )
    _expect(
        entry[matured].notna().all()
        and exit_price[matured].notna().all()
        and entry[matured].gt(0.0).all()
        and exit_price[matured].gt(0.0).all(),
        "matured proxy returns require positive T/T+1 open prices",
    )
    _expect(
        np.isclose(
            gross[matured],
            exit_price[matured] / entry[matured] - 1.0,
            rtol=0.0,
            atol=5e-11,
        ).all(),
        "gross return conflicts with T/T+1 daily-open proxies",
    )
    _expect(
        net[not_buyable].isna().all()
        and source_profit[not_buyable].isna().all()
        and source_big_loss[not_buyable].isna().all(),
        "non-buyable rows must not carry conditional return labels",
    )

    output = pd.DataFrame(index=joined.index)
    output["signal_date"] = joined["signal_date"]
    output["exec_date"] = joined["buy_date"]
    output["scheduled_exit_date"] = joined["target_exit_date"]
    output["ts_code"] = joined["ts_code"]
    output["stage"] = pd.to_numeric(joined["stage"], errors="raise").astype(int)
    output["stage_transition"] = output["stage"].map({2: "2_to_3", 3: "3_to_4"})
    _expect(output["stage_transition"].notna().all(), "ledger escaped hard stage 2/3 scope")
    output["board"] = joined["board"]
    output["promotion_rank"] = pd.to_numeric(
        joined["promotion_rank"], errors="raise"
    ).astype(int)
    output["promotion_pool_size"] = pd.to_numeric(
        joined["promotion_pool_size"], errors="raise"
    ).astype(int)
    output["top10_members_sha256"] = joined["top10_members_sha256"].astype(str)
    output["promotion_oof_fold"] = pd.to_numeric(
        joined["promotion_oof_fold"], errors="raise"
    ).astype(int)
    for column in (
        "promotion_oof_fold_kind",
        "promotion_oof_train_end",
        "promotion_oof_model_kind",
        "promotion_oof_calibration",
    ):
        output[column] = joined[column].fillna("").astype(str)
    output["contract_id"] = str(contract.get("contract_id"))
    output["public_market_buyable_proxy"] = fill
    output["public_market_buyable_reason"] = joined["fill_reason"].fillna("").astype(str)
    output["buyability_label_version"] = BUYABILITY_LABEL_VERSION
    output["conditional_entry_price_proxy"] = entry.where(fill.eq(1))
    output["conditional_exit_price_proxy"] = exit_price.where(matured)
    output["conditional_gross_return"] = gross.where(matured)
    output["conditional_net_return_after_cost"] = net.where(matured)
    output["conditional_profit_hit"] = profit
    output["conditional_big_loss_hit"] = big_loss
    buckets = pd.Series(pd.NA, index=joined.index, dtype="string")
    buckets.loc[matured & net.le(-0.03)] = "BIG_LOSS"
    buckets.loc[matured & net.gt(-0.03) & net.le(0.0)] = "NON_PROFIT"
    buckets.loc[matured & net.gt(0.0)] = "PROFIT"
    output["conditional_return_bucket"] = buckets
    executable_hit = pd.Series(np.nan, index=joined.index, dtype=float)
    executable_hit.loc[not_buyable] = 0.0
    executable_hit.loc[matured] = profit.loc[matured]
    output["executable_profit_proxy_hit"] = executable_hit
    strategy_return = pd.Series(np.nan, index=joined.index, dtype=float)
    strategy_return.loc[not_buyable] = 0.0
    strategy_return.loc[matured] = net.loc[matured]
    output["strategy_slot_net_return"] = strategy_return
    stress_return = pd.Series(np.nan, index=joined.index, dtype=float)
    stress_return.loc[not_buyable] = 0.0
    stress_return.loc[matured] = gross.loc[matured] - 0.009
    output["strategy_slot_net_return_2x_cost"] = stress_return
    status = pd.Series("PENDING_MISSING_ENTRY_TRUTH", index=joined.index, dtype="string")
    status.loc[not_buyable] = "MATURED_NOT_BUYABLE_PROXY"
    status.loc[fill.eq(1) & ~matured] = "PENDING_MISSING_OR_DISCONTINUOUS_EXIT_TRUTH"
    status.loc[matured] = "MATURED_TPLUS1_DAILY_OPEN_PROXY"
    output["outcome_status"] = status
    output["cost_rate"] = 0.0045
    output["stress_cost_rate"] = 0.009
    output["exit_proxy_version"] = EXIT_PROXY_VERSION
    output["blocked_limit_down_exit_truth_available"] = 0
    output["actual_order_fill_observed"] = 0
    output["actual_execution_claimed"] = 0
    for column in feature_columns:
        output[column] = features[column]
    output["feature_snapshot_sha256"] = output.apply(
        _feature_snapshot_hash, axis=1, feature_columns=feature_columns
    )

    output = output.sort_values(
        ["signal_date", "promotion_rank", "ts_code"], kind="stable"
    ).reset_index(drop=True)

    stage_matured = {
        str(stage): int(
            (
                output["conditional_net_return_after_cost"].notna()
                & output["stage"].eq(stage)
            ).sum()
        )
        for stage in (2, 3)
    }
    gates = {
        "minimum_rows": {
            "passed": len(output) >= MIN_ROWS,
            "actual": int(len(output)),
            "requirement": f">={MIN_ROWS}",
        },
        "minimum_signal_dates": {
            "passed": output["signal_date"].nunique() >= MIN_SIGNAL_DATES,
            "actual": int(output["signal_date"].nunique()),
            "requirement": f">={MIN_SIGNAL_DATES}",
        },
        "minimum_matured_conditional_rows": {
            "passed": int(matured.sum()) >= MIN_MATURED_CONDITIONAL_ROWS,
            "actual": int(matured.sum()),
            "requirement": f">={MIN_MATURED_CONDITIONAL_ROWS}",
        },
        "stage_matured_support": {
            "passed": min(stage_matured.values()) >= MIN_STAGE_MATURED_ROWS,
            "actual": stage_matured,
            "requirement": f"each stage >={MIN_STAGE_MATURED_ROWS}",
        },
        "feature_minimum_coverage": {
            "passed": min(feature_coverage.values()) >= MIN_FEATURE_COVERAGE,
            "actual": min(feature_coverage.values()),
            "requirement": f">={MIN_FEATURE_COVERAGE}",
        },
        "promotion_rank_not_a_model_feature": {
            "passed": "promotion_rank" not in feature_columns,
            "actual": "promotion_rank" in feature_columns,
            "requirement": "false",
        },
        "promotion_probability_not_present": {
            "passed": "predicted_promotion_probability" not in output.columns,
            "actual": "predicted_promotion_probability" in output.columns,
            "requirement": "false",
        },
        "actual_execution_claims_false": {
            "passed": bool(
                output["actual_order_fill_observed"].eq(0).all()
                and output["actual_execution_claimed"].eq(0).all()
            ),
            "actual": False,
            "requirement": "false",
        },
        "blocked_exit_truth_explicitly_unavailable": {
            "passed": output["blocked_limit_down_exit_truth_available"].eq(0).all(),
            "actual": False,
            "requirement": "false and research-only",
        },
    }
    _expect(all(gate["passed"] for gate in gates.values()), "historical ledger quality gate failed")
    audit = {
        "feature_columns": feature_columns,
        "raw_feature_columns": raw_feature_columns,
        "feature_columns_sha256": actual_feature_sha,
        "feature_nonnull_fraction": feature_coverage,
        "gates": gates,
        "coverage": {
            "rows": int(len(output)),
            "signal_dates": int(output["signal_date"].nunique()),
            "signal_start": str(output["signal_date"].min()),
            "signal_end": str(output["signal_date"].max()),
            "dates_with_fewer_than_two_candidates": int(
                output.groupby("signal_date").size().lt(2).sum()
            ),
            "buyability_known_rows": int(fill.notna().sum()),
            "buyable_proxy_rows": int(fill.eq(1).sum()),
            "not_buyable_proxy_rows": int(fill.eq(0).sum()),
            "buyability_pending_rows": int(fill.isna().sum()),
            "matured_conditional_return_rows": int(matured.sum()),
            "pending_conditional_return_rows": int((fill.eq(1) & ~matured).sum()),
            "strategy_evaluable_rows": int(output["strategy_slot_net_return"].notna().sum()),
            "conditional_profit_rows": int(profit.eq(1).sum()),
            "conditional_big_loss_rows": int(big_loss.eq(1).sum()),
            "stage_matured_rows": stage_matured,
        },
    }
    return output, audit


def build_ledger(
    *,
    repo_root: Path,
    contract_path: Path,
    source_ledger_path: Path,
    oof_path: Path,
    calendar_path: Path,
    output_ledger_path: Path,
    output_manifest_path: Path,
    validate_frozen_contract: bool = True,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    contract_absolute = (
        contract_path if contract_path.is_absolute() else repo_root / contract_path
    )
    contract = _load_json(contract_absolute)
    if validate_frozen_contract:
        try:
            relative_contract = contract_absolute.relative_to(repo_root)
        except ValueError as exc:
            raise ExecutableProfitLedgerError(
                "validated contract must be inside repo_root"
            ) from exc
        result = validate_contract(repo_root, relative_contract)
        _expect(result.get("valid") is True, "frozen design contract validation failed")
    pins = _validate_source_pins(
        contract,
        source_ledger_path=source_ledger_path,
        oof_path=oof_path,
        calendar_path=calendar_path,
    )
    output, audit = build_historical_frame(
        source=_read_csv(source_ledger_path),
        oof=_read_csv(oof_path),
        open_dates=_read_open_dates(calendar_path),
        contract=contract,
    )
    payload = _deterministic_gzip_csv(output)
    _atomic_bytes(output_ledger_path, payload)
    output_sha = hashlib.sha256(payload).hexdigest()
    identity = _mapping(contract.get("promotion_identity"), "promotion_identity")
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "ledger_schema_version": LEDGER_SCHEMA,
        "status": OUTPUT_STATUS,
        "owner": "njedu2023-prog/DC20",
        "repository": "njedu2023-prog/DC20",
        "branch": "main",
        "runtime_dependency_on_top10_decision": False,
        "contract": {
            "path": DEFAULT_CONTRACT.as_posix(),
            "id": contract.get("contract_id"),
            "sha256": _sha256(contract_absolute),
            "promotion_contract_sha256": contract.get("promotion_contract_sha256"),
        },
        "inputs": {
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
        },
        "output": {
            "path": DEFAULT_LEDGER.as_posix(),
            "sha256": output_sha,
            "bytes": len(payload),
            "rows": int(len(output)),
            "signal_dates": int(output["signal_date"].nunique()),
            "columns": list(output.columns),
            "compression": "gzip_mtime_0",
            "sort": ["signal_date", "promotion_rank", "ts_code"],
        },
        "candidate_contract": {
            "scope": "exact frozen promotion OOF Top10 only",
            "hard_stage_scope": ["2_to_3", "3_to_4"],
            "promotion_rank_role": "audit and same-date baseline only; never a model feature",
            "old_profit_big_loss_pfill_predictions_copied": False,
        },
        "feature_contract": {
            "known_at": "D close",
            "columns": audit["feature_columns"],
            "columns_sha256": audit["feature_columns_sha256"],
            "raw_columns": audit["raw_feature_columns"],
            "promotion_rank_or_probability_used": False,
            "future_or_cross_head_outputs_used": False,
            "feature_snapshot_schema": FEATURE_SNAPSHOT_SCHEMA,
            "nonnull_fraction": audit["feature_nonnull_fraction"],
        },
        "label_contract": {
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
        },
        "coverage": audit["coverage"],
        "quality_gates": audit["gates"],
        "release": {
            "historical_ledger_ready": True,
            "model_trained": False,
            "front_end_shadow_rank_allowed": False,
            "official_trade_action_allowed": False,
            "reason": (
                "Research-proxy ledger is ready; model, independent time validation, "
                "forward Shadow evidence, and blocked-exit truth remain pending."
            ),
        },
    }
    _atomic_json(output_manifest_path, manifest)
    return manifest


def _resolve_input(repo_root: Path, path: Path | None, contract_path: str) -> Path:
    if path is not None:
        return path.resolve()
    return (repo_root / contract_path).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build DC20's independent executable-profit historical OOF ledger."
    )
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--source-ledger", type=Path)
    parser.add_argument("--oof-top10", type=Path)
    parser.add_argument("--calendar", type=Path)
    parser.add_argument("--output-ledger", type=Path)
    parser.add_argument("--output-manifest", type=Path)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    contract_path = args.contract if args.contract.is_absolute() else root / args.contract
    try:
        contract = _load_json(contract_path)
        identity = _mapping(contract.get("promotion_identity"), "promotion_identity")
        manifest = build_ledger(
            repo_root=root,
            contract_path=args.contract,
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
            output_ledger_path=(
                args.output_ledger.resolve()
                if args.output_ledger
                else (root / DEFAULT_LEDGER).resolve()
            ),
            output_manifest_path=(
                args.output_manifest.resolve()
                if args.output_manifest
                else (root / DEFAULT_MANIFEST).resolve()
            ),
        )
    except (ExecutableProfitLedgerError, OSError, ValueError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(
        json.dumps(
            {
                "valid": True,
                "status": manifest["status"],
                "ledger_sha256": manifest["output"]["sha256"],
                "rows": manifest["output"]["rows"],
                "signal_dates": manifest["output"]["signal_dates"],
                "model_trained": False,
                "official_trade_action_allowed": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
