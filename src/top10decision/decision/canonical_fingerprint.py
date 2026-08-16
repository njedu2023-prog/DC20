from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from numbers import Integral, Real
from pathlib import Path
from typing import Any

import pandas as pd


CANONICAL_FINGERPRINT_SCHEMA = "dc20_canonical_fingerprint_v2"
CANONICAL_DECIMAL_PROBES = (6, 8, 10, 12)

DATE_COLUMNS = frozenset(
    {
        "signal_date",
        "buy_date",
        "target_exit_date",
        "actual_exit_date",
        "trade_date",
    }
)
CODE_COLUMNS = frozenset({"ts_code", "stock_code", "code"})
STAGE_COLUMNS = frozenset({"stage"})

EXECUTABLE_POLICY_THRESHOLD_KEYS = (
    "min_trade_score",
    "min_mean_return_lcb",
    "min_fill_probability",
    "max_big_loss_probability",
)


class CanonicalSchemaError(ValueError):
    """Raised when a requested canonical frame contract is not satisfied."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _normal_text(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value)).strip()


def normalize_date(value: Any) -> str:
    text = _normal_text(value)
    digits = "".join(character for character in text if character.isdigit())
    return digits[:8] if len(digits) >= 8 else text


def normalize_code(value: Any) -> str:
    text = _normal_text(value).upper()
    numeric = re.fullmatch(r"([0-9]+)(?:\.0+)?", text)
    if numeric:
        digits = numeric.group(1).zfill(6)[-6:]
        return f"{digits}.SH" if digits.startswith("6") else f"{digits}.SZ"
    if "." in text:
        left, right = text.split(".", 1)
        digits = "".join(character for character in left if character.isdigit())
        if digits and right in {"SH", "SZ", "BJ"}:
            return f"{digits.zfill(6)[-6:]}.{right}"
        return text
    digits = "".join(character for character in text if character.isdigit())
    if not digits:
        return text
    digits = digits.zfill(6)[-6:]
    return f"{digits}.SH" if digits.startswith("6") else f"{digits}.SZ"


def normalize_stage(value: Any) -> str:
    text = _normal_text(value).replace("进", "→").replace("->", "→")
    return re.sub(r"\s*(?:→|[-–—>])\s*", "→", text)


def _is_missing(value: Any) -> bool:
    if value is None or value is pd.NA:
        return True
    if isinstance(value, (str, bytes, bytearray)):
        return False
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return bool(result) if isinstance(result, (bool, Integral)) else False


def _coerce_float(value: Any) -> float:
    if _is_missing(value):
        return float("nan")
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in {"na", "nan", "null", "none", "<na>"}:
            return float("nan")
    return float(value)


def canonical_float_token(value: Any, *, decimals: int) -> dict[str, str]:
    if decimals < 0 or decimals > 18:
        raise ValueError("decimals must be between 0 and 18")
    try:
        number = _coerce_float(value)
    except (TypeError, ValueError, OverflowError):
        return {"$special": "invalid"}
    if math.isnan(number):
        return {"$special": "missing"}
    if math.isinf(number):
        return {"$special": "+inf" if number > 0 else "-inf"}
    quantum = Decimal(1).scaleb(-decimals)
    try:
        with localcontext() as context:
            context.prec = 50
            quantized = Decimal(str(number)).quantize(
                quantum,
                rounding=ROUND_HALF_EVEN,
            )
    except (InvalidOperation, ValueError):
        return {"$special": "invalid"}
    if quantized == 0:
        quantized = abs(quantized)
    return {"$float": format(quantized, f".{decimals}f")}


def _canonical_integer(value: Any) -> int | dict[str, str]:
    try:
        number = _coerce_float(value)
    except (TypeError, ValueError, OverflowError):
        return {"$special": "invalid"}
    if math.isnan(number):
        return {"$special": "missing"}
    if math.isinf(number):
        return {"$special": "+inf" if number > 0 else "-inf"}
    rounded = round(number)
    if not math.isclose(number, rounded, rel_tol=0.0, abs_tol=1e-9):
        return {"$special": "invalid"}
    return int(rounded)


def _is_invalid_token(value: Any) -> bool:
    return isinstance(value, Mapping) and value.get("$special") == "invalid"


def canonical_value(
    value: Any,
    *,
    decimals: int,
    kind: str = "auto",
) -> Any:
    if kind == "date":
        return {"$special": "missing"} if _is_missing(value) else normalize_date(value)
    if kind == "code":
        return {"$special": "missing"} if _is_missing(value) else normalize_code(value)
    if kind == "stage":
        return {"$special": "missing"} if _is_missing(value) else normalize_stage(value)
    if kind == "text":
        return {"$special": "missing"} if _is_missing(value) else _normal_text(value)
    if kind == "exact_text":
        return {"$special": "missing"} if _is_missing(value) else str(value)
    if kind == "integer":
        return _canonical_integer(value)
    if kind == "float":
        return canonical_float_token(value, decimals=decimals)
    if kind != "auto":
        raise ValueError(f"unsupported canonical kind: {kind}")

    if _is_missing(value):
        return {"$special": "missing"}
    if isinstance(value, bool):
        return value
    if type(value).__module__.startswith("numpy") and hasattr(value, "item"):
        return canonical_value(value.item(), decimals=decimals, kind="auto")
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        return canonical_float_token(value, decimals=decimals)
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Mapping):
        return {
            str(key): canonical_value(item, decimals=decimals, kind="auto")
            for key, item in value.items()
        }
    if isinstance(value, (set, frozenset)):
        values = [
            canonical_value(item, decimals=decimals, kind="auto")
            for item in value
        ]
        return sorted(values, key=canonical_json_bytes)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            canonical_value(item, decimals=decimals, kind="auto")
            for item in value
        ]
    return _normal_text(value)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _execution_exact_string_tokens(value: Any) -> Any:
    if isinstance(value, str):
        return {"$exact_text_utf8_hex": value.encode("utf-8").hex()}
    if isinstance(value, Path):
        return {"$exact_text_utf8_hex": value.as_posix().encode("utf-8").hex()}
    if isinstance(value, Mapping):
        return {
            str(key): _execution_exact_string_tokens(item)
            for key, item in value.items()
        }
    if isinstance(value, (set, frozenset)):
        values = [_execution_exact_string_tokens(item) for item in value]
        return sorted(values, key=canonical_json_bytes)
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return [_execution_exact_string_tokens(item) for item in value]
    return value


def canonical_mapping_sha256(
    value: Any,
    *,
    decimals: int,
    exact_strings: bool = False,
) -> str:
    source = _execution_exact_string_tokens(value) if exact_strings else value
    canonical = canonical_value(source, decimals=decimals, kind="auto")
    payload = {
        "schema": CANONICAL_FINGERPRINT_SCHEMA,
        "decimals": int(decimals),
        "value": canonical,
    }
    return _sha256_bytes(canonical_json_bytes(payload))


def column_kind(name: str, overrides: Mapping[str, str] | None = None) -> str:
    if overrides and name in overrides:
        return str(overrides[name])
    if name in DATE_COLUMNS:
        return "date"
    if name in CODE_COLUMNS:
        return "code"
    if name in STAGE_COLUMNS:
        return "stage"
    return "float"


def canonical_frame_fingerprint(
    frame: pd.DataFrame,
    columns: Sequence[str],
    *,
    decimals: int,
    kinds: Mapping[str, str] | None = None,
    strict: bool = True,
) -> dict[str, Any]:
    requested = list(dict.fromkeys(str(column) for column in columns))
    present = [column for column in requested if column in frame.columns]
    missing = [column for column in requested if column not in frame.columns]
    contract = [
        {
            "name": column,
            "kind": column_kind(column, kinds),
            "present": column in frame.columns,
        }
        for column in requested
    ]
    row_hashes: list[str] = []
    invalid_cells: list[dict[str, Any]] = []
    if present:
        for row_number, values in enumerate(
            frame[present].itertuples(index=False, name=None)
        ):
            row: list[Any] = []
            for column, value in zip(present, values):
                token = canonical_value(
                    value,
                    decimals=decimals,
                    kind=column_kind(column, kinds),
                )
                row.append(token)
                if _is_invalid_token(token):
                    invalid_cells.append(
                        {"row": int(row_number), "column": column}
                    )
            row_hashes.append(_sha256_bytes(canonical_json_bytes(row)))
    else:
        row_hashes = [_sha256_bytes(b"[]") for _ in range(len(frame))]
    row_hashes.sort()
    header = {
        "schema": CANONICAL_FINGERPRINT_SCHEMA,
        "decimals": int(decimals),
        "rows": int(len(frame)),
        "columns": contract,
    }
    digest = hashlib.sha256(canonical_json_bytes(header))
    digest.update(b"\n")
    for row_hash in row_hashes:
        digest.update(row_hash.encode("ascii"))
        digest.update(b"\n")
    result = {
        "sha256": digest.hexdigest(),
        "rows": int(len(frame)),
        "requested_columns": requested,
        "present_columns": present,
        "missing_columns": missing,
        "invalid_cell_count": int(len(invalid_cells)),
        "invalid_cell_sample": invalid_cells[:20],
        "valid": not missing and not invalid_cells,
        "row_order_independent": True,
        "decimals": int(decimals),
    }
    if strict and not result["valid"]:
        raise CanonicalSchemaError(
            "canonical frame contract failed: "
            f"missing_columns={missing!r}, "
            f"invalid_cell_count={len(invalid_cells)}"
        )
    return result


def executable_policy_projection(policy: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(policy or {})
    thresholds = dict(source.get("thresholds") or {})
    return {
        "version": source.get("version", ""),
        "ready": source.get("ready") is True,
        "reason": source.get("reason", ""),
        "max_positions": source.get("max_positions"),
        "tail_risk_weight": source.get("tail_risk_weight"),
        "thresholds": {
            name: thresholds.get(name)
            for name in EXECUTABLE_POLICY_THRESHOLD_KEYS
        },
    }


def canonical_policy_fingerprint(
    policy: Mapping[str, Any] | None,
    *,
    decimals: int,
) -> dict[str, Any]:
    projection = executable_policy_projection(policy)
    return {
        "sha256": canonical_mapping_sha256(
            projection,
            decimals=decimals,
            exact_strings=True,
        ),
        "projection": canonical_value(
            projection,
            decimals=decimals,
            kind="auto",
        ),
        "excluded_diagnostics": (
            "metrics",
            "checks",
            "evaluated_policies",
            "feasible_policies",
        ),
        "decimals": int(decimals),
    }


def compose_artifact_fingerprint(
    *,
    artifact_kind: str,
    provenance_sha256: str,
    semantic_sha256: str,
    policy_sha256: str = "",
    decimals: int,
) -> str:
    payload = {
        "schema": CANONICAL_FINGERPRINT_SCHEMA,
        "artifact_kind": str(artifact_kind),
        "decimals": int(decimals),
        "provenance_sha256": str(provenance_sha256),
        "semantic_sha256": str(semantic_sha256),
        "policy_sha256": str(policy_sha256),
    }
    return _sha256_bytes(canonical_json_bytes(payload))


__all__ = [
    "CANONICAL_DECIMAL_PROBES",
    "CANONICAL_FINGERPRINT_SCHEMA",
    "canonical_float_token",
    "canonical_frame_fingerprint",
    "canonical_mapping_sha256",
    "canonical_policy_fingerprint",
    "canonical_value",
    "compose_artifact_fingerprint",
    "executable_policy_projection",
    "normalize_code",
    "normalize_date",
    "normalize_stage",
]
