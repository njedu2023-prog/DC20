from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .eligibility import annotate_standard_limit_universe, filter_standard_limit_universe
from .canonical_fingerprint import (
    CANONICAL_FINGERPRINT_SCHEMA,
    CanonicalSchemaError,
    canonical_execution_projection,
    canonical_json_bytes,
    canonical_mapping_sha256,
    canonical_policy_fingerprint,
    compose_artifact_fingerprint,
)
from .observation import (
    OBSERVATION_START_EXEC_DATE,
    OBSERVATION_TOP_N,
    rank_observation_rows,
)


REPORT_RE = re.compile(r"decision_report_(20\d{6})\.md$")
MODEL_V2_POLICY_THRESHOLDS = (
    "max_big_loss_probability",
    "min_mean_return_lcb",
    "min_fill_probability",
    "min_exit_probability",
    "min_conservative_ev",
    "min_selection_score",
)
TRADE_SELECTOR_V2_POLICY_THRESHOLDS = (
    "min_trade_score",
    "min_mean_return_lcb",
    "min_fill_probability",
    "max_big_loss_probability",
)
SELECTOR_DOMAIN_SCORE_COLUMNS = (
    "promotion_rank_score",
    "predicted_promotion_probability",
    "trade_score",
    "trade_predicted_conditional_net_return",
    "trade_predicted_mean_return_lcb",
    "trade_predicted_fill_probability",
    "trade_predicted_public_market_buyable_probability",
    "trade_predicted_big_loss_probability",
    "trade_predicted_outcome_q10",
    "trade_tail_loss_proxy",
    "trade_base_score",
    "trade_tail_risk_weight",
)
SELECTOR_DOMAIN_RANK_COLUMNS = (
    "promotion_rank",
    "trade_rank",
)
SELECTOR_DOMAIN_BINARY_COLUMNS = (
    "trade_gate_pass",
    "trade_shadow_selected",
    "trade_selected",
    "trade_selector_policy_ready",
)
SELECTOR_GLOBAL_BINARY_COLUMNS = ("trade_selector_promoted",)
SELECTOR_ARTIFACT_COLUMNS = (
    "trade_selector_artifact_sha256",
    "trade_selector_artifact_v2_sha256",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _date(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    return round(number, 10) if math.isfinite(number) else None


def _integer(value: Any, default: int = 0) -> int:
    number = _number(value)
    return int(number) if number is not None else default


def _text(value: Any) -> str:
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return pd.read_csv(path, encoding=encoding, low_memory=False)
        except Exception:
            continue
    return pd.DataFrame()


def _unique_nonempty_column_value(frame: pd.DataFrame, name: str) -> str:
    if frame.empty or name not in frame.columns:
        return ""
    values = {
        value
        for value in (_text(item) for item in frame[name].tolist())
        if value
    }
    return next(iter(values)) if len(values) == 1 else ""


def _strict_unique_text_column_value(
    frame: pd.DataFrame,
    name: str,
) -> tuple[str, bool]:
    """Return one value only when every prediction row carries it.

    V2 canonical provenance is a hard promotion gate.  Unlike the legacy
    audit helper above, a blank row is therefore not ignored: missing, blank,
    or mixed values all fail closed.  Execution values remain raw float64.
    """

    if frame.empty or name not in frame.columns:
        return "", False
    values = [_text(item) for item in frame[name].tolist()]
    if not values or any(not value for value in values):
        return "", False
    unique = set(values)
    return (values[0], True) if len(unique) == 1 else ("", False)


def _strict_unique_exact_text_column_value(
    frame: pd.DataFrame,
    name: str,
) -> tuple[str, bool]:
    """Return one non-empty string without normalizing its bytes."""

    if frame.empty or name not in frame.columns:
        return "", False
    values = frame[name].tolist()
    if not values or any(
        not isinstance(value, str) or value == ""
        for value in values
    ):
        return "", False
    unique = set(values)
    return (values[0], True) if len(unique) == 1 else ("", False)


def _strict_unique_prediction_date(frame: pd.DataFrame, name: str) -> str:
    if frame.empty:
        return ""
    if name not in frame.columns:
        raise RuntimeError(f"prediction missing required date column: {name}")
    values = [_date(value) for value in frame[name].tolist()]
    if not values or any(
        not re.fullmatch(r"20\d{6}", value)
        for value in values
    ):
        raise RuntimeError(f"prediction has an invalid or empty {name}")
    for value in values:
        try:
            datetime.strptime(value, "%Y%m%d")
        except ValueError as exc:
            raise RuntimeError(f"prediction has an invalid {name}") from exc
    if len(set(values)) != 1:
        raise RuntimeError(f"prediction mixes multiple {name} values")
    return values[0]


def _exact_nonempty_text(value: Any) -> str:
    return value if isinstance(value, str) and value != "" else ""


def _strict_real_number(value: Any) -> float | None:
    if isinstance(value, (bool, np.bool_)) or not isinstance(
        value,
        (int, float, np.integer, np.floating),
    ):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _canonical_decimals(value: Any) -> int | None:
    number = _strict_real_number(value)
    if number is None or not number.is_integer():
        return None
    decimals = int(number)
    return decimals if 0 <= decimals <= 18 else None


def _strict_unique_canonical_decimals_column_value(
    frame: pd.DataFrame,
    name: str,
) -> tuple[int | None, bool]:
    if frame.empty or name not in frame.columns:
        return None, False
    values = [_canonical_decimals(item) for item in frame[name].tolist()]
    if not values or any(value is None for value in values):
        return None, False
    unique = set(values)
    return (values[0], True) if len(unique) == 1 else (None, False)


def _strict_all_true_column(frame: pd.DataFrame, name: str) -> bool:
    if frame.empty or name not in frame.columns:
        return False
    values = frame[name].tolist()
    if not values:
        return False
    for value in values:
        if isinstance(value, (bool, np.bool_)):
            parsed = bool(value)
        else:
            number = _strict_real_number(value)
            parsed = number == 1.0 if number is not None else False
        if not parsed:
            return False
    return True


def _missing_cell(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip() == ""
    try:
        return bool(pd.isna(value))
    except Exception:
        return value is None


def _selector_prediction_domain(
    prediction: pd.DataFrame,
) -> dict[str, Any]:
    """Validate the exact selector execution domain in a prediction ledger.

    Canonical declarations describe the selector contract on every row.  The
    fitted selector artifact and executable selector scores, however, only
    exist for rows admitted to the observation Top10.  Blanks outside that
    domain are therefore required evidence, not values to silently drop.
    """

    failures: list[str] = []
    if prediction.empty or "observation_selected" not in prediction.columns:
        return {
            "frame": prediction.iloc[0:0].copy(),
            "outside": prediction.copy(),
            "valid": False,
            "failures": ["observation_selected"],
            "rows": 0,
            "outside_rows": int(len(prediction)),
        }

    parsed_domain = [
        _canonical_decimals(value)
        for value in prediction["observation_selected"].tolist()
    ]
    if any(value not in {0, 1} for value in parsed_domain):
        failures.append("observation_selected")
        domain_mask = pd.Series(False, index=prediction.index)
        outside_mask = ~domain_mask
    else:
        domain_mask = pd.Series(parsed_domain, index=prediction.index).eq(1)
        outside_mask = ~domain_mask
    domain = prediction.loc[domain_mask].copy()
    outside = prediction.loc[outside_mask].copy()
    if domain.empty:
        failures.append("empty_domain")

    required_columns = {
        *SELECTOR_DOMAIN_SCORE_COLUMNS,
        *SELECTOR_DOMAIN_RANK_COLUMNS,
        *SELECTOR_DOMAIN_BINARY_COLUMNS,
        *SELECTOR_GLOBAL_BINARY_COLUMNS,
        *SELECTOR_ARTIFACT_COLUMNS,
        "trade_model_reason",
    }
    missing_columns = sorted(required_columns.difference(prediction.columns))
    if missing_columns:
        failures.append("missing_execution_columns")
    else:
        for name in SELECTOR_DOMAIN_SCORE_COLUMNS:
            values = pd.to_numeric(domain[name], errors="coerce")
            if len(values) != len(domain) or not np.isfinite(values).all():
                failures.append(f"domain_finite:{name}")
        for name in SELECTOR_DOMAIN_RANK_COLUMNS:
            values = pd.to_numeric(domain[name], errors="coerce")
            valid = (
                len(values) == len(domain)
                and np.isfinite(values).all()
                and values.gt(0).all()
                and values.mod(1).eq(0).all()
                and values.nunique(dropna=False) == len(values)
            )
            if not valid:
                failures.append(f"domain_rank:{name}")
        for name in SELECTOR_DOMAIN_BINARY_COLUMNS:
            values = [_canonical_decimals(value) for value in domain[name]]
            if any(value not in {0, 1} for value in values):
                failures.append(f"domain_binary:{name}")
        for name in SELECTOR_GLOBAL_BINARY_COLUMNS:
            values = [
                _canonical_decimals(value)
                for value in prediction[name]
            ]
            if (
                any(value not in {0, 1} for value in values)
                or len(set(values)) != 1
            ):
                failures.append(f"global_binary:{name}")
        reasons = domain["trade_model_reason"].tolist()
        if any(
            not isinstance(value, str) or value == ""
            for value in reasons
        ):
            failures.append("domain_trade_model_reason")
        for name in SELECTOR_ARTIFACT_COLUMNS:
            value, complete = _strict_unique_exact_text_column_value(
                domain,
                name,
            )
            if not complete or not _is_sha256(value):
                failures.append(f"domain_artifact:{name}")

        outside_missing_columns = (
            *SELECTOR_DOMAIN_SCORE_COLUMNS,
            *SELECTOR_DOMAIN_RANK_COLUMNS,
            *SELECTOR_ARTIFACT_COLUMNS,
        )
        for name in outside_missing_columns:
            if not all(_missing_cell(value) for value in outside[name]):
                failures.append(f"outside_missing:{name}")
        for name in SELECTOR_DOMAIN_BINARY_COLUMNS:
            values = [_canonical_decimals(value) for value in outside[name]]
            if any(value != 0 for value in values):
                failures.append(f"outside_zero:{name}")
        if any(
            not isinstance(value, str)
            or value != "outside_observation_top10"
            for value in outside["trade_model_reason"].tolist()
        ):
            failures.append("outside_trade_model_reason")

    return {
        "frame": domain,
        "outside": outside,
        "valid": not failures,
        "failures": failures,
        "rows": int(len(domain)),
        "outside_rows": int(len(outside)),
    }


def _valid_canonical_contract(value: Any, *, layer: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if set(value) != {
        "schema",
        "layer",
        "decimals",
        "rounding",
        "execution_mode",
        "raw_execution_preserved",
    }:
        return {}
    schema = value.get("schema")
    contract_layer = value.get("layer")
    decimals = _canonical_decimals(value.get("decimals"))
    rounding = value.get("rounding")
    execution_mode = value.get("execution_mode")
    raw_execution_preserved = value.get("raw_execution_preserved") is True
    if (
        schema != "dc20_canonical_fingerprint_v2"
        or contract_layer != layer
        or decimals != 8
        or rounding != "decimal_string_half_even"
        or execution_mode != "raw_float64"
        or not raw_execution_preserved
    ):
        return {}
    return {
        "schema": schema,
        "layer": contract_layer,
        "decimals": decimals,
        "rounding": rounding,
        "execution_mode": execution_mode,
        "raw_execution_preserved": True,
    }


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{64}", value) is not None
    )


def _finite_number(value: Any) -> bool:
    return _strict_real_number(value) is not None


def _valid_policy_projection(value: Any, *, layer: str) -> bool:
    if not isinstance(value, dict):
        return False
    common_keys = {
        "version",
        "ready",
        "reason",
        "max_positions",
        "thresholds",
    }
    expected_keys = (
        common_keys | {"tail_risk_weight"}
        if layer == "trade_selector"
        else common_keys
    )
    if set(value) != expected_keys:
        return False
    version = value.get("version")
    reason = value.get("reason")
    if (
        not isinstance(version, str)
        or version == ""
        or not isinstance(reason, str)
        or reason == ""
    ):
        return False
    if type(value.get("ready")) is not bool:
        return False
    max_positions = value.get("max_positions")
    if type(max_positions) is not int:
        return False
    max_positions_number = _strict_real_number(max_positions)
    if max_positions_number is None:
        return False
    if max_positions < (1 if layer == "trade_selector" else 0):
        return False
    if layer == "trade_selector" and (
        type(value.get("tail_risk_weight")) is not float
        or not math.isfinite(value["tail_risk_weight"])
    ):
        return False
    thresholds = value.get("thresholds")
    if not isinstance(thresholds, dict):
        return False
    expected_thresholds = set(
        TRADE_SELECTOR_V2_POLICY_THRESHOLDS
        if layer == "trade_selector"
        else MODEL_V2_POLICY_THRESHOLDS
    )
    if set(thresholds) != expected_thresholds:
        return False
    if not all(
        type(thresholds[name]) is float and math.isfinite(thresholds[name])
        for name in expected_thresholds
    ):
        return False
    # The V2 envelope carries the executable policy's q8 projection, never a
    # raw-float alias that merely hashes to the same q8 value.  Keep this
    # check type-sensitive (for example, ``True`` is not interchangeable with
    # ``1``) so synchronized metadata tampering still fails closed.
    try:
        canonical = canonical_execution_projection(value, decimals=8)
        return canonical_json_bytes(canonical) == canonical_json_bytes(value)
    except (CanonicalSchemaError, TypeError, ValueError):
        return False


def _valid_v2_fingerprint(
    value: Any,
    *,
    layer: str,
    canonical_version: str,
    artifact_sha256: str,
    canonical_contract: dict[str, Any],
) -> bool:
    if not isinstance(value, dict):
        return False
    if set(value) != {
        "schema",
        "canonical_version",
        "canonical_contract",
        "provenance_sha256",
        "semantic_sha256",
        "policy_sha256",
        "policy_projection",
        "artifact_sha256",
        "schema_valid",
        "missing_columns",
        "invalid_cell_count",
    }:
        return False
    if value.get("schema_valid") is not True:
        return False
    if value.get("missing_columns") != []:
        return False
    invalid_cell_count = value.get("invalid_cell_count")
    if (
        isinstance(invalid_cell_count, (bool, np.bool_))
        or _canonical_decimals(invalid_cell_count) != 0
    ):
        return False
    if value.get("schema") != "dc20_canonical_fingerprint_v2":
        return False
    if value.get("canonical_version") != canonical_version:
        return False
    if value.get("artifact_sha256") != artifact_sha256:
        return False
    if _valid_canonical_contract(
        value.get("canonical_contract"),
        layer=layer,
    ) != canonical_contract:
        return False
    policy_projection = value.get("policy_projection")
    if not _valid_policy_projection(policy_projection, layer=layer):
        return False
    provenance_sha256 = value.get("provenance_sha256")
    semantic_sha256 = value.get("semantic_sha256")
    policy_sha256 = value.get("policy_sha256")
    if not all(
        _is_sha256(item)
        for item in (
            provenance_sha256,
            semantic_sha256,
            policy_sha256,
            artifact_sha256,
        )
    ):
        return False
    if layer == "model":
        expected_policy_sha256 = canonical_mapping_sha256(
            {
                "schema": CANONICAL_FINGERPRINT_SCHEMA,
                "artifact_kind": "decision_model_executable_policy",
                "projection": policy_projection,
            },
            decimals=canonical_contract["decimals"],
            exact_strings=True,
        )
        artifact_kind = "decision_model_canonical_runtime_v2"
    else:
        expected_policy_sha256 = canonical_policy_fingerprint(
            policy_projection,
            decimals=canonical_contract["decimals"],
        )["sha256"]
        artifact_kind = "decision_trade_selector_canonical_runtime_v2"
    if policy_sha256 != expected_policy_sha256:
        return False
    expected_artifact_sha256 = compose_artifact_fingerprint(
        artifact_kind=artifact_kind,
        provenance_sha256=provenance_sha256,
        semantic_sha256=semantic_sha256,
        policy_sha256=policy_sha256,
        decimals=canonical_contract["decimals"],
    )
    return artifact_sha256 == expected_artifact_sha256


def _v2_layer_integrity(
    prediction: pd.DataFrame,
    *,
    layer: str,
    prediction_version_column: str,
    prediction_artifact_column: str,
    prediction_canonical_schema_column: str,
    prediction_canonical_decimals_column: str,
    prediction_execution_numeric_mode_column: str,
    prediction_raw_execution_preserved_column: str,
    backtest_payload: Any,
    model_meta_payload: Any,
    version_key: str,
    artifact_key: str,
    fingerprint_key: str,
    canonical_contract_key: str,
    artifact_prediction: pd.DataFrame | None = None,
    prediction_domain_valid: bool = True,
    prediction_domain_rows: int | None = None,
    prediction_outside_domain_rows: int | None = None,
) -> dict[str, Any]:
    backtest_payload = (
        backtest_payload if isinstance(backtest_payload, dict) else {}
    )
    model_meta_payload = (
        model_meta_payload if isinstance(model_meta_payload, dict) else {}
    )

    prediction_version, prediction_version_complete = (
        _strict_unique_exact_text_column_value(
            prediction,
            prediction_version_column,
        )
    )
    backtest_version = _exact_nonempty_text(
        backtest_payload.get(version_key)
    )
    model_meta_version = _exact_nonempty_text(
        model_meta_payload.get(version_key)
    )
    version_match = bool(
        prediction_version_complete
        and prediction_version
        == backtest_version
        == model_meta_version
    )

    artifact_scope = (
        artifact_prediction
        if artifact_prediction is not None
        else prediction
    )
    prediction_artifact, prediction_artifact_complete = (
        _strict_unique_exact_text_column_value(
            artifact_scope,
            prediction_artifact_column,
        )
    )
    backtest_artifact = _exact_nonempty_text(
        backtest_payload.get(artifact_key)
    )
    model_meta_artifact = _exact_nonempty_text(
        model_meta_payload.get(artifact_key)
    )
    fingerprints_match = bool(
        prediction_artifact_complete
        and _is_sha256(prediction_artifact)
        and prediction_artifact
        == backtest_artifact
        == model_meta_artifact
    )

    prediction_schema, prediction_schema_complete = (
        _strict_unique_exact_text_column_value(
            prediction,
            prediction_canonical_schema_column,
        )
    )
    prediction_execution_mode, prediction_execution_mode_complete = (
        _strict_unique_exact_text_column_value(
            prediction,
            prediction_execution_numeric_mode_column,
        )
    )
    prediction_raw_execution_preserved = _strict_all_true_column(
        prediction,
        prediction_raw_execution_preserved_column,
    )
    backtest_contract_raw = backtest_payload.get(canonical_contract_key)
    model_meta_contract_raw = model_meta_payload.get(
        canonical_contract_key
    )
    backtest_contract = _valid_canonical_contract(
        backtest_contract_raw,
        layer=layer,
    )
    model_meta_contract = _valid_canonical_contract(
        model_meta_contract_raw,
        layer=layer,
    )
    canonical_contract_match = bool(
        prediction_schema_complete
        and prediction_execution_mode_complete
        and prediction_raw_execution_preserved
        and backtest_contract
        and model_meta_contract
        and backtest_contract == model_meta_contract
        and prediction_schema == model_meta_contract["schema"]
        and prediction_execution_mode
        == model_meta_contract["execution_mode"]
        and model_meta_contract["raw_execution_preserved"] is True
    )

    prediction_decimals, prediction_decimals_complete = (
        _strict_unique_canonical_decimals_column_value(
            prediction,
            prediction_canonical_decimals_column,
        )
    )
    canonical_decimals_match = bool(
        prediction_decimals_complete
        and backtest_contract
        and model_meta_contract
        and prediction_decimals
        == backtest_contract["decimals"]
        == model_meta_contract["decimals"]
    )

    backtest_fingerprint = backtest_payload.get(fingerprint_key)
    model_meta_fingerprint = model_meta_payload.get(fingerprint_key)
    fingerprint_v2_valid = bool(
        version_match
        and fingerprints_match
        and canonical_contract_match
        and backtest_fingerprint == model_meta_fingerprint
        and _valid_v2_fingerprint(
            backtest_fingerprint,
            layer=layer,
            canonical_version=backtest_version,
            artifact_sha256=backtest_artifact,
            canonical_contract=backtest_contract,
        )
        and _valid_v2_fingerprint(
            model_meta_fingerprint,
            layer=layer,
            canonical_version=model_meta_version,
            artifact_sha256=model_meta_artifact,
            canonical_contract=model_meta_contract,
        )
    )
    policy_ready = bool(
        fingerprint_v2_valid
        and model_meta_fingerprint["policy_projection"]["ready"] is True
    )

    failures = [
        name
        for name, passed in (
            ("prediction_domain", prediction_domain_valid),
            ("canonical_v2_version", version_match),
            ("artifact_v2_sha256", fingerprints_match),
            ("canonical_contract", canonical_contract_match),
            ("canonical_decimals", canonical_decimals_match),
            ("fingerprint_v2", fingerprint_v2_valid),
        )
        if not passed
    ]
    return {
        "canonical_version": model_meta_version,
        "artifact_sha256": model_meta_artifact,
        "canonical_contract": model_meta_contract,
        "canonical_schema": (
            model_meta_contract.get("schema")
            if model_meta_contract
            else ""
        ),
        "canonical_decimals": (
            model_meta_contract.get("decimals")
            if model_meta_contract
            else None
        ),
        "version_match": version_match,
        "fingerprints_match": fingerprints_match,
        "canonical_contract_match": canonical_contract_match,
        "canonical_decimals_match": canonical_decimals_match,
        "fingerprint_v2": (
            model_meta_fingerprint
            if isinstance(model_meta_fingerprint, dict)
            else {}
        ),
        "fingerprint_v2_valid": fingerprint_v2_valid,
        "policy_ready": policy_ready,
        "execution_numeric_mode": (
            model_meta_contract.get("execution_mode")
            if model_meta_contract
            else ""
        ),
        "raw_execution_preserved": bool(
            model_meta_contract.get("raw_execution_preserved")
        )
        if model_meta_contract
        else False,
        "match": not failures,
        "failures": failures,
        "prediction_domain_valid": prediction_domain_valid,
        "prediction_domain_rows": (
            int(prediction_domain_rows)
            if prediction_domain_rows is not None
            else int(len(prediction))
        ),
        "prediction_outside_domain_rows": (
            int(prediction_outside_domain_rows)
            if prediction_outside_domain_rows is not None
            else 0
        ),
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if pd.isna(value):
        return None
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def _report_dates(root: Path) -> list[str]:
    dates: list[str] = []
    for path in (root / "outputs" / "decision").glob("decision_report_*.md"):
        match = REPORT_RE.fullmatch(path.name)
        if match:
            dates.append(match.group(1))
    return sorted(set(dates), reverse=True)


def _candidate_path(root: Path, evaluation: dict[str, Any], signal_date: str) -> Path:
    raw = str((evaluation.get("paths", {}) or {}).get("candidates", "") or "").strip()
    if raw:
        path = Path(raw)
        path = path if path.is_absolute() else root / path
        if path.exists():
            return path
    return root / "data" / "decision" / f"decision_candidates_{signal_date}.csv"


def _industry(row: pd.Series) -> str:
    for column in ("industry", "industry_tag", "行业", "行业板块", "board"):
        if column in row.index:
            value = _text(row.get(column))
            if value:
                return value
    return "未分类"


def _limit_up_industry_leaders(
    value: Any,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    leaders: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        industry = _text(raw.get("industry"))
        limit_up_count = _integer(raw.get("limit_up_count"))
        if not industry or industry == "未分类" or limit_up_count <= 0:
            continue
        item: dict[str, Any] = {
            "rank": len(leaders) + 1,
            "industry": industry,
            "limit_up_count": limit_up_count,
        }
        share = _number(raw.get("share"))
        if share is not None:
            item["share"] = share
        leaders.append(item)
        if len(leaders) == limit:
            break
    return leaders


def _rejection_reason(value: Any) -> str:
    reason = _text(value)
    return {
        "no_safe_price": "没有通过成本、成交、退出和尾部风险约束的安全竞价价格",
        "selection_policy_not_ready": "独立策略留出期没有找到同时满足频率、收益、成本压力与尾部风险的可执行策略",
        "big_loss_probability_exceeds_cap": "预测大跌概率超过独立留出期确定的风险上限，禁止建议买入",
        "big_loss_probability_exceeds_policy_cap": "预测大跌概率超过独立留出期确定的风险上限，禁止建议买入",
        "return_lcb_not_positive": "保守收益下界不为正，禁止建议买入",
        "mean_return_lcb_below_policy_floor": "预测均值的保守下界低于独立留出期确定的最低要求",
        "exit_probability_below_floor": "T+1可退出概率不足，禁止建议买入",
        "exit_probability_below_policy_floor": "T+1可退出概率低于策略门槛，禁止建议买入",
        "fill_probability_below_floor": "竞价可成交概率不足，放弃",
        "fill_probability_below_policy_floor": "竞价可成交概率低于策略门槛，放弃",
        "profit_probability_below_floor": "盈利概率不足，放弃",
        "conservative_edge_below_floor": "扣除尾部风险与不可退出风险后没有正优势",
        "conservative_ev_below_policy_floor": "扣除尾部风险与不可退出风险后的保守期望不足",
        "selection_score_below_policy_cutoff": "综合排序分低于独立留出期确定的入选分位",
        "insufficient_independent_history": "独立交易日样本不足，模型尚未达到可用条件",
        "insufficient_nested_oos_history": "第二层交易排序的严格样本外历史不足",
        "outside_observation_top10": "不在当日观察Top10内",
        "below_learned_policy": "未通过第二层交易策略的收益、成交与大跌风险门槛",
        "shadow_policy_only": "第二层仅通过影子排序，尚未达到正式晋级门槛",
        "selector_not_promoted": "第二层交易排序未通过严格样本外晋级门槛",
    }.get(reason, reason or "没有满足风险约束的安全竞价价格")


def _decision_lookup(frame: pd.DataFrame) -> dict[str, pd.Series]:
    if frame.empty or "ts_code" not in frame.columns:
        return {}
    return {str(row["ts_code"]): row for _, row in frame.drop_duplicates("ts_code", keep="first").iterrows()}


def _pending_candidates(frame: pd.DataFrame, limit: int = 20) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    eligible, _ = filter_standard_limit_universe(frame, code_col="ts_code", name_col="name")
    rows: list[dict[str, Any]] = []
    for index, (_, row) in enumerate(eligible.head(limit).iterrows(), start=1):
        rows.append(
            {
                "rank": index,
                "action": "PENDING",
                "ts_code": _text(row.get("ts_code")),
                "name": _text(row.get("name")),
                "industry": _industry(row),
                "stage_transition": _text(row.get("stage_transition"))
                or _text(row.get("stage"))
                or _text(row.get("advance_stage")),
                "stage_focus": _integer(row.get("stage_focus"), 1),
                "trade_rank": _integer(row.get("trade_rank")) or None,
                "promotion_rank": _integer(row.get("promotion_rank")) or None,
                "mechanism_limit_pct": _number(
                    row.get("mechanism_limit_pct", row.get("decision_limit_pct"))
                ),
                "predicted_big_loss_probability": _number(
                    row.get("predicted_big_loss_probability")
                ),
                "predicted_return_lcb": _number(row.get("predicted_return_lcb")),
                "predicted_continuation_limit_up_probability": _number(
                    row.get("predicted_continuation_limit_up_probability")
                ),
                "target_weight": 0.0,
                "decision_p_fill": _number(row.get("p_fill_pred")),
                "decision_e_ret": _number(row.get("e_ret_pred")),
                "decision_ev": _number(row.get("ev_pred")),
                "decision_cost": _number(row.get("cost_est")),
                "decision_risk_penalty": _number(row.get("risk_penalty")),
                "rejection_reason": "等待 Decision 竞价指导模型完成严格样本外定价",
            }
        )
    return rows


def _merge_auction_candidates(
    prediction: pd.DataFrame,
    decision: pd.DataFrame,
    *,
    promoted: bool,
    risk_budget: float,
) -> list[dict[str, Any]]:
    lookup = _decision_lookup(decision)
    prediction = annotate_standard_limit_universe(prediction, code_col="ts_code", name_col="name")
    selected_source = (
        prediction["trade_selected"]
        if "trade_selected" in prediction.columns
        else prediction.get("selected")
    )
    selected_mask = pd.to_numeric(selected_source, errors="coerce").fillna(0).eq(1)
    eligible_mask = pd.to_numeric(prediction["decision_universe_eligible"], errors="coerce").fillna(0).eq(1)
    selected_count = int((selected_mask & eligible_mask).sum())
    per_position_weight = min(0.12, max(0.0, risk_budget) / max(selected_count, 1)) if promoted else 0.0
    rows: list[dict[str, Any]] = []
    for index, (_, row) in enumerate(prediction.iterrows(), start=1):
        code = _text(row.get("ts_code"))
        old = lookup.get(code, pd.Series(dtype=object))
        universe_eligible = _integer(row.get("decision_universe_eligible")) == 1
        selected = (
            _integer(row.get("trade_selected")) == 1
            and universe_eligible
        )
        shadow_selected = (
            _integer(row.get("trade_shadow_selected")) == 1
            and universe_eligible
        )
        action = (
            "BUY"
            if promoted and selected
            else "SHADOW_ONLY"
            if selected or shadow_selected
            else "REJECT"
        )
        reason = ""
        if not universe_eligible:
            action = "REJECT"
            reason = _text(row.get("decision_universe_reason")) or "涨跌幅机制不符合不超过10%的交易范围"
        elif not promoted and selected:
            reason = "严格样本外晋级门槛未全部通过，禁止正式买入"
        elif shadow_selected and not selected:
            reason = "第二层影子交易排序入选，但严格样本外晋级尚未通过"
        elif action != "BUY":
            reason = _rejection_reason(row.get("model_reason"))
        rows.append(
            {
                "rank": index,
                "action": action,
                "ts_code": code,
                "name": _text(row.get("name")) or _text(old.get("name")),
                "industry": _text(row.get("industry")) or _industry(old),
                "stage_transition": _text(row.get("stage_transition")) or _text(row.get("stage")),
                "stage_focus": _integer(row.get("stage_focus")),
                "shadow_rank": _integer(row.get("shadow_rank")),
                "shadow_selected": _integer(row.get("shadow_selected")),
                "first_layer_selected": _integer(
                    row.get("first_layer_selected")
                ),
                "first_layer_shadow_selected": _integer(
                    row.get("first_layer_shadow_selected")
                ),
                "trade_rank": _integer(row.get("trade_rank")),
                "promotion_rank": _integer(row.get("promotion_rank")),
                "promotion_rank_score": _number(
                    row.get("promotion_rank_score")
                ),
                "predicted_promotion_probability": _number(
                    row.get("predicted_promotion_probability")
                ),
                "promotion_rank_quality_ready": _integer(
                    row.get("promotion_rank_quality_ready")
                ),
                "promotion_probability_quality_ready": _integer(
                    row.get("promotion_probability_quality_ready")
                ),
                "trade_score": _number(row.get("trade_score")),
                "trade_predicted_conditional_net_return": _number(
                    row.get("trade_predicted_conditional_net_return")
                ),
                "trade_predicted_mean_return_lcb": _number(
                    row.get("trade_predicted_mean_return_lcb")
                ),
                "trade_predicted_fill_probability": _number(
                    row.get("trade_predicted_fill_probability")
                ),
                "trade_predicted_big_loss_probability": _number(
                    row.get("trade_predicted_big_loss_probability")
                ),
                "trade_predicted_outcome_q10": _number(
                    row.get("trade_predicted_outcome_q10")
                ),
                "trade_tail_loss_proxy": _number(
                    row.get("trade_tail_loss_proxy")
                ),
                "trade_tail_risk_weight": _number(
                    row.get("trade_tail_risk_weight")
                ),
                "trade_gate_pass": _integer(row.get("trade_gate_pass")),
                "trade_shadow_selected": _integer(
                    row.get("trade_shadow_selected")
                ),
                "trade_selected": _integer(row.get("trade_selected")),
                "trade_selector_policy_ready": _integer(
                    row.get("trade_selector_policy_ready")
                ),
                "trade_selector_promoted": _integer(
                    row.get("trade_selector_promoted")
                ),
                "trade_selector_version": _text(
                    row.get("trade_selector_version")
                ),
                "trade_selector_artifact_sha256": _text(
                    row.get("trade_selector_artifact_sha256")
                ),
                "trade_selector_canonical_v2_version": _text(
                    row.get("trade_selector_canonical_v2_version")
                ),
                "trade_selector_artifact_v2_sha256": _text(
                    row.get("trade_selector_artifact_v2_sha256")
                ),
                "trade_selector_canonical_schema": _text(
                    row.get("trade_selector_canonical_schema")
                ),
                "trade_selector_canonical_decimals": _canonical_decimals(
                    row.get("trade_selector_canonical_decimals")
                ),
                "trade_selector_execution_numeric_mode": _text(
                    row.get("trade_selector_execution_numeric_mode")
                ),
                "trade_selector_raw_execution_preserved": (
                    _integer(
                        row.get(
                            "trade_selector_raw_execution_preserved"
                        )
                    )
                    == 1
                ),
                "model_canonical_v2_version": _text(
                    row.get("model_canonical_v2_version")
                ),
                "model_artifact_v2_sha256": _text(
                    row.get("model_artifact_v2_sha256")
                ),
                "model_canonical_schema": _text(
                    row.get("model_canonical_schema")
                ),
                "model_canonical_decimals": _canonical_decimals(
                    row.get("model_canonical_decimals")
                ),
                "model_execution_numeric_mode": _text(
                    row.get("model_execution_numeric_mode")
                ),
                "model_raw_execution_preserved": (
                    _integer(row.get("model_raw_execution_preserved"))
                    == 1
                ),
                "trade_model_reason": _text(row.get("trade_model_reason")),
                "path_label_code": _text(row.get("path_label_code")),
                "path_label": _text(row.get("path_label")) or "路径数据不足",
                "path_explanation": _text(row.get("path_explanation")),
                "path_data_coverage": _number(row.get("path_data_coverage")),
                "path_strength_latest": _number(row.get("path_strength_latest")),
                "path_strength_delta": _number(row.get("path_strength_delta")),
                "stage_pool_size": _integer(row.get("stage_pool_size")),
                "focus_pool_size": _integer(row.get("focus_pool_size")),
                "same_industry_stage_count": _integer(
                    row.get("same_industry_stage_count")
                ),
                "stage_recent_promotion_rate": _number(
                    row.get("stage_recent_promotion_rate")
                ),
                "stage_recent_promotion_samples": _integer(
                    row.get("stage_recent_promotion_samples")
                ),
                "target_weight": per_position_weight if action == "BUY" else 0.0,
                "mechanism_limit_pct": _number(row.get("decision_limit_pct")),
                "d_close": _number(row.get("d_close")),
                "estimated_up_limit": _number(row.get("estimated_up_limit")),
                "recommended_max_price": _number(row.get("recommended_max_price")),
                "max_auction_change_pct": _number(row.get("max_auction_change_pct")),
                "diagnostic_gap": _number(row.get("diagnostic_gap")),
                "observation_max_price": _number(row.get("observation_max_price")),
                "observation_auction_change_pct": _number(
                    row.get("observation_auction_change_pct")
                ),
                "observation_price_basis": _text(row.get("observation_price_basis")),
                "observation_price_is_formal": _integer(
                    row.get("observation_price_is_formal")
                ),
                "observation_risk_tier": _integer(
                    row.get("observation_risk_tier"),
                    2,
                ),
                "observation_risk_label": _text(
                    row.get("observation_risk_label")
                ),
                "take_profit_pct": _number(row.get("take_profit_pct")),
                "stop_loss_pct": _number(row.get("stop_loss_pct")),
                "take_profit_price": _number(row.get("take_profit_price")),
                "stop_loss_price": _number(row.get("stop_loss_price")),
                "latest_exit_time": _text(row.get("latest_exit_time")),
                "exit_policy_version": _text(row.get("exit_policy_version")),
                "predicted_fill_probability": _number(row.get("predicted_fill_probability")),
                "predicted_exit_probability": _number(row.get("predicted_exit_probability")),
                "predicted_profit_probability": _number(row.get("predicted_profit_probability")),
                "predicted_big_loss_probability": _number(row.get("predicted_big_loss_probability")),
                "predicted_continuation_limit_up_probability": _number(
                    row.get("predicted_continuation_limit_up_probability")
                ),
                "predicted_net_return": _number(row.get("predicted_net_return")),
                "predicted_return_lcb": _number(row.get("predicted_return_lcb")),
                "predicted_return_ucb": _number(row.get("predicted_return_ucb")),
                "conservative_ev": _number(row.get("conservative_ev")),
                "decision_p_fill": _number(old.get("p_fill_pred")),
                "decision_e_ret": _number(old.get("e_ret_pred")),
                "decision_ev": _number(old.get("ev_pred")),
                "decision_cost": _number(old.get("cost_est")),
                "decision_risk_penalty": _number(old.get("risk_penalty")),
                "entry_rule": _text(row.get("entry_rule")),
                "exit_rule": _text(row.get("exit_rule")),
                "order_type": _text(row.get("order_type")),
                "market_order_allowed": _integer(row.get("market_order_allowed")),
                "risk_gate_pass": _integer(row.get("risk_gate_pass")),
                "gate_policy_ready": _integer(row.get("gate_policy_ready")),
                "gate_exit_probability": _integer(
                    row.get("gate_exit_probability")
                ),
                "gate_fill_probability": _integer(
                    row.get("gate_fill_probability")
                ),
                "gate_big_loss_probability": _integer(
                    row.get("gate_big_loss_probability")
                ),
                "gate_mean_return_lcb": _integer(
                    row.get("gate_mean_return_lcb")
                ),
                "gate_conservative_ev": _integer(
                    row.get("gate_conservative_ev")
                ),
                "gate_selection_score": _integer(
                    row.get("gate_selection_score")
                ),
                "rejection_reason": reason,
            }
        )
    return rows


def _stage_watchlist(
    rows: list[dict[str, Any]],
    limit: int = OBSERVATION_TOP_N,
) -> tuple[list[dict[str, Any]], int]:
    return rank_observation_rows(rows, limit=limit)


def _ensure_relative_best_two(
    rows: list[dict[str, Any]],
    *,
    limit: int = 2,
) -> list[dict[str, Any]]:
    def eligible_candidate(row: dict[str, Any]) -> bool:
        mechanism_limit = _number(row.get("mechanism_limit_pct"))
        transition = _text(row.get("stage_transition"))
        return (
            _integer(row.get("decision_universe_eligible"), 1) == 1
            and (mechanism_limit is None or mechanism_limit <= 10.0)
            and transition in {"2→3", "3→4"}
        )

    eligible = [
        row
        for row in rows
        if eligible_candidate(row) and _integer(row.get("stage_focus"), 1) == 1
    ]
    if not eligible:
        eligible = [row for row in rows if eligible_candidate(row)]
    if not eligible:
        return rows

    # A positive trade rank means the selector admitted the row into its
    # frozen Top10.  Unranked rows may still be present for diagnostics, but
    # must not displace the selector's two live shadow choices.  Pending plans
    # have no trade ranks yet and continue to use the deterministic fallback.
    ranked_pool = [row for row in eligible if _integer(row.get("trade_rank")) > 0]
    uses_selector_ranks = bool(ranked_pool)
    if uses_selector_ranks:
        eligible = ranked_pool

    def ascending(value: Any, default: float) -> float:
        number = _number(value)
        return number if number is not None else default

    def descending(value: Any, default: float) -> float:
        number = _number(value)
        return -number if number is not None else default

    ordered = sorted(
        eligible,
        key=lambda row: (
            ascending(row.get("trade_rank"), 999999.0),
            ascending(row.get("promotion_rank"), 999999.0),
            ascending(
                row.get("trade_predicted_big_loss_probability"),
                ascending(row.get("predicted_big_loss_probability"), 1.0),
            ),
            descending(
                row.get("trade_predicted_mean_return_lcb"),
                descending(row.get("predicted_return_lcb"), 999999.0),
            ),
            descending(
                row.get("predicted_continuation_limit_up_probability"),
                999999.0,
            ),
            ascending(row.get("rank"), 999999.0),
            _text(row.get("ts_code")),
        ),
    )
    if not uses_selector_ranks:
        for fallback_rank, row in enumerate(ordered, start=1):
            if _integer(row.get("trade_rank")) <= 0:
                row["trade_rank"] = fallback_rank
    selected_ids = {id(row) for row in ordered[: min(limit, len(ordered))]}
    for row in rows:
        selected = id(row) in selected_ids
        row["trade_shadow_selected"] = int(selected)
        if selected and _text(row.get("action")) != "BUY":
            row["action"] = "SHADOW_ONLY"
            row["rejection_reason"] = (
                "二筛相对优选入选；正式样本外盈利门槛独立审计"
            )
        elif not selected and _text(row.get("action")) == "SHADOW_ONLY":
            row["action"] = "REJECT"
    return rows


def _observation_status_label(value: Any) -> str:
    return {
        "PENDING_T": "等待T日收盘",
        "PENDING_T1": "T日市价已验证，等待T+1",
        "T_VERIFIED_FILLED": "T日市价已验证",
        "T_VERIFIED_NO_FILL": "无有效开盘成交价",
        "FINAL_VERIFIED": "T+1最终完成",
        "FINAL_NO_FILL": "无有效开盘成交价",
        "PENDING_EXIT_TRUTH": "等待可退出真值",
    }.get(_text(value), _text(value) or "待验证")


def _prediction_timing_label(value: Any) -> str:
    return {
        "PREMARKET_VALID": "9:25前冻结",
        "RETROSPECTIVE_LATE_GENERATION": "收盘后回溯",
        "UNKNOWN_GENERATION_TIME": "生成时间未知",
        "UNKNOWN_BUY_DATE": "执行日未知",
    }.get(_text(value), _text(value) or "待审计")


def _observation_frame(root: Path, exec_date: str) -> pd.DataFrame:
    dated = root / "outputs" / "auction_v3" / "verification" / f"observation_{exec_date}.csv"
    if dated.exists():
        return _read_csv(dated)
    ledger = _read_csv(
        root / "outputs" / "auction_v3" / "verification" / "observation_latest.csv"
    )
    if ledger.empty or "expected_buy_date" not in ledger.columns:
        return pd.DataFrame()
    dates = ledger["expected_buy_date"].map(_date)
    return ledger[dates.eq(exec_date)].copy()


def _attach_observation_validation(
    root: Path,
    plan: dict[str, Any],
) -> dict[str, Any]:
    plan = dict(plan)
    candidates = [
        dict(row)
        for row in plan.get("candidates", [])
        if isinstance(row, dict)
    ]
    watchlist, watch_total = _stage_watchlist(candidates)
    exec_date = _date(plan.get("exec_date"))
    truth = _observation_frame(root, exec_date)
    lookup: dict[str, pd.Series] = {}
    if not truth.empty and "ts_code" in truth.columns:
        lookup = {
            _text(row.get("ts_code")): row
            for _, row in truth.drop_duplicates("ts_code", keep="last").iterrows()
        }
    truth_fields = (
        "observation_max_price",
        "observation_auction_change_pct",
        "observation_price_basis",
        "observation_price_is_formal",
        "observation_rank",
        "observation_pool_size",
        "validation_mode",
        "observation_execution_mode",
        "prediction_timing_status",
        "prediction_timing_valid",
        "prediction_deadline_utc",
        "validation_status",
        "actual_buy_date",
        "actual_open_price",
        "actual_t_close",
        "market_daily_return",
        "observation_fill",
        "observation_fill_reason",
        "observation_limit_accept",
        "observation_price_vs_cap",
        "market_buyable_diagnostic",
        "market_buyable_reason",
        "observation_t_return",
        "continuation_limit_up_hit",
        "actual_exit_date",
        "actual_exit_price",
        "actual_gross_return",
        "actual_net_return",
        "exit_reason",
        "truth_source",
        "truth_generated_at_utc",
    )
    for row in watchlist:
        verified = lookup.get(_text(row.get("ts_code")))
        if verified is not None:
            for field in truth_fields:
                value = verified.get(field)
                row[field] = _json_safe(value)
        row["validation_status_label"] = _observation_status_label(
            row.get("validation_status")
        )
        row["prediction_timing_label"] = _prediction_timing_label(
            row.get("prediction_timing_status")
        )
        row["watch_label"] = (
            "正式买入"
            if _text(row.get("action")) == "BUY"
            else "二筛影子"
            if _integer(row.get("trade_shadow_selected")) == 1
            else "仅观察"
        )

    metrics = _read_json(
        root
        / "outputs"
        / "auction_v3"
        / "metrics"
        / "observation_cumulative_latest.json"
    )
    statuses = [_text(row.get("validation_status")) for row in watchlist]
    plan.update(
        {
            "schema_version": "decision_action_plan_v12_top10_trade_selector",
            "stage_watchlist": watchlist,
            "stage_watch_count": len(watchlist),
            "stage_watch_eligible_count": watch_total,
            "stage_watch_display_limit": OBSERVATION_TOP_N,
            "observation_validation": {
                "schema_version": "decision_observation_validation_v4_auction_truth",
                "exec_date": exec_date,
                "rows": len(watchlist),
                "t_validated_rows": sum(status not in {"", "PENDING_T"} for status in statuses),
                "final_rows": sum(status.startswith("FINAL_") for status in statuses),
                "premarket_valid_rows": sum(
                    int((_number(row.get("prediction_timing_valid")) or 0) == 1)
                    for row in watchlist
                ),
                "retrospective_rows": sum(
                    _text(row.get("prediction_timing_status"))
                    == "RETROSPECTIVE_LATE_GENERATION"
                    for row in watchlist
                ),
                "pending_rows": sum(
                    status in {"", "PENDING_T", "PENDING_T1", "PENDING_EXIT_TRUTH"}
                    for status in statuses
                ),
                "generated_at_utc": max(
                    (_text(row.get("truth_generated_at_utc")) for row in watchlist),
                    default="",
                ),
                "public_market_proxy": True,
                "execution_mode": "market_at_open_proxy",
                "market_open_fill_assumption": True,
                "displayed_limit_affects_fill": False,
                "manual_actual_separate": True,
            },
            "observation_statistics": metrics,
        }
    )
    return _json_safe(plan)


def _attach_market_close_comparison(
    root: Path,
    plan: dict[str, Any],
    *,
    engine: Any = None,
) -> dict[str, Any]:
    """Attach deterministic D/T close diagnostics without changing decisions."""
    plan = dict(plan)
    signal_date = _date(plan.get("signal_date"))
    exec_date = _date(plan.get("exec_date"))
    try:
        if engine is None:
            from top10decision.auction_v3.config import AuctionV3Config
            from top10decision.auction_v3.engine import AuctionV3Engine

            engine = AuctionV3Engine(AuctionV3Config(root=root))
        d_snapshot = engine.market_close_display_snapshot(signal_date)
        t_snapshot = engine.market_close_display_snapshot(exec_date)
    except Exception as exc:
        plan["market_close_comparison"] = {
            "scope": "all_a_share_daily_close",
            "ranking_anchor": "D",
            "d": {
                "trade_date": signal_date,
                "available": False,
                "status": "SNAPSHOT_ERROR",
            },
            "t": {
                "trade_date": exec_date,
                "available": False,
                "status": "SNAPSHOT_ERROR",
                "maturity_status": "WAITING_T_CLOSE",
            },
            "error": type(exc).__name__,
        }
        return _json_safe(plan)

    d_snapshot = dict(d_snapshot)
    t_snapshot = dict(t_snapshot)
    d_available = d_snapshot.get("available") is True
    t_close_available = t_snapshot.get("available") is True
    d_stock_count = _integer(d_snapshot.get("stock_count"))
    t_stock_count = _integer(t_snapshot.get("stock_count"))
    t_coverage_against_d = (
        float(t_stock_count / d_stock_count)
        if d_stock_count > 0
        else None
    )
    t_mature = bool(
        d_available
        and t_close_available
        and t_coverage_against_d is not None
        and t_coverage_against_d >= 0.90
    )

    d_snapshot["maturity_status"] = (
        "FINAL_D_CLOSE"
        if d_available
        else "D_CLOSE_UNAVAILABLE"
    )
    t_snapshot["raw_close_available"] = t_close_available
    t_snapshot["coverage_against_d"] = t_coverage_against_d
    t_snapshot["available"] = t_mature
    t_snapshot["maturity_status"] = (
        "FINAL_T_CLOSE"
        if t_mature
        else (
            "INCOMPLETE_T_CLOSE"
            if t_close_available
            else "WAITING_T_CLOSE"
        )
    )
    plan["market_close_comparison"] = {
        "scope": "all_a_share_daily_close",
        "ranking_anchor": "D",
        "t_minimum_d_coverage": 0.90,
        "model_input": False,
        "d": d_snapshot,
        "t": t_snapshot,
    }
    return _json_safe(plan)


def build_action_plan(root: Path, report_date: str = "") -> dict[str, Any]:
    root = root.resolve()
    dates = _report_dates(root)
    chosen_date = _date(report_date) or (dates[0] if dates else "")
    if not chosen_date:
        raise RuntimeError("No decision_report_YYYYMMDD.md exists")

    evaluation = _read_json(root / "outputs" / "decision" / f"eval_{chosen_date}.json")
    signal_date = _date(evaluation.get("signal_date"))
    exec_date = _date(evaluation.get("exec_date")) or chosen_date
    exit_date = _date(evaluation.get("exit_date"))
    risk_budget = _number(evaluation.get("risk_budget")) or 0.0
    candidates = _read_csv(_candidate_path(root, evaluation, signal_date))

    prediction = _read_csv(root / "outputs" / "auction_v3" / "predictions" / "pred_latest.csv")
    backtest = _read_json(root / "outputs" / "auction_v3" / "metrics" / "backtest_latest.json")
    model_meta = _read_json(root / "outputs" / "auction_v3" / "models" / "model_meta_latest.json")
    sentiment_meta = model_meta.get("current_market_sentiment") or {}

    def sentiment_value(name: str) -> Any:
        if not prediction.empty and name in prediction.columns:
            value = prediction[name].iloc[0]
            try:
                if not pd.isna(value):
                    return value
            except Exception:
                if value is not None:
                    return value
        return sentiment_meta.get(name)

    pred_signal = _strict_unique_prediction_date(prediction, "signal_date")
    pred_buy = _strict_unique_prediction_date(prediction, "expected_buy_date")
    pred_exit = _strict_unique_prediction_date(prediction, "expected_exit_date")
    prediction_matches = bool(signal_date and pred_signal == signal_date and pred_buy == exec_date and pred_exit == exit_date)
    pred_version = _text(prediction.get("model_version", pd.Series([""])).iloc[0]) if not prediction.empty else ""
    backtest_version = _text(backtest.get("model_version"))
    meta_version = _text(model_meta.get("model_version"))
    artifact_versions_match = bool(
        pred_version
        and pred_version == backtest_version == meta_version
    )
    pred_artifact = (
        _text(
            prediction.get(
                "model_artifact_sha256",
                pd.Series([""]),
            ).iloc[0]
        )
        if not prediction.empty
        else ""
    )
    backtest_artifact = _text(backtest.get("model_artifact_sha256"))
    meta_artifact = _text(model_meta.get("model_artifact_sha256"))
    artifact_fingerprints_match = bool(
        pred_artifact
        and pred_artifact == backtest_artifact == meta_artifact
    )
    artifacts_match = bool(
        artifact_versions_match
        and artifact_fingerprints_match
    )
    model_v2_integrity = _v2_layer_integrity(
        prediction,
        layer="model",
        prediction_version_column="model_canonical_v2_version",
        prediction_artifact_column="model_artifact_v2_sha256",
        prediction_canonical_schema_column="model_canonical_schema",
        prediction_canonical_decimals_column="model_canonical_decimals",
        prediction_execution_numeric_mode_column=(
            "model_execution_numeric_mode"
        ),
        prediction_raw_execution_preserved_column=(
            "model_raw_execution_preserved"
        ),
        backtest_payload=backtest,
        model_meta_payload=model_meta,
        version_key="model_canonical_v2_version",
        artifact_key="model_artifact_v2_sha256",
        fingerprint_key="model_fingerprint_v2",
        canonical_contract_key="model_canonical_contract",
    )
    prediction_ready = _strict_all_true_column(
        prediction,
        "model_ready",
    )
    prediction_promoted = _strict_all_true_column(
        prediction,
        "trade_selector_promoted",
    )
    backtest_selector_raw = backtest.get("trade_selector")
    meta_selector_raw = model_meta.get("trade_selector")
    backtest_selector = (
        backtest_selector_raw
        if isinstance(backtest_selector_raw, dict)
        else {}
    )
    meta_selector = (
        meta_selector_raw
        if isinstance(meta_selector_raw, dict)
        else {}
    )
    selector_domain = _selector_prediction_domain(prediction)
    selector_prediction = selector_domain["frame"]
    prediction_selector_artifact, prediction_selector_artifact_complete = (
        _strict_unique_text_column_value(
            selector_prediction,
            "trade_selector_artifact_sha256",
        )
    )
    prediction_selector_version, prediction_selector_version_complete = (
        _strict_unique_exact_text_column_value(
            prediction,
            "trade_selector_version",
        )
    )
    backtest_selector_version = _exact_nonempty_text(
        backtest_selector.get("version")
    )
    meta_selector_version = _exact_nonempty_text(
        meta_selector.get("version")
    )
    selector_versions_match = bool(
        prediction_selector_version_complete
        and prediction_selector_version
        == backtest_selector_version
        == meta_selector_version
    )
    backtest_selector_artifact = _exact_nonempty_text(
        backtest_selector.get("production_artifact_sha256")
    )
    meta_selector_artifact = _exact_nonempty_text(
        meta_selector.get("production_artifact_sha256")
    )
    selector_artifacts_match = bool(
        selector_domain["valid"]
        and selector_versions_match
        and prediction_selector_artifact_complete
        and _is_sha256(prediction_selector_artifact)
        and prediction_selector_artifact
        == backtest_selector_artifact
        == meta_selector_artifact
    )
    selector_v2_integrity = _v2_layer_integrity(
        prediction,
        layer="trade_selector",
        prediction_version_column="trade_selector_canonical_v2_version",
        prediction_artifact_column="trade_selector_artifact_v2_sha256",
        prediction_canonical_schema_column=(
            "trade_selector_canonical_schema"
        ),
        prediction_canonical_decimals_column=(
            "trade_selector_canonical_decimals"
        ),
        prediction_execution_numeric_mode_column=(
            "trade_selector_execution_numeric_mode"
        ),
        prediction_raw_execution_preserved_column=(
            "trade_selector_raw_execution_preserved"
        ),
        backtest_payload=backtest_selector,
        model_meta_payload=meta_selector,
        version_key="canonical_v2_version",
        artifact_key="production_artifact_v2_sha256",
        fingerprint_key="production_fingerprint_v2",
        canonical_contract_key="canonical_contract",
        artifact_prediction=selector_prediction,
        prediction_domain_valid=selector_domain["valid"],
        prediction_domain_rows=selector_domain["rows"],
        prediction_outside_domain_rows=selector_domain["outside_rows"],
    )
    v2_integrity_match = bool(
        model_v2_integrity["match"]
        and selector_v2_integrity["match"]
    )
    v2_eligibility_match = bool(
        v2_integrity_match
        and model_v2_integrity["policy_ready"]
        and selector_v2_integrity["policy_ready"]
    )
    promoted = bool(
        backtest.get("promoted") is True
        and model_meta.get("promoted") is True
        and backtest_selector.get("promoted") is True
        and meta_selector.get("promoted") is True
        and model_meta.get("ready") is True
        and prediction_ready
        and prediction_promoted
        and prediction_matches
        and v2_eligibility_match
    )

    if evaluation.get("stop_trading") is True:
        status_code = "NO_TRADE_GUARDRAIL"
        status_label = "停手：风控阻止交易"
        action_rows = _pending_candidates(candidates)
        for row in action_rows:
            row["action"] = "REJECT"
            row["rejection_reason"] = _text(evaluation.get("reason")) or "Decision guardrail stopped trading"
    elif not prediction_matches:
        status_code = "PENDING_AUCTION_MODEL"
        status_label = "等待竞价执行模型完成"
        action_rows = _pending_candidates(candidates)
    else:
        action_rows = _merge_auction_candidates(prediction, candidates, promoted=promoted, risk_budget=risk_budget)
        formal_count = sum(row["action"] == "BUY" for row in action_rows)
        if not promoted:
            status_code = "NO_TRADE_MODEL_NOT_PROMOTED"
            status_label = "不交易：样本外晋级未通过"
        elif formal_count == 0:
            status_code = "NO_TRADE_NO_POSITIVE_EDGE"
            status_label = "不交易：没有通过全部约束的正收益机会"
        else:
            status_code = "ACTIONABLE_BUY"
            status_label = "人工参考：按竞价上限自行挂单"

    action_rows = _ensure_relative_best_two(action_rows)
    formal_count = sum(row["action"] == "BUY" for row in action_rows)
    shadow_count = sum(row["action"] == "SHADOW_ONLY" for row in action_rows)
    stage_watchlist, stage_watch_total = _stage_watchlist(action_rows)
    industry_leaders = _limit_up_industry_leaders(
        sentiment_value("market_limit_up_industry_top10")
        or sentiment_value("market_limit_up_industry_top5"),
        limit=10,
    )
    plan = {
        "schema_version": "decision_action_plan_v12_top10_trade_selector",
        "generated_at_utc": _utc_now(),
        "report_date": chosen_date,
        "report_file": f"decision_report_{chosen_date}.md",
        "signal_date": signal_date,
        "exec_date": exec_date,
        "exit_date": exit_date,
        "status_code": status_code,
        "status_label": status_label,
        "formal_buy_count": formal_count,
        "shadow_count": shadow_count,
        "stage_watch_count": len(stage_watchlist),
        "stage_watch_eligible_count": stage_watch_total,
        "stage_watch_display_limit": OBSERVATION_TOP_N,
        "risk_budget": risk_budget,
        "guidance_only": True,
        "broker_connected": False,
        "order_execution": "manual_only",
        "model": {
            "version": _text(model_meta.get("model_version")) or _text(backtest.get("model_version")),
            "ready": model_meta.get("ready") is True,
            "promoted": promoted,
            "prediction_matches_report": prediction_matches,
            "artifact_versions_match": artifacts_match,
            "artifact_fingerprints_match": artifact_fingerprints_match,
            "artifact_sha256": meta_artifact,
            "legacy_v1_audit_only": True,
            "v2_integrity_enforced": True,
            "v2_integrity_match": v2_integrity_match,
            "v2_eligibility_match": v2_eligibility_match,
            "v2_integrity_failures": [
                *[
                    f"model.{name}"
                    for name in model_v2_integrity["failures"]
                ],
                *[
                    f"trade_selector.{name}"
                    for name in selector_v2_integrity["failures"]
                ],
                *[
                    f"trade_selector.domain.{name}"
                    for name in selector_domain["failures"]
                ],
            ],
            "canonical_v2_version": model_v2_integrity[
                "canonical_version"
            ],
            "canonical_v2_versions_match": model_v2_integrity[
                "version_match"
            ],
            "artifact_v2_fingerprints_match": model_v2_integrity[
                "fingerprints_match"
            ],
            "artifact_v2_sha256": model_v2_integrity[
                "artifact_sha256"
            ],
            "fingerprint_v2": model_v2_integrity["fingerprint_v2"],
            "fingerprint_v2_valid": model_v2_integrity[
                "fingerprint_v2_valid"
            ],
            "canonical_policy_ready": model_v2_integrity[
                "policy_ready"
            ],
            "canonical_contract": model_v2_integrity[
                "canonical_contract"
            ],
            "canonical_schema": model_v2_integrity[
                "canonical_schema"
            ],
            "canonical_contracts_match": model_v2_integrity[
                "canonical_contract_match"
            ],
            "canonical_decimals": model_v2_integrity[
                "canonical_decimals"
            ],
            "canonical_decimals_match": model_v2_integrity[
                "canonical_decimals_match"
            ],
            "execution_numeric_mode": model_v2_integrity[
                "execution_numeric_mode"
            ],
            "raw_execution_preserved": model_v2_integrity[
                "raw_execution_preserved"
            ],
            "trade_selector_artifacts_match": selector_artifacts_match,
            "trade_selector_artifact_sha256": meta_selector_artifact,
            "trade_selector_canonical_v2_version": (
                selector_v2_integrity["canonical_version"]
            ),
            "trade_selector_canonical_v2_versions_match": (
                selector_v2_integrity["version_match"]
            ),
            "trade_selector_artifacts_v2_match": (
                selector_v2_integrity["fingerprints_match"]
            ),
            "trade_selector_artifact_v2_sha256": (
                selector_v2_integrity["artifact_sha256"]
            ),
            "trade_selector_fingerprint_v2": (
                selector_v2_integrity["fingerprint_v2"]
            ),
            "trade_selector_fingerprint_v2_valid": (
                selector_v2_integrity["fingerprint_v2_valid"]
            ),
            "trade_selector_canonical_policy_ready": (
                selector_v2_integrity["policy_ready"]
            ),
            "trade_selector_canonical_contract": (
                selector_v2_integrity["canonical_contract"]
            ),
            "trade_selector_canonical_schema": (
                selector_v2_integrity["canonical_schema"]
            ),
            "trade_selector_canonical_contracts_match": (
                selector_v2_integrity["canonical_contract_match"]
            ),
            "trade_selector_canonical_decimals": (
                selector_v2_integrity["canonical_decimals"]
            ),
            "trade_selector_canonical_decimals_match": (
                selector_v2_integrity["canonical_decimals_match"]
            ),
            "trade_selector_execution_numeric_mode": (
                selector_v2_integrity["execution_numeric_mode"]
            ),
            "trade_selector_raw_execution_preserved": (
                selector_v2_integrity["raw_execution_preserved"]
            ),
            "trade_selector_prediction_domain_valid": (
                selector_domain["valid"]
            ),
            "trade_selector_prediction_domain_rows": (
                selector_domain["rows"]
            ),
            "trade_selector_prediction_outside_domain_rows": (
                selector_domain["outside_rows"]
            ),
            "trade_selector_prediction_domain_failures": list(
                selector_domain["failures"]
            ),
            "trade_selector": meta_selector,
            "promotion_failures": list(backtest.get("promotion_failures", []) or []),
            "return_model": _text((model_meta.get("return_selection", {}) or {}).get("selected")),
            "profit_model": _text(
                ((model_meta.get("classifier_selection", {}) or {}).get("profit", {}) or {}).get("selected")
            ),
            "big_loss_model": _text(
                ((model_meta.get("classifier_selection", {}) or {}).get("big_loss", {}) or {}).get("selected")
            ),
            "continuation_model": _text(
                ((model_meta.get("classifier_selection", {}) or {}).get("continuation_limit_up", {}) or {}).get("selected")
            ),
            "fill_model": _text(
                (
                    (
                        model_meta.get(
                            "classifier_selection",
                            {},
                        )
                        or {}
                    ).get("fill", {})
                    or {}
                ).get("selected")
            ),
            "exit_model": _text(
                (
                    (
                        model_meta.get(
                            "classifier_selection",
                            {},
                        )
                        or {}
                    ).get("exit_on_time", {})
                    or {}
                ).get("selected")
            ),
            "return_selection": model_meta.get("return_selection") or {},
            "probability_models": model_meta.get("classifier_selection") or {},
            "probability_quality_gate": model_meta.get(
                "probability_quality_gate"
            )
            or {},
            "selection_policy": model_meta.get("selection_policy") or {},
            "conformal_residual_quantiles": model_meta.get(
                "conformal_residual_quantiles"
            )
            or {},
            "data_coverage": model_meta.get("data_coverage") or {},
            "truth_ledgers": model_meta.get("truth_ledgers") or {},
            "continuation_feature_set": _text(
                ((model_meta.get("classifier_selection", {}) or {}).get("continuation_limit_up", {}) or {}).get("feature_set")
            ),
            "continuation_training_scope": _text(
                ((model_meta.get("classifier_selection", {}) or {}).get("continuation_limit_up", {}) or {}).get("training_scope")
            ),
            "continuation_path_ablation": (
                ((model_meta.get("classifier_selection", {}) or {}).get("continuation_limit_up", {}) or {}).get("ablation")
                or {}
            ),
            "continuation_sentiment_ablation": (
                ((model_meta.get("classifier_selection", {}) or {}).get("continuation_limit_up", {}) or {}).get("ablation")
                or {}
            ),
            "stage_recent_promotion_rate": model_meta.get("stage_recent_promotion_rate") or {},
            "continuation_stage_logit_adjustments": model_meta.get(
                "continuation_stage_logit_adjustments"
            )
            or {},
        },
        "market_sentiment": {
            "signal_date": signal_date,
            "score": _number(sentiment_value("market_sentiment_score")),
            "delta": _number(sentiment_value("market_sentiment_delta")),
            "acceleration": _number(
                sentiment_value("market_sentiment_acceleration")
            ),
            "coverage": _number(sentiment_value("market_sentiment_coverage")),
            "regime_code": _text(
                sentiment_value("market_sentiment_regime_code")
            ),
            "regime_label": _text(
                sentiment_value("market_sentiment_regime_label")
            ),
            "eligible_stock_count": _integer(
                sentiment_value("market_eligible_stock_count")
            ),
            "equal_weight_return": _number(
                sentiment_value("market_equal_weight_return")
            ),
            "up_ratio": _number(sentiment_value("market_up_ratio")),
            "down_ratio": _number(sentiment_value("market_down_ratio")),
            "limit_up_count": _integer(
                sentiment_value("market_limit_up_count")
            ),
            "limit_down_count": _integer(
                sentiment_value("market_limit_down_count")
            ),
            "touched_up_count": _integer(
                sentiment_value("market_touched_up_count")
            ),
            "failed_limit_up_count": _integer(
                sentiment_value("market_failed_limit_up_count")
            ),
            "failed_limit_up_rate": _number(
                sentiment_value("market_failed_limit_up_rate")
            ),
            "reseal_count": _integer(
                sentiment_value("market_reseal_count")
            ),
            "reseal_rate": _number(
                sentiment_value("market_reseal_rate")
            ),
            "previous_limit_up_sample": _integer(
                sentiment_value("market_prev_limit_up_sample")
            ),
            "previous_limit_up_mean_return": _number(
                sentiment_value("market_prev_limit_up_mean_return")
            ),
            "previous_limit_up_positive_rate": _number(
                sentiment_value("market_prev_limit_up_positive_rate")
            ),
            "previous_limit_up_open_gap_mean": _number(
                sentiment_value("market_prev_limit_up_open_gap_mean")
            ),
            "promotion_2_to_3_rate": _number(
                sentiment_value("market_2_to_3_promotion_rate")
            ),
            "promotion_2_to_3_samples": _integer(
                sentiment_value("market_2_to_3_promotion_samples")
            ),
            "promotion_3_to_4_rate": _number(
                sentiment_value("market_3_to_4_promotion_rate")
            ),
            "promotion_3_to_4_samples": _integer(
                sentiment_value("market_3_to_4_promotion_samples")
            ),
            "focus_promotion_rate": _number(
                sentiment_value("market_focus_promotion_rate")
            ),
            "focus_promotion_samples": _integer(
                sentiment_value("market_focus_promotion_samples")
            ),
            "max_streak": _integer(
                sentiment_value("market_max_streak")
            ),
            "industry_concentration": _number(
                sentiment_value(
                    "market_limit_up_industry_concentration"
                )
            ),
            "limit_up_amount_top3_share": _number(
                sentiment_value("market_limit_up_amount_top3_share")
            ),
            "limit_up_industry_top10": industry_leaders,
            "limit_up_industry_top5": industry_leaders[:5],
            "amount_ratio_5d": _number(
                sentiment_value("market_amount_ratio_5d")
            ),
            "breadth_score": _number(
                sentiment_value("market_sentiment_breadth_score")
            ),
            "limit_ecology_score": _number(
                sentiment_value("market_sentiment_limit_ecology_score")
            ),
            "promotion_score": _number(
                sentiment_value("market_sentiment_promotion_score")
            ),
            "profit_effect_score": _number(
                sentiment_value("market_sentiment_profit_effect_score")
            ),
            "liquidity_score": _number(
                sentiment_value("market_sentiment_liquidity_score")
            ),
        },
        "backtest": {
            key: backtest.get(key)
            for key in (
                "history_dates",
                "oos_dates",
                "signals",
                "signal_dates",
                "signal_date_ratio",
                "max_no_signal_streak",
                "filled_trades",
                "mean_trade_net_return",
                "win_rate",
                "realized_big_loss_rate",
                "tail_10pct_mean_return",
                "worst_trade_net_return",
                "stage_focus_signals",
                "stage_focus_filled_trades",
                "stage_focus_continuation_hit_rate",
                "cumulative_return",
                "max_drawdown",
                "sharpe",
                "stress_2x_cost_mean_daily_return",
                "bootstrap_probability_mean_positive",
                "exit_on_time_rate",
                "path_oos",
                "stage_focus_all",
                "top10_oos",
                "rank_bucket_oos",
                "path_shadow_policies",
                "gate_funnel",
                "shadow_policies",
                "trade_selector",
            )
        },
        "universe_eligibility": model_meta.get("universe_eligibility") or evaluation.get("universe_eligibility") or {},
        "execution_contract": {
            "objective": "D日冻结信号，指导人工在T日9:25前参与开盘集合竞价，T+1固定在9:30开盘退出，最大化扣除费用和不可成交风险后的样本外收益",
            "calendar": "严格使用上交所A股交易日历，禁止工作日或raw目录推断",
            "candidate_pool": "以D日limit_list_d确认涨停清单为权威全集，不扩展到全市场、不受旧Top50截断；正式推荐严格限定2进3、3进4，其他阶段不得进入正式买入名单",
            "streak_path": "逐板量化竞价变化、首封时点、炸板变化、换手与封单斜率，识别弱转强、强转弱、加速一致、分歧回封和持续强势",
            "market_sentiment": "只用D日及更早收盘数据，量化市场广度、涨跌停生态、涨停行业Top10、炸板回封、昨日涨停溢价、2进3/3进4真实晋级、拥挤度与流动性；仅在严格时序留出期战胜常数基线时进入模型，否则自动回退",
            "observation_ranking": "第一层每天按同一算法产生2进3和3进4观察Top10，候选少于10只时按实际数量统计、绝不补票；这一层只负责候选发现与观察顺序",
            "trade_ranking": "第二层只在第一层观察Top10内独立排序，E_ret仅用真实可成交样本训练并与P_fill分离；最多选择2只、允许0只，零交易或长期无交易不能通过晋级门槛",
            "eligible_universe": "D日已涨停且价格涨跌幅限制机制不超过10%的A股",
            "entry": "系统不下单；T日9:25前仅允许人工限价挂单，禁止无上限市价单，高于冻结上限或未成交均放弃",
            "exit": "T+1固定按9:30开盘集合竞价成交价人工退出；一字跌停无法成交时顺延至首个可成交开盘",
            "return_target": "优先使用Tushare stk_auction_o真实开盘集合竞价成交价，计算T日竞价买入到T+1固定9:30开盘退出的保守可执行净收益；不使用T+1盘中或收盘未来信息",
            "validation": "正式限价代理、全部2进3/3进4强制开盘价反事实真值、排名分层、路径分层和人工实际成交分账累计，互不覆盖；强制真值不代表真实可成交，实际可买率另行披露",
            "probability_calibration": "全部概率按交易日隔离校准并接受Brier技能审计；大跌与P_fill必须有信息增益，盈利、晋级和近乎单一标签的退出模型可安全回退常数，不得成为全局否决器",
            "policy_selection": "第二层模型拟合、概率校准、策略阈值选择使用三个依次向后的交易日窗口并设置禁运间隔，外层再做逐段前推；策略必须同时满足非零覆盖率、费用压力、可买样本收益和尾部风险",
            "return_uncertainty": "保形q10/q90用于尾部诊断；正式授权使用独立策略留出期确定的均值保守下界和保守期望，不把极端分位机械设为必须大于零",
            "risk_veto": "大跌、均值保守下界、P_fill、T+1退出、保守期望和综合分位均使用独立策略留出期阈值；无可行策略则正式不交易，但全部2进3/3进4反事实账继续验证",
            "guidance_only": True,
            "broker_connected": False,
            "no_trade_is_valid": True,
            "profit_not_guaranteed": True,
        },
        "stage_watchlist": stage_watchlist,
        "candidates": action_rows,
    }
    plan = _attach_market_close_comparison(root, plan)
    return _attach_observation_validation(root, plan)


def build_report_index(root: Path, latest_report_date: str = "") -> dict[str, Any]:
    dates = _report_dates(root)
    # Report freshness and action availability are independent truths.  A
    # historical action recovery must never move the newest report pointer
    # backwards, and a report without its dated action must not inherit the
    # unrelated action_plan_latest alias in the dashboard.
    latest = dates[0] if dates else ""
    output = root / "outputs" / "decision"
    def valid_dated_action(date: str) -> bool:
        path = output / f"action_plan_{date}.json"
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            return False
        payload = _read_json(path)
        return bool(payload) and _date(payload.get("report_date")) == date

    action_dates = {date for date in dates if valid_dated_action(date)}
    latest_action_date = next((date for date in dates if date in action_dates), "")
    reports = []
    for date in dates:
        action_available = date in action_dates
        report = {
            "report_date": date,
            "report_file": f"decision_report_{date}.md",
            "report_url": f"outputs/decision/decision_report_{date}.md",
            "eval_url": f"outputs/decision/eval_{date}.json",
            "action_available": action_available,
        }
        if action_available:
            report["action_url"] = f"outputs/decision/action_plan_{date}.json"
        reports.append(report)
    return {
        "schema_version": "decision_report_index_v2_action_truth",
        "generated_at_utc": _utc_now(),
        "latest_report_date": latest,
        "latest_report_file": f"decision_report_{latest}.md" if latest else "",
        "latest_action_report_date": latest_action_date,
        "latest_action_url": (
            f"outputs/decision/action_plan_{latest_action_date}.json"
            if latest_action_date
            else ""
        ),
        "reports": reports,
    }


def publish_action_plan(root: Path, report_date: str = "") -> tuple[Path, Path, Path, dict[str, Any]]:
    root = root.resolve()
    plan = build_action_plan(root, report_date)
    report_date = str(plan["report_date"])
    output = root / "outputs" / "decision"
    dated_path = output / f"action_plan_{report_date}.json"
    latest_path = output / "action_plan_latest.json"
    index_path = output / "report_index.json"
    _write_json(dated_path, plan)
    _write_json(latest_path, plan)
    _write_json(index_path, build_report_index(root, report_date))
    return dated_path, latest_path, index_path, plan


def refresh_action_plan_observations(
    root: Path,
    from_exec_date: str = OBSERVATION_START_EXEC_DATE,
) -> list[Path]:
    """Attach observation truth without recomputing frozen historical decisions."""
    root = root.resolve()
    output = root / "outputs" / "decision"
    threshold = _date(from_exec_date) or OBSERVATION_START_EXEC_DATE
    changed: list[Path] = []
    from top10decision.auction_v3.config import AuctionV3Config
    from top10decision.auction_v3.engine import AuctionV3Engine

    market_engine = AuctionV3Engine(AuctionV3Config(root=root))
    for path in sorted(output.glob("action_plan_20*.json")):
        if not re.fullmatch(r"action_plan_20\d{6}\.json", path.name):
            continue
        plan = _read_json(path)
        if not plan or _date(plan.get("exec_date")) < threshold:
            continue
        plan = _attach_market_close_comparison(
            root,
            plan,
            engine=market_engine,
        )
        _write_json(path, _attach_observation_validation(root, plan))
        changed.append(path)

    latest_path = output / "action_plan_latest.json"
    latest = _read_json(latest_path)
    if latest and _date(latest.get("exec_date")) >= threshold:
        latest = _attach_market_close_comparison(
            root,
            latest,
            engine=market_engine,
        )
        latest = _attach_observation_validation(root, latest)
        _write_json(latest_path, latest)
        changed.append(latest_path)
    return changed


__all__ = [
    "build_action_plan",
    "build_report_index",
    "publish_action_plan",
    "refresh_action_plan_observations",
]
