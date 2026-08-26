#!/usr/bin/env python3
"""Compare persisted and independently replayed Decision action semantics.

This diagnostic is deliberately fail-closed.  It applies the repository's
reviewed V3 projection to both action plans, reports JSON-Pointer differences,
and exits non-zero whenever the exact canonical projections differ.  The q8
flag is evidence only; it never changes the comparison result or any writer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any

Q8 = Decimal("0.00000001")
MISSING = object()


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _load_action(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"action path is not a regular file: {path}")
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_constant,
    )
    if not isinstance(value, dict):
        raise ValueError("action payload must be an object")
    generated_at = value.pop("generated_at_utc", None)
    if type(generated_at) is not str:
        raise ValueError("action timestamp is invalid")
    try:
        generated_time = datetime.fromisoformat(generated_at)
    except ValueError as exc:
        raise ValueError("action timestamp is invalid") from exc
    if (
        generated_time.isoformat() != generated_at
        or generated_time.utcoffset() != timedelta(0)
        or generated_time.microsecond != 0
    ):
        raise ValueError("action timestamp is invalid")
    return value


def _escape_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _scalar_token(value: Any) -> Any:
    if value is MISSING:
        return {"missing": True}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        if len(encoded) <= 160:
            return value
        return {
            "bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }
    encoded = _canonical_json_bytes(value)
    return {
        "bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _type_name(value: Any) -> str:
    if value is MISSING:
        return "missing"
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    if type(value) is str:
        return "string"
    if type(value) is list:
        return "array"
    if type(value) is dict:
        return "object"
    return type(value).__name__


def _q8(value: Any) -> str | None:
    if type(value) not in {int, float}:
        return None
    if type(value) is float and not math.isfinite(value):
        return None
    try:
        return format(Decimal(str(value)).quantize(Q8, rounding=ROUND_HALF_EVEN), "f")
    except (InvalidOperation, ValueError):
        return None


def _subtree(pointer: str) -> str:
    if not pointer or pointer == "/":
        return "root"
    return pointer.split("/", 2)[1].replace("~1", "/").replace("~0", "~")


def _difference(pointer: str, left: Any, right: Any, kind: str) -> dict[str, Any]:
    record: dict[str, Any] = {
        "pointer": pointer or "/",
        "subtree": _subtree(pointer),
        "kind": kind,
        "persisted_type": _type_name(left),
        "replayed_type": _type_name(right),
        "persisted": _scalar_token(left),
        "replayed": _scalar_token(right),
    }
    left_q8 = _q8(left)
    right_q8 = _q8(right)
    if left_q8 is not None and right_q8 is not None:
        record["persisted_q8"] = left_q8
        record["replayed_q8"] = right_q8
        record["q8_equal"] = left_q8 == right_q8
        record["absolute_delta"] = format(
            abs(Decimal(str(left)) - Decimal(str(right))), "f"
        )
    else:
        record["q8_equal"] = False
    return record


def _deep_differences(left: Any, right: Any, pointer: str = "") -> list[dict[str, Any]]:
    if type(left) is not type(right):
        return [_difference(pointer, left, right, "type")]
    if isinstance(left, dict):
        differences: list[dict[str, Any]] = []
        for key in sorted(set(left) | set(right)):
            child = f"{pointer}/{_escape_pointer(key)}"
            if key not in left:
                differences.append(_difference(child, MISSING, right[key], "missing_persisted"))
            elif key not in right:
                differences.append(_difference(child, left[key], MISSING, "missing_replayed"))
            else:
                differences.extend(_deep_differences(left[key], right[key], child))
        return differences
    if isinstance(left, list):
        differences = []
        width = max(len(left), len(right))
        for index in range(width):
            child = f"{pointer}/{index}"
            if index >= len(left):
                differences.append(_difference(child, MISSING, right[index], "missing_persisted"))
            elif index >= len(right):
                differences.append(_difference(child, left[index], MISSING, "missing_replayed"))
            else:
                differences.extend(_deep_differences(left[index], right[index], child))
        return differences
    if left != right:
        return [_difference(pointer, left, right, "value")]
    return []


def _subtree_hashes(projection: dict[str, Any]) -> dict[str, str]:
    return {
        key: hashlib.sha256(_canonical_json_bytes(value)).hexdigest()
        for key, value in sorted(projection.items())
    }


def compare(persisted_path: Path, replayed_path: Path) -> dict[str, Any]:
    from top10decision.decision.action_plan import (
        NATIVE_NO_TRADE_COMPARISON_PROFILE_V3,
        RETROSPECTIVE_REPLAY_COMPARISON_PROFILE_V3,
        action_plan_semantic_comparison_profile_v3,
        action_plan_semantic_projection_v3,
    )
    from top10decision.decision.canonical_fingerprint import canonical_json_bytes

    persisted = _load_action(persisted_path)
    replayed = _load_action(replayed_path)
    persisted_profile = action_plan_semantic_comparison_profile_v3(persisted)
    replayed_profile = action_plan_semantic_comparison_profile_v3(replayed)
    if replayed_profile == persisted_profile:
        profile_relation = "identical"
    elif (
        persisted_profile == RETROSPECTIVE_REPLAY_COMPARISON_PROFILE_V3
        and replayed_profile == NATIVE_NO_TRADE_COMPARISON_PROFILE_V3
    ):
        profile_relation = "retrospective_persisted_vs_native_replay"
    else:
        raise ValueError("replayed comparison profile drifted")
    persisted_projection = action_plan_semantic_projection_v3(
        persisted, comparison_profile=persisted_profile
    )
    replayed_projection = action_plan_semantic_projection_v3(
        replayed, comparison_profile=persisted_profile
    )
    persisted_bytes = canonical_json_bytes(persisted_projection)
    replayed_bytes = canonical_json_bytes(replayed_projection)
    differences = _deep_differences(persisted_projection, replayed_projection)
    q8_equal = sum(1 for item in differences if item.get("q8_equal") is True)
    return {
        "schema_version": "decision_action_semantic_deep_diff_v1",
        "status": "pass" if not differences else "fail",
        "comparison_profile": persisted_profile,
        "replayed_profile": replayed_profile,
        "profile_relation": profile_relation,
        "persisted_semantic_sha256": hashlib.sha256(persisted_bytes).hexdigest(),
        "replayed_semantic_sha256": hashlib.sha256(replayed_bytes).hexdigest(),
        "persisted_subtree_sha256": _subtree_hashes(persisted_projection),
        "replayed_subtree_sha256": _subtree_hashes(replayed_projection),
        "difference_count": len(differences),
        "q8_equal_numeric_difference_count": q8_equal,
        "non_q8_difference_count": len(differences) - q8_equal,
        "differences": differences,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--persisted", required=True)
    parser.add_argument("--replayed", required=True)
    parser.add_argument("--report", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report_path = Path(args.report)
    try:
        payload = compare(Path(args.persisted), Path(args.replayed))
    except Exception as exc:
        payload = {
            "schema_version": "decision_action_semantic_deep_diff_v1",
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    report_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if payload.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
