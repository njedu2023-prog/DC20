from __future__ import annotations

import copy
import csv
import fcntl
import hashlib
import io
import json
import math
import os
import re
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from top10decision.decision.three_rank import (
    top10_members_sha256,
    validate_three_rank_contract,
)


CONTRACT_ID = "dc20_executable_profit_research_projection_20260825_v2"
CONTRACT_PATH = Path(
    "models/decision_executable_profit_research_projection_contract.json"
)
DISPLAY_NAME = "可实现盈利概率排序（模型估计·未校准）"
PROJECTION_SCHEMA = "dc20_executable_profit_public_research_projection_v2"
PROJECTION_KIND = "immutable_d_frozen_executable_profit_research_projection"
STATISTICS_SCHEMA = "dc20_executable_profit_public_shadow_statistics_v1"
STATISTICS_KIND = "immutable_asof_executable_profit_shadow_statistics_projection"
INDEX_SCHEMA = "dc20_executable_profit_public_research_index_v1"
INDEX_KIND = "dated_executable_profit_research_pointer_only"
OUTPUT_ROOT = Path("outputs/decision/executable_profit_research")
SELECTION_ROOT = Path("data/decision_executable_profit/forward/selections")
VERIFICATION_ROOT = Path("data/decision_executable_profit/forward/verifications")
SETTLEMENT_ROOT = Path("data/decision_executable_profit/forward/settlements")
SOURCE_STATISTICS_PATH = Path(
    "data/decision_executable_profit/forward/statistics/summary.json"
)
MINIMUM_SIGNAL_DATE = "20260824"
DATE_RE = re.compile(r"20\d{6}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")

PUBLIC_BOUNDARIES = {
    "public_research_projection_allowed": True,
    "estimated_probability_display_allowed": True,
    "estimated_probability_calibrated": False,
    "formal_probability_allowed": False,
    "formal_rank_allowed": False,
    "official_trade_action_allowed": False,
    "may_create_trade_action": False,
    "broker_or_order_integration_allowed": False,
    "actual_execution_claimed": False,
    "human_decision_support_only": True,
    "proxy_scores_uncalibrated": True,
    "may_change_promotion_membership_or_rank": False,
}


class ExecutableProfitResearchProjectionError(RuntimeError):
    """Raised when a public research projection cannot prove exact lineage."""


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise ExecutableProfitResearchProjectionError(message)


def _normal_date(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _normal_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text if re.fullmatch(r"\d{6}\.(?:SH|SZ)", text) else ""


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            _json_safe(value),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _source_statistics_input_files_sha256(value: Any) -> str:
    # The settlement subsystem intentionally includes a trailing newline in
    # its canonical JSON byte contract. Keep that source identity exact here.
    return hashlib.sha256(_canonical_json_bytes(value) + b"\n").hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExecutableProfitResearchProjectionError(
            f"invalid {label}: {path}"
        ) from exc
    _expect(isinstance(value, dict), f"{label} must be a JSON object")
    return value


def _safe_existing_file(root: Path, relative: Path, *, label: str) -> Path:
    root = root.resolve(strict=True)
    _expect(
        not relative.is_absolute() and ".." not in relative.parts,
        f"unsafe {label} path",
    )
    current = root
    for part in relative.parts:
        current = current / part
        _expect(not current.is_symlink(), f"{label} has a symlink ancestor")
    _expect(current.is_file(), f"{label} is missing")
    _expect(
        current.resolve(strict=True).is_relative_to(root),
        f"{label} escaped repository",
    )
    return current


def _ensure_directory(root: Path, relative: Path) -> Path:
    root = root.resolve(strict=True)
    _expect(
        not relative.is_absolute() and ".." not in relative.parts,
        "unsafe public output path",
    )
    current = root
    for part in relative.parts:
        current = current / part
        if current.exists():
            _expect(
                current.is_dir() and not current.is_symlink(),
                "public output has a symlink ancestor",
            )
        else:
            current.mkdir()
    return current


def _payload_snapshot(payload: Mapping[str, Any]) -> str:
    copied = copy.deepcopy(dict(payload))
    copied.pop("snapshot_sha256", None)
    copied.pop("downloads", None)
    return _canonical_sha256(copied)


def _load_contract(repo_root: Path) -> tuple[Path, dict[str, Any]]:
    path = _safe_existing_file(
        repo_root,
        CONTRACT_PATH,
        label="public research projection contract",
    )
    contract = _read_json(path, label="public research projection contract")
    _expect(
        contract.get("schema_version")
        == "dc20_executable_profit_research_projection_contract_v2"
        and contract.get("contract_id") == CONTRACT_ID
        and contract.get("status") == "PUBLIC_RESEARCH_PROJECTION_ALLOWED"
        and contract.get("display_name") == DISPLAY_NAME,
        "public research projection contract identity drifted",
    )
    authority = contract.get("authority")
    _expect(
        authority
        == {
            "repository": "njedu2023-prog/DC20",
            "branch": "main",
            "runtime_dependency_on_codex": False,
            "runtime_dependency_on_top10_decision": False,
        },
        "public research projection authority drifted",
    )
    inputs = contract.get("inputs")
    _expect(
        inputs
        == {
            "selection_pattern": (
                "data/decision_executable_profit/forward/selections/"
                "shadow_<D>.json"
            ),
            "three_rank_pattern": "outputs/decision/three_rank_top10_<D>.json",
            "optional_t_verification_pattern": (
                "data/decision_executable_profit/forward/verifications/"
                "t_verification_<D>.json"
            ),
            "optional_t1_settlement_pattern": (
                "data/decision_executable_profit/forward/settlements/"
                "settlement_<D>.json"
            ),
            "optional_statistics_path": SOURCE_STATISTICS_PATH.as_posix(),
            "model_artifact_or_pickle_allowed": False,
            "network_input_allowed": False,
            "legacy_p_fill_ledger_allowed": False,
            "exact_file_sha256_required": True,
        },
        "public research projection input boundary drifted",
    )
    ranking = contract.get("ranking")
    _expect(
        ranking
        == {
            "source_order": "immutable selection internal_shadow_order",
            "recompute_or_rerank_allowed": False,
            "candidate_scope": (
                "exact frozen promotion TopN in hard 2-to-3/3-to-4 scope"
            ),
            "candidate_count_rule": (
                "N may be 0 through 10; show exactly N and never pad"
            ),
            "shadow_slot_rule": "actual_slots = min(2, N); never pad Top2",
            "promotion_rank_is_independent": True,
            "shadow_price_use": (
                "D-frozen research price cap only; not a buy instruction"
            ),
            "score_kind": (
                "uncalibrated model-estimated executable-profit probability"
            ),
            "estimated_probability_field": (
                "estimated_executable_profit_probability"
            ),
            "estimated_probability_display_allowed": True,
            "estimated_probability_calibrated": False,
            "formal_probability_claim_allowed": False,
        },
        "public research projection ranking boundary drifted",
    )
    _expect(
        contract.get("outputs")
        == {
            "projection_json": (
                "outputs/decision/executable_profit_research/projection_<D>.json"
            ),
            "projection_csv": (
                "outputs/decision/executable_profit_research/projection_<D>.csv"
            ),
            "statistics_json": (
                "outputs/decision/executable_profit_research/"
                "shadow_statistics_<D>_asof_<A>.json"
            ),
            "index_json": "outputs/decision/executable_profit_research/index.json",
            "dated_projection_is_immutable": True,
            "statistics_asof_is_immutable": True,
            "same_date_different_payload": "REJECT",
            "out_of_order_pointer": "REJECT",
        },
        "public research projection output contract drifted",
    )
    _expect(
        contract.get("boundaries") == PUBLIC_BOUNDARIES,
        "public research projection safety boundaries drifted",
    )
    return path, contract


def _reject_legacy_selection_injection(selection: Mapping[str, Any]) -> None:
    forbidden = {
        "p_fill_shadow_probability",
        "p_fill_shadow_status",
        "p_fill_ledger",
        "legacy_p_fill_ledger",
        "legacy_profit_ledger",
        "model_pickle_path",
        "joblib_path",
    }
    rows = selection.get("rows")
    _expect(isinstance(rows, list), "selection rows missing")
    for row in rows:
        _expect(isinstance(row, Mapping), "selection row is invalid")
        injected = forbidden.intersection(str(key) for key in row)
        _expect(not injected, "legacy/model ledger fields were injected into selection")
    injected = forbidden.intersection(str(key) for key in selection)
    _expect(not injected, "legacy/model ledger was injected into selection")


def _load_selection(
    repo_root: Path,
    signal_date: str,
) -> tuple[Path, Path, dict[str, Any]]:
    relative = SELECTION_ROOT / f"shadow_{signal_date}.json"
    path = _safe_existing_file(repo_root, relative, label="immutable Shadow selection")
    selection = _read_json(path, label="immutable Shadow selection")
    _reject_legacy_selection_injection(selection)
    rows = selection.get("rows")
    _expect(isinstance(rows, list) and len(rows) <= 10, "selection row count invalid")
    try:
        from top10decision.decision.executable_profit_shadow import (
            validate_internal_forward_shadow_payload,
        )

        validate_internal_forward_shadow_payload(
            selection,
            require_downloads=True,
        )
    except Exception as exc:
        raise ExecutableProfitResearchProjectionError(
            "immutable Shadow selection contract is invalid"
        ) from exc
    _expect(
        selection.get("signal_date") == signal_date,
        "selection filename/date binding drifted",
    )
    downloads = selection.get("downloads")
    _expect(isinstance(downloads, Mapping), "selection downloads missing")
    csv_relative = SELECTION_ROOT / f"shadow_{signal_date}.csv"
    _expect(
        downloads.get("json_url") == relative.as_posix()
        and downloads.get("csv_url") == csv_relative.as_posix()
        and downloads.get("row_count") == len(rows),
        "selection dated downloads drifted",
    )
    csv_path = _safe_existing_file(
        repo_root,
        csv_relative,
        label="immutable Shadow selection CSV",
    )
    _expect(
        downloads.get("csv_sha256") == _sha256(csv_path),
        "selection CSV SHA drifted",
    )
    return path, csv_path, selection


def _load_three_rank(
    repo_root: Path,
    signal_date: str,
) -> tuple[Path, Path, dict[str, Any]]:
    relative = Path(f"outputs/decision/three_rank_top10_{signal_date}.json")
    path = _safe_existing_file(repo_root, relative, label="exact dated three-rank contract")
    contract = _read_json(path, label="exact dated three-rank contract")
    try:
        validate_three_rank_contract(contract)
    except Exception as exc:
        raise ExecutableProfitResearchProjectionError(
            "exact dated three-rank contract is invalid"
        ) from exc
    _expect(
        contract.get("signal_date") == signal_date,
        "three-rank filename/date binding drifted",
    )
    downloads = contract.get("downloads")
    csv_relative = Path(f"outputs/decision/three_rank_top10_{signal_date}.csv")
    _expect(
        isinstance(downloads, Mapping)
        and downloads.get("json_url") == relative.as_posix()
        and downloads.get("csv_url") == csv_relative.as_posix()
        and downloads.get("row_count") == len(contract.get("rows") or []),
        "three-rank dated downloads drifted",
    )
    csv_path = _safe_existing_file(
        repo_root,
        csv_relative,
        label="exact dated three-rank CSV",
    )
    _expect(
        downloads.get("csv_sha256") == _sha256(csv_path),
        "three-rank CSV SHA drifted",
    )
    return path, csv_path, contract


def _same_number(left: Any, right: Any) -> bool:
    left_number = _finite(left)
    right_number = _finite(right)
    return (
        left_number is not None
        and right_number is not None
        and math.isclose(left_number, right_number, rel_tol=0.0, abs_tol=1e-15)
    )


def _bind_selection_to_three_rank(
    selection: Mapping[str, Any],
    three_rank: Mapping[str, Any],
) -> None:
    for field in ("signal_date", "exec_date", "exit_date"):
        _expect(
            selection.get(field) == three_rank.get(field),
            f"selection/three-rank {field} drifted",
        )
    selection_rows = selection.get("rows")
    three_rows = three_rank.get("rows")
    _expect(
        isinstance(selection_rows, list)
        and isinstance(three_rows, list)
        and len(selection_rows) == len(three_rows)
        and 0 <= len(selection_rows) <= 10,
        "selection/three-rank candidate count drifted",
    )
    signal_date = str(selection["signal_date"])
    selection_codes = [_normal_code(row.get("ts_code")) for row in selection_rows]
    three_codes = [_normal_code(row.get("ts_code")) for row in three_rows]
    expected_members = top10_members_sha256(signal_date, selection_codes)
    _expect(
        all(selection_codes)
        and len(set(selection_codes)) == len(selection_codes)
        and set(selection_codes) == set(three_codes),
        "selection/three-rank membership drifted",
    )
    _expect(
        selection.get("top10_members_sha256")
        == three_rank.get("top10_members_sha256")
        == expected_members,
        "selection/three-rank member SHA drifted",
    )
    source = selection.get("source_promotion")
    _expect(
        isinstance(source, Mapping)
        and source.get("source_bundle_sha256") == three_rank.get("bundle_sha256")
        and source.get("source_feature_snapshot_sha256")
        == three_rank.get("feature_snapshot_sha256")
        and source.get("source_top10_members_sha256") == expected_members,
        "selection no longer binds exact three-rank bundle",
    )
    by_code = {str(row["ts_code"]): row for row in three_rows}
    for row in selection_rows:
        source_row = by_code.get(str(row.get("ts_code") or ""))
        _expect(source_row is not None, "selection contains a foreign member")
        _expect(
            row.get("name") == source_row.get("name")
            and row.get("industry") == source_row.get("industry")
            and row.get("stage_transition") == source_row.get("stage_transition")
            and row.get("promotion_rank") == source_row.get("promotion_rank")
            and _same_number(
                row.get("predicted_promotion_probability"),
                source_row.get("predicted_promotion_probability"),
            ),
            "selection changed exact promotion row identity or rank",
        )


def _source_binding(
    repo_root: Path,
    selection_path: Path,
    selection_csv_path: Path,
    selection: Mapping[str, Any],
    three_rank_path: Path,
    three_rank_csv_path: Path,
    three_rank: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "selection": {
            "json_path": selection_path.relative_to(repo_root).as_posix(),
            "json_sha256": _sha256(selection_path),
            "csv_path": selection_csv_path.relative_to(repo_root).as_posix(),
            "csv_sha256": _sha256(selection_csv_path),
            "snapshot_sha256": selection["snapshot_sha256"],
            "d_feature_file_sha256": selection["source_d_feature"][
                "file_sha256"
            ],
        },
        "three_rank": {
            "json_path": three_rank_path.relative_to(repo_root).as_posix(),
            "json_sha256": _sha256(three_rank_path),
            "csv_path": three_rank_csv_path.relative_to(repo_root).as_posix(),
            "csv_sha256": _sha256(three_rank_csv_path),
            "bundle_sha256": three_rank["bundle_sha256"],
            "feature_snapshot_sha256": three_rank["feature_snapshot_sha256"],
        },
    }


def build_research_projection(
    repo_root: Path,
    signal_date: str,
) -> dict[str, Any]:
    """Project a frozen selection without loading a model or changing its order."""

    repo_root = repo_root.resolve(strict=True)
    signal_date = _normal_date(signal_date)
    _expect(
        DATE_RE.fullmatch(signal_date) is not None
        and signal_date >= MINIMUM_SIGNAL_DATE,
        "public research signal date is invalid",
    )
    contract_path, _ = _load_contract(repo_root)
    selection_path, selection_csv_path, selection = _load_selection(
        repo_root,
        signal_date,
    )
    three_path, three_csv_path, three_rank = _load_three_rank(
        repo_root,
        signal_date,
    )
    _bind_selection_to_three_rank(selection, three_rank)
    source_binding = _source_binding(
        repo_root,
        selection_path,
        selection_csv_path,
        selection,
        three_path,
        three_csv_path,
        three_rank,
    )
    rows: list[dict[str, Any]] = []
    selection_rows = selection.get("rows")
    _expect(isinstance(selection_rows, list), "selection rows missing")
    for frozen in selection_rows:
        rows.append(
            {
                "ts_code": str(frozen["ts_code"]),
                "name": str(frozen["name"]),
                "industry": str(frozen["industry"]),
                "stage_transition": str(frozen["stage_transition"]),
                "promotion_rank": int(frozen["promotion_rank"]),
                "predicted_promotion_probability": float(
                    frozen["predicted_promotion_probability"]
                ),
                "executable_profit_research_rank": int(
                    frozen["internal_shadow_order"]
                ),
                "research_joint_proxy_score": float(
                    frozen["research_joint_proxy_score"]
                ),
                "estimated_executable_profit_probability": float(
                    frozen["research_joint_proxy_score"]
                ),
                "research_fill_proxy_score": float(
                    frozen["research_fill_proxy_score"]
                ),
                "research_conditional_profit_score": float(
                    frozen["research_conditional_profit_score"]
                ),
                "shadow_selected": bool(frozen["internal_shadow_selected"]),
                "shadow_slot": frozen["shadow_slot"],
                "shadow_max_price": float(frozen["shadow_max_price"]),
                "shadow_price_basis": str(frozen["shadow_price_basis"]),
                "shadow_price_source_sha256": str(
                    frozen["shadow_price_source_sha256"]
                ),
            }
        )
    payload: dict[str, Any] = {
        "schema_version": PROJECTION_SCHEMA,
        "artifact_kind": PROJECTION_KIND,
        "contract_id": CONTRACT_ID,
        "contract_file_sha256": _sha256(contract_path),
        "status": "PUBLIC_RESEARCH_ONLY_NOT_FORMAL",
        "display_name": DISPLAY_NAME,
        "signal_date": signal_date,
        "exec_date": str(selection["exec_date"]),
        "exit_date": str(selection["exit_date"]),
        "candidate_count": len(rows),
        "top10_members_sha256": str(selection["top10_members_sha256"]),
        "source_bundle_sha256": str(three_rank["bundle_sha256"]),
        "source_bindings": source_binding,
        "ranking_contract": {
            "source_order": "immutable selection internal_shadow_order",
            "visible_rank_field": "executable_profit_research_rank",
            "estimated_probability_field": (
                "estimated_executable_profit_probability"
            ),
            "score_label": "模型估计可实现盈利概率（未校准）",
            "estimated_probability_calibrated": False,
            "recomputed_or_reranked": False,
            "candidate_count_rule": "show exactly N for 0<=N<=10; never pad",
            "shadow_requested_slots": 2,
            "shadow_actual_slots": min(2, len(rows)),
            "shadow_slot_rule": "min(2, N); no padding",
            "promotion_rank_is_independent": True,
            "shadow_price_use": (
                "D-frozen research price cap only; not a buy instruction"
            ),
        },
        "rows": rows,
        "boundaries": dict(PUBLIC_BOUNDARIES),
    }
    payload["snapshot_sha256"] = _payload_snapshot(payload)
    validate_research_projection(payload)
    return payload


PROJECTION_ROW_KEYS = {
    "ts_code",
    "name",
    "industry",
    "stage_transition",
    "promotion_rank",
    "predicted_promotion_probability",
    "executable_profit_research_rank",
    "estimated_executable_profit_probability",
    "research_joint_proxy_score",
    "research_fill_proxy_score",
    "research_conditional_profit_score",
    "shadow_selected",
    "shadow_slot",
    "shadow_max_price",
    "shadow_price_basis",
    "shadow_price_source_sha256",
}


def validate_research_projection(
    payload: Mapping[str, Any],
    *,
    require_downloads: bool = False,
) -> None:
    expected = {
        "schema_version",
        "artifact_kind",
        "contract_id",
        "contract_file_sha256",
        "status",
        "display_name",
        "signal_date",
        "exec_date",
        "exit_date",
        "candidate_count",
        "top10_members_sha256",
        "source_bundle_sha256",
        "source_bindings",
        "ranking_contract",
        "rows",
        "boundaries",
        "snapshot_sha256",
    }
    if require_downloads or "downloads" in payload:
        expected.add("downloads")
    _expect(set(payload) == expected, "public research projection surface drifted")
    _expect(
        payload.get("schema_version") == PROJECTION_SCHEMA
        and payload.get("artifact_kind") == PROJECTION_KIND
        and payload.get("contract_id") == CONTRACT_ID
        and payload.get("status") == "PUBLIC_RESEARCH_ONLY_NOT_FORMAL"
        and payload.get("display_name") == DISPLAY_NAME,
        "public research projection identity drifted",
    )
    signal_date = _normal_date(payload.get("signal_date"))
    exec_date = _normal_date(payload.get("exec_date"))
    exit_date = _normal_date(payload.get("exit_date"))
    _expect(
        signal_date >= MINIMUM_SIGNAL_DATE
        and payload.get("signal_date") == signal_date
        and payload.get("exec_date") == exec_date
        and payload.get("exit_date") == exit_date
        and signal_date < exec_date < exit_date,
        "public research projection dates invalid",
    )
    for key in (
        "contract_file_sha256",
        "top10_members_sha256",
        "source_bundle_sha256",
        "snapshot_sha256",
    ):
        _expect(
            SHA256_RE.fullmatch(str(payload.get(key) or "")) is not None,
            f"public research projection {key} invalid",
        )
    rows = payload.get("rows")
    _expect(
        isinstance(rows, list)
        and 0 <= len(rows) <= 10
        and payload.get("candidate_count") == len(rows),
        "public research projection candidate count invalid",
    )
    codes: list[str] = []
    promotion_ranks: list[int] = []
    for index, row in enumerate(rows, start=1):
        _expect(
            isinstance(row, Mapping) and set(row) == PROJECTION_ROW_KEYS,
            "public research projection row surface drifted",
        )
        code = _normal_code(row.get("ts_code"))
        _expect(code, "public research projection code invalid")
        codes.append(code)
        _expect(
            row.get("stage_transition") in {"2→3", "3→4"},
            "public research projection escaped hard stage scope",
        )
        _expect(
            type(row.get("promotion_rank")) is int
            and type(row.get("executable_profit_research_rank")) is int,
            "public research projection rank type invalid",
        )
        promotion_ranks.append(int(row["promotion_rank"]))
        _expect(
            row.get("executable_profit_research_rank") == index,
            "public research projection changed immutable research order",
        )
        for field in (
            "predicted_promotion_probability",
            "estimated_executable_profit_probability",
            "research_joint_proxy_score",
            "research_fill_proxy_score",
            "research_conditional_profit_score",
        ):
            score = _finite(row.get(field))
            _expect(
                score is not None and 0.0 <= score <= 1.0,
                f"public research projection {field} invalid",
            )
        joint = float(row["research_joint_proxy_score"])
        estimated_probability = float(
            row["estimated_executable_profit_probability"]
        )
        fill = float(row["research_fill_proxy_score"])
        conditional = float(row["research_conditional_profit_score"])
        _expect(
            math.isclose(joint, fill * conditional, rel_tol=0.0, abs_tol=1e-15),
            "public research projection lost exact joint proxy identity",
        )
        _expect(
            math.isclose(
                estimated_probability,
                joint,
                rel_tol=0.0,
                abs_tol=1e-15,
            ),
            "model-estimated executable-profit probability drifted from frozen joint score",
        )
        selected = index <= min(2, len(rows))
        _expect(
            row.get("shadow_selected") is selected
            and row.get("shadow_slot") == (index if selected else None),
            "public research projection padded or changed Shadow slots",
        )
        shadow_max_price = _finite(row.get("shadow_max_price"))
        _expect(
            shadow_max_price is not None
            and shadow_max_price > 0.0
            and math.isclose(
                shadow_max_price * 100.0,
                round(shadow_max_price * 100.0),
                rel_tol=0.0,
                abs_tol=1e-7,
            )
            and row.get("shadow_price_basis")
            in {
                "D_FROZEN_RECOMMENDED_MAX_PRICE",
                "D_FROZEN_OBSERVATION_MAX_PRICE",
                "D_ONLY_MODEL_DIAGNOSTIC_CAP",
                "D_CLOSE_CONSERVATIVE_CAP",
            }
            and SHA256_RE.fullmatch(
                str(row.get("shadow_price_source_sha256") or "")
            )
            is not None,
            "public research projection D-frozen price cap invalid",
        )
    _expect(
        len(set(codes)) == len(codes)
        and payload.get("top10_members_sha256")
        == top10_members_sha256(signal_date, codes),
        "public research projection membership hash invalid",
    )
    _expect(
        sorted(promotion_ranks) == list(range(1, len(rows) + 1)),
        "public research projection promotion ranks are not independent 1..N",
    )
    ranking = payload.get("ranking_contract")
    _expect(
        ranking
        == {
            "source_order": "immutable selection internal_shadow_order",
            "visible_rank_field": "executable_profit_research_rank",
            "estimated_probability_field": (
                "estimated_executable_profit_probability"
            ),
            "score_label": "模型估计可实现盈利概率（未校准）",
            "estimated_probability_calibrated": False,
            "recomputed_or_reranked": False,
            "candidate_count_rule": "show exactly N for 0<=N<=10; never pad",
            "shadow_requested_slots": 2,
            "shadow_actual_slots": min(2, len(rows)),
            "shadow_slot_rule": "min(2, N); no padding",
            "promotion_rank_is_independent": True,
            "shadow_price_use": (
                "D-frozen research price cap only; not a buy instruction"
            ),
        },
        "public research projection ranking contract drifted",
    )
    _expect(
        payload.get("boundaries") == PUBLIC_BOUNDARIES,
        "public research projection safety boundaries drifted",
    )
    bindings = payload.get("source_bindings")
    _expect(
        isinstance(bindings, Mapping)
        and set(bindings) == {"selection", "three_rank"},
        "public research projection bindings missing",
    )
    selection = bindings.get("selection")
    three_rank = bindings.get("three_rank")
    _expect(
        isinstance(selection, Mapping)
        and set(selection)
        == {
            "json_path",
            "json_sha256",
            "csv_path",
            "csv_sha256",
            "snapshot_sha256",
            "d_feature_file_sha256",
        }
        and selection.get("json_path")
        == f"{SELECTION_ROOT.as_posix()}/shadow_{signal_date}.json"
        and selection.get("csv_path")
        == f"{SELECTION_ROOT.as_posix()}/shadow_{signal_date}.csv",
        "public research projection selection binding drifted",
    )
    _expect(
        all(
            row.get("shadow_price_source_sha256")
            == selection.get("d_feature_file_sha256")
            for row in rows
        ),
        "public research projection price cap source SHA drifted",
    )
    _expect(
        isinstance(three_rank, Mapping)
        and set(three_rank)
        == {
            "json_path",
            "json_sha256",
            "csv_path",
            "csv_sha256",
            "bundle_sha256",
            "feature_snapshot_sha256",
        }
        and three_rank.get("json_path")
        == f"outputs/decision/three_rank_top10_{signal_date}.json"
        and three_rank.get("csv_path")
        == f"outputs/decision/three_rank_top10_{signal_date}.csv"
        and three_rank.get("bundle_sha256") == payload.get("source_bundle_sha256"),
        "public research projection three-rank binding drifted",
    )
    for binding in (selection, three_rank):
        _expect(isinstance(binding, Mapping), "public research source binding invalid")
        for key, value in binding.items():
            if key.endswith("sha256"):
                if (
                    key == "feature_snapshot_sha256"
                    and len(rows) == 0
                    and value in {None, ""}
                ):
                    continue
                _expect(
                    SHA256_RE.fullmatch(str(value or "")) is not None,
                    f"public research source binding {key} invalid",
                )
    _expect(
        payload.get("snapshot_sha256") == _payload_snapshot(payload),
        "public research projection snapshot hash drifted",
    )
    if require_downloads or "downloads" in payload:
        downloads = payload.get("downloads")
        prefix = f"{OUTPUT_ROOT.as_posix()}/projection_{signal_date}"
        _expect(
            isinstance(downloads, Mapping)
            and downloads.get("json_url") == f"{prefix}.json"
            and downloads.get("csv_url") == f"{prefix}.csv"
            and downloads.get("row_count") == len(rows)
            and SHA256_RE.fullmatch(str(downloads.get("csv_sha256") or ""))
            is not None,
            "public research projection downloads drifted",
        )


def _optional_json(
    repo_root: Path,
    relative: Path,
    *,
    label: str,
) -> tuple[Path, dict[str, Any]] | None:
    candidate = repo_root / relative
    if not candidate.exists():
        return None
    path = _safe_existing_file(repo_root, relative, label=label)
    return path, _read_json(path, label=label)


def _selection_file_binding(
    projection: Mapping[str, Any],
) -> tuple[str, str, str, list[tuple[int, str]]]:
    source = projection["source_bindings"]["selection"]
    members = [
        (int(row["shadow_slot"]), str(row["ts_code"]))
        for row in projection["rows"]
        if row["shadow_selected"]
    ]
    return (
        str(source["json_path"]),
        str(source["json_sha256"]),
        str(source["snapshot_sha256"]),
        members,
    )


def _validate_optional_truth_binding(
    payload: Mapping[str, Any],
    projection: Mapping[str, Any],
    *,
    kind: str,
) -> None:
    selection_path, selection_file_sha, selection_snapshot, members = (
        _selection_file_binding(projection)
    )
    _expect(
        payload.get("signal_date") == projection.get("signal_date")
        and payload.get("exec_date") == projection.get("exec_date")
        and payload.get("exit_date") == projection.get("exit_date"),
        f"{kind} D/T/T+1 binding drifted",
    )
    selection = payload.get("selection")
    _expect(
        isinstance(selection, Mapping)
        and selection.get("path") == selection_path
        and selection.get("file_sha256") == selection_file_sha
        and selection.get("snapshot_sha256") == selection_snapshot
        and selection.get("top10_members_sha256")
        == projection.get("top10_members_sha256")
        and [
            (int(row.get("shadow_slot") or 0), str(row.get("ts_code") or ""))
            for row in selection.get("selected_members") or []
            if isinstance(row, Mapping)
        ]
        == members,
        f"{kind} immutable selection binding drifted",
    )
    truth_rows = payload.get("rows")
    _expect(isinstance(truth_rows, list), f"{kind} rows missing")
    _expect(
        [
            (int(row.get("shadow_slot") or 0), str(row.get("ts_code") or ""))
            for row in truth_rows
            if isinstance(row, Mapping)
        ]
        == members,
        f"{kind} changed frozen Shadow members",
    )
    boundaries = payload.get("boundaries")
    _expect(
        isinstance(boundaries, Mapping)
        and boundaries.get("official_trade_action_allowed") is False
        and boundaries.get("selection_changed") is False,
        f"{kind} safety boundary drifted",
    )
    snapshot = str(payload.get("snapshot_sha256") or "")
    _expect(
        SHA256_RE.fullmatch(snapshot) is not None,
        f"{kind} snapshot SHA invalid",
    )


def _truth_row_projection(
    projection: Mapping[str, Any],
    verification: Mapping[str, Any] | None,
    settlement: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    verification_rows = {
        int(row["shadow_slot"]): row
        for row in (verification or {}).get("rows", [])
        if isinstance(row, Mapping)
    }
    settlement_rows = {
        int(row["shadow_slot"]): row
        for row in (settlement or {}).get("rows", [])
        if isinstance(row, Mapping)
    }
    output: list[dict[str, Any]] = []
    for row in projection["rows"]:
        if not row["shadow_selected"]:
            continue
        slot = int(row["shadow_slot"])
        verified = verification_rows.get(slot)
        settled = settlement_rows.get(slot)
        output.append(
            {
                "shadow_slot": slot,
                "ts_code": row["ts_code"],
                "executable_profit_research_rank": row[
                    "executable_profit_research_rank"
                ],
                "t_validation_status": (
                    verified.get("validation_status") if verified is not None else None
                ),
                "proxy_fill": (
                    verified.get("proxy_fill") if verified is not None else None
                ),
                "t1_settlement_status": (
                    settled.get("settlement_status") if settled is not None else None
                ),
                "net_return_after_cost": (
                    settled.get("net_return_after_cost")
                    if settled is not None
                    else None
                ),
                "strategy_slot_return": (
                    settled.get("strategy_slot_return")
                    if settled is not None
                    else None
                ),
                "actual_human_trade_return": None,
            }
        )
    return output


def _validate_statistics_input_files_asof(
    repo_root: Path,
    statistics: Mapping[str, Any],
    as_of_date: str,
) -> None:
    """Prove that the deterministic summary cannot import a legacy/future ledger."""

    inputs = statistics.get("input_files")
    _expect(isinstance(inputs, list), "Shadow statistics input file ledger missing")
    from top10decision.decision.executable_profit_shadow import (
        validate_internal_forward_shadow_payload,
    )
    from top10decision.decision.executable_profit_shadow_settlement import (
        validate_t1_settlement,
        validate_t_verification,
    )

    allowed = (
        re.compile(
            r"data/decision_executable_profit/forward/selections/"
            r"shadow_(20\d{6})\.json"
        ),
        re.compile(
            r"data/decision_executable_profit/forward/verifications/"
            r"t_verification_(20\d{6})\.json"
        ),
        re.compile(
            r"data/decision_executable_profit/forward/settlements/"
            r"settlement_(20\d{6})\.json"
        ),
    )
    normalized: list[dict[str, str]] = []
    for item in inputs:
        _expect(
            isinstance(item, Mapping)
            and set(item) == {"path", "sha256"}
            and SHA256_RE.fullmatch(str(item.get("sha256") or "")) is not None,
            "Shadow statistics input file binding invalid",
        )
        relative_text = str(item["path"])
        matches = [pattern.fullmatch(relative_text) for pattern in allowed]
        _expect(
            sum(match is not None for match in matches) == 1,
            "legacy or foreign ledger was injected into Shadow statistics",
        )
        relative = Path(relative_text)
        path = _safe_existing_file(
            repo_root,
            relative,
            label="Shadow statistics bound input",
        )
        _expect(
            _sha256(path) == item["sha256"],
            "Shadow statistics bound input file SHA drifted",
        )
        source = _read_json(path, label="Shadow statistics bound input")
        source_date = next(match.group(1) for match in matches if match is not None)
        _expect(source_date <= as_of_date, "Shadow statistics contains future D")
        if matches[0] is not None:
            validate_internal_forward_shadow_payload(source, require_downloads=True)
            truth_date = str(source["signal_date"])
        elif matches[1] is not None:
            validate_t_verification(source)
            truth_date = str(source["exec_date"])
        else:
            validate_t1_settlement(source)
            actual_dates = [
                str(row["actual_exit_date"])
                for row in source["rows"]
                if row.get("actual_exit_date") is not None
            ]
            truth_date = max(actual_dates, default=str(source["exit_date"]))
        _expect(
            truth_date <= as_of_date,
            "Shadow statistics exposes truth after its as-of date",
        )
        normalized.append({"path": relative_text, "sha256": str(item["sha256"])})
    _expect(
        normalized == sorted(normalized, key=lambda row: row["path"])
        and statistics.get("input_files_sha256")
        == _source_statistics_input_files_sha256(normalized),
        "Shadow statistics input file bundle hash drifted",
    )


def _validate_public_asof_calendar(
    repo_root: Path,
    projection: Mapping[str, Any],
    as_of_date: str,
) -> None:
    """Bind public D/T/T+1/A dates to the pinned SSE open-session calendar."""

    from top10decision.decision.executable_profit_shadow_settlement import (
        CALENDAR_PATH,
        CALENDAR_SHA256,
    )

    calendar_path = _safe_existing_file(
        repo_root,
        CALENDAR_PATH,
        label="public research strict SSE calendar",
    )
    _expect(
        _sha256(calendar_path) == CALENDAR_SHA256,
        "public research strict SSE calendar SHA drifted",
    )
    try:
        with calendar_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ExecutableProfitResearchProjectionError(
            "public research strict SSE calendar is unreadable"
        ) from exc
    open_dates = sorted(
        {
            _normal_date(row.get("cal_date"))
            for row in rows
            if str(row.get("exchange") or "").strip().upper() == "SSE"
            and str(row.get("is_open") or "").strip() == "1"
        }
    )
    _expect(
        open_dates and all(DATE_RE.fullmatch(value) for value in open_dates),
        "public research strict SSE calendar is empty",
    )
    signal_date = str(projection["signal_date"])
    exec_date = str(projection["exec_date"])
    exit_date = str(projection["exit_date"])
    try:
        signal_index = open_dates.index(signal_date)
    except ValueError as exc:
        raise ExecutableProfitResearchProjectionError(
            "public research D is not a pinned SSE open session"
        ) from exc
    _expect(
        signal_index + 2 < len(open_dates)
        and open_dates[signal_index + 1] == exec_date
        and open_dates[signal_index + 2] == exit_date,
        "public research D/T/T+1 are not adjacent pinned SSE sessions",
    )
    _expect(
        as_of_date in open_dates and as_of_date >= signal_date,
        "public research as-of date must be a pinned SSE open session on or after D",
    )


def build_shadow_statistics_projection(
    repo_root: Path,
    projection: Mapping[str, Any],
    as_of_date: str,
) -> dict[str, Any]:
    """Build a separate as-of truth/statistics view without rewriting D rank."""

    repo_root = repo_root.resolve(strict=True)
    validate_research_projection(projection)
    supplied_as_of_date = str(as_of_date)
    as_of_date = _normal_date(supplied_as_of_date)
    _expect(
        DATE_RE.fullmatch(as_of_date) is not None
        and supplied_as_of_date == as_of_date
        and as_of_date >= str(projection["signal_date"]),
        "public Shadow statistics as-of date is invalid",
    )
    _validate_public_asof_calendar(repo_root, projection, as_of_date)
    signal_date = str(projection["signal_date"])
    verification_loaded = _optional_json(
        repo_root,
        VERIFICATION_ROOT / f"t_verification_{signal_date}.json",
        label="optional immutable T verification",
    )
    settlement_loaded = _optional_json(
        repo_root,
        SETTLEMENT_ROOT / f"settlement_{signal_date}.json",
        label="optional immutable T+1 settlement",
    )
    statistics_loaded = _optional_json(
        repo_root,
        SOURCE_STATISTICS_PATH,
        label="optional deterministic Shadow statistics",
    )
    verification = verification_loaded[1] if verification_loaded else None
    settlement = settlement_loaded[1] if settlement_loaded else None
    statistics = statistics_loaded[1] if statistics_loaded else None
    try:
        from top10decision.decision.executable_profit_shadow_settlement import (
            validate_statistics,
            validate_t1_settlement,
            validate_t_verification,
        )

        if verification is not None:
            validate_t_verification(verification)
        if settlement is not None:
            validate_t1_settlement(settlement)
        if statistics is not None:
            validate_statistics(statistics)
    except Exception as exc:
        raise ExecutableProfitResearchProjectionError(
            "optional immutable Shadow truth/statistics contract is invalid"
        ) from exc
    if verification is not None:
        _validate_optional_truth_binding(
            verification,
            projection,
            kind="T verification",
        )
        _expect(
            str(projection["exec_date"]) <= as_of_date,
            "as-of statistics exposed T truth before T",
        )
    if settlement is not None:
        _expect(
            verification is not None,
            "T+1 settlement exists without immutable T verification",
        )
        _validate_optional_truth_binding(
            settlement,
            projection,
            kind="T+1 settlement",
        )
        _expect(
            str(projection["exit_date"]) <= as_of_date,
            "as-of statistics exposed T+1 truth before T+1",
        )
        actual_exit_dates: list[str] = []
        for row in settlement.get("rows", []):
            _expect(
                isinstance(row, Mapping),
                "T+1 settlement row is invalid",
            )
            raw_actual_exit_date = row.get("actual_exit_date")
            if raw_actual_exit_date is None:
                continue
            actual_exit_date = _normal_date(raw_actual_exit_date)
            _expect(
                DATE_RE.fullmatch(actual_exit_date) is not None
                and str(raw_actual_exit_date) == actual_exit_date,
                "T+1 settlement actual exit date is invalid",
            )
            actual_exit_dates.append(actual_exit_date)
        _expect(
            all(value <= as_of_date for value in actual_exit_dates),
            "T+1 settlement actual exit is after public as-of date",
        )
        truth_binding = settlement.get("t_verification")
        _expect(
            isinstance(truth_binding, Mapping)
            and verification_loaded is not None
            and truth_binding.get("file_sha256")
            == _sha256(verification_loaded[0])
            and truth_binding.get("snapshot_sha256")
            == verification.get("snapshot_sha256"),
            "T+1 settlement no longer binds exact T verification bytes",
        )
    if statistics is not None:
        _expect(
            statistics.get("status") == "INTERNAL_RESEARCH_SHADOW_ONLY"
            and statistics.get("as_of_date") == as_of_date
            and isinstance(statistics.get("cohorts"), Mapping)
            and isinstance(statistics.get("forward_signal_date_progress_180"), Mapping)
            and statistics.get("boundaries", {}).get(
                "official_trade_action_allowed"
            )
            is False
            and statistics.get("boundaries", {}).get("actual_execution_claimed")
            is False,
            "optional Shadow statistics contract drifted",
        )
        source_snapshot = str(statistics.get("snapshot_sha256") or "")
        _expect(
            SHA256_RE.fullmatch(source_snapshot) is not None,
            "optional Shadow statistics snapshot SHA invalid",
        )
        _validate_statistics_input_files_asof(
            repo_root,
            statistics,
            as_of_date,
        )
    source_bindings: dict[str, Any] = {
        "t_verification": None,
        "t1_settlement": None,
        "statistics": None,
    }
    if verification_loaded:
        source_bindings["t_verification"] = {
            "path": verification_loaded[0].relative_to(repo_root).as_posix(),
            "file_sha256": _sha256(verification_loaded[0]),
            "snapshot_sha256": verification["snapshot_sha256"],
        }
    if settlement_loaded:
        source_bindings["t1_settlement"] = {
            "path": settlement_loaded[0].relative_to(repo_root).as_posix(),
            "file_sha256": _sha256(settlement_loaded[0]),
            "snapshot_sha256": settlement["snapshot_sha256"],
        }
    if statistics_loaded:
        source_bindings["statistics"] = {
            "path": statistics_loaded[0].relative_to(repo_root).as_posix(),
            "file_sha256": _sha256(statistics_loaded[0]),
            "snapshot_sha256": statistics["snapshot_sha256"],
        }
    final_projection, _, final_projection_json = _projection_output_payload(
        projection
    )
    projection_binding = {
        "path": f"{OUTPUT_ROOT.as_posix()}/projection_{signal_date}.json",
        "file_sha256": _sha256_bytes(final_projection_json),
        "snapshot_sha256": final_projection["snapshot_sha256"],
        "top10_members_sha256": final_projection["top10_members_sha256"],
        "source_bundle_sha256": final_projection["source_bundle_sha256"],
    }
    payload: dict[str, Any] = {
        "schema_version": STATISTICS_SCHEMA,
        "artifact_kind": STATISTICS_KIND,
        "contract_id": CONTRACT_ID,
        "status": "PUBLIC_RESEARCH_SHADOW_STATISTICS_ONLY",
        "display_name": DISPLAY_NAME,
        "as_of_date": as_of_date,
        "signal_date": signal_date,
        "exec_date": projection["exec_date"],
        "exit_date": projection["exit_date"],
        "projection_binding": projection_binding,
        "source_bindings": source_bindings,
        "latest_selected_rows": _truth_row_projection(
            projection,
            verification,
            settlement,
        ),
        "statistics": (
            {
                "scope": copy.deepcopy(statistics.get("scope")),
                "source_as_of_date": statistics.get("as_of_date"),
                "forward_signal_date_progress_180": copy.deepcopy(
                    statistics.get("forward_signal_date_progress_180")
                ),
                "cohorts": copy.deepcopy(statistics.get("cohorts")),
                "probability_diagnostics": copy.deepcopy(
                    statistics.get("probability_diagnostics")
                ),
                "excluded_ledgers": copy.deepcopy(
                    statistics.get("excluded_ledgers")
                ),
                "pending_definitions": copy.deepcopy(
                    statistics.get("pending_definitions")
                ),
                "input_files_sha256": statistics.get("input_files_sha256"),
            }
            if statistics is not None
            else None
        ),
        "boundaries": dict(PUBLIC_BOUNDARIES),
    }
    payload["snapshot_sha256"] = _payload_snapshot(payload)
    validate_shadow_statistics_projection(payload)
    return payload


def validate_shadow_statistics_projection(payload: Mapping[str, Any]) -> None:
    expected = {
        "schema_version",
        "artifact_kind",
        "contract_id",
        "status",
        "display_name",
        "as_of_date",
        "signal_date",
        "exec_date",
        "exit_date",
        "projection_binding",
        "source_bindings",
        "latest_selected_rows",
        "statistics",
        "boundaries",
        "snapshot_sha256",
    }
    _expect(set(payload) == expected, "public Shadow statistics surface drifted")
    _expect(
        payload.get("schema_version") == STATISTICS_SCHEMA
        and payload.get("artifact_kind") == STATISTICS_KIND
        and payload.get("contract_id") == CONTRACT_ID
        and payload.get("status") == "PUBLIC_RESEARCH_SHADOW_STATISTICS_ONLY"
        and payload.get("display_name") == DISPLAY_NAME,
        "public Shadow statistics identity drifted",
    )
    signal_date = _normal_date(payload.get("signal_date"))
    exec_date = _normal_date(payload.get("exec_date"))
    exit_date = _normal_date(payload.get("exit_date"))
    as_of_date = _normal_date(payload.get("as_of_date"))
    _expect(
        signal_date >= MINIMUM_SIGNAL_DATE
        and signal_date < exec_date < exit_date
        and as_of_date >= signal_date
        and payload.get("signal_date") == signal_date
        and payload.get("exec_date") == exec_date
        and payload.get("exit_date") == exit_date
        and payload.get("as_of_date") == as_of_date,
        "public Shadow statistics dates invalid",
    )
    projection = payload.get("projection_binding")
    _expect(
        isinstance(projection, Mapping)
        and set(projection)
        == {
            "path",
            "file_sha256",
            "snapshot_sha256",
            "top10_members_sha256",
            "source_bundle_sha256",
        }
        and projection.get("path")
        == f"{OUTPUT_ROOT.as_posix()}/projection_{signal_date}.json",
        "public Shadow statistics projection binding drifted",
    )
    for key in (
        "file_sha256",
        "snapshot_sha256",
        "top10_members_sha256",
        "source_bundle_sha256",
    ):
        _expect(
            SHA256_RE.fullmatch(str(projection.get(key) or "")) is not None,
            f"public Shadow statistics projection {key} invalid",
        )
    sources = payload.get("source_bindings")
    _expect(
        isinstance(sources, Mapping)
        and set(sources) == {"t_verification", "t1_settlement", "statistics"},
        "public Shadow statistics source bindings drifted",
    )
    for name, binding in sources.items():
        if binding is None:
            continue
        _expect(
            isinstance(binding, Mapping)
            and set(binding) == {"path", "file_sha256", "snapshot_sha256"}
            and SHA256_RE.fullmatch(str(binding.get("file_sha256") or ""))
            is not None
            and SHA256_RE.fullmatch(str(binding.get("snapshot_sha256") or ""))
            is not None,
            f"public Shadow statistics {name} binding invalid",
        )
    _expect(
        sources.get("t1_settlement") is None
        or sources.get("t_verification") is not None,
        "public Shadow statistics settlement lacks T verification",
    )
    rows = payload.get("latest_selected_rows")
    _expect(isinstance(rows, list) and len(rows) <= 2, "public Shadow latest rows invalid")
    row_keys = {
        "shadow_slot",
        "ts_code",
        "executable_profit_research_rank",
        "t_validation_status",
        "proxy_fill",
        "t1_settlement_status",
        "net_return_after_cost",
        "strategy_slot_return",
        "actual_human_trade_return",
    }
    for index, row in enumerate(rows, start=1):
        _expect(
            isinstance(row, Mapping)
            and set(row) == row_keys
            and row.get("shadow_slot") == index
            and row.get("executable_profit_research_rank") == index
            and _normal_code(row.get("ts_code"))
            and row.get("proxy_fill") in {None, 0, 1}
            and row.get("actual_human_trade_return") is None,
            "public Shadow latest row drifted or claims a human trade",
        )
    statistics = payload.get("statistics")
    _expect(
        statistics is None
        or (
            isinstance(statistics, Mapping)
            and set(statistics)
            == {
                "scope",
                "source_as_of_date",
                "forward_signal_date_progress_180",
                "cohorts",
                "probability_diagnostics",
                "excluded_ledgers",
                "pending_definitions",
                "input_files_sha256",
            }
            and sources.get("statistics") is not None
        ),
        "public Shadow statistics summary/binding drifted",
    )
    _expect(
        payload.get("boundaries") == PUBLIC_BOUNDARIES,
        "public Shadow statistics safety boundaries drifted",
    )
    _expect(
        SHA256_RE.fullmatch(str(payload.get("snapshot_sha256") or ""))
        is not None
        and payload.get("snapshot_sha256") == _payload_snapshot(payload),
        "public Shadow statistics snapshot hash drifted",
    )


PROJECTION_CSV_FIELDS = (
    "schema_version",
    "status",
    "display_name",
    "signal_date",
    "exec_date",
    "exit_date",
    "snapshot_sha256",
    "top10_members_sha256",
    "source_bundle_sha256",
    "ts_code",
    "name",
    "industry",
    "stage_transition",
    "promotion_rank",
    "predicted_promotion_probability",
    "executable_profit_research_rank",
    "estimated_executable_profit_probability",
    "research_joint_proxy_score",
    "research_fill_proxy_score",
    "research_conditional_profit_score",
    "shadow_selected",
    "shadow_slot",
    "shadow_max_price",
    "shadow_price_basis",
    "shadow_price_source_sha256",
    "public_research_projection_allowed",
    "estimated_probability_display_allowed",
    "estimated_probability_calibrated",
    "formal_probability_allowed",
    "formal_rank_allowed",
    "official_trade_action_allowed",
    "actual_execution_claimed",
    "human_decision_support_only",
)


def _projection_csv_bytes(payload: Mapping[str, Any]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=PROJECTION_CSV_FIELDS,
        lineterminator="\n",
        extrasaction="ignore",
    )
    writer.writeheader()
    common = {
        "schema_version": payload["schema_version"],
        "status": payload["status"],
        "display_name": payload["display_name"],
        "signal_date": payload["signal_date"],
        "exec_date": payload["exec_date"],
        "exit_date": payload["exit_date"],
        "snapshot_sha256": payload["snapshot_sha256"],
        "top10_members_sha256": payload["top10_members_sha256"],
        "source_bundle_sha256": payload["source_bundle_sha256"],
        "public_research_projection_allowed": True,
        "estimated_probability_display_allowed": True,
        "estimated_probability_calibrated": False,
        "formal_probability_allowed": False,
        "formal_rank_allowed": False,
        "official_trade_action_allowed": False,
        "actual_execution_claimed": False,
        "human_decision_support_only": True,
    }
    for row in payload["rows"]:
        writer.writerow({**common, **row})
    return b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")


def _projection_output_payload(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], bytes, bytes]:
    validate_research_projection(payload)
    enriched = copy.deepcopy(dict(payload))
    csv_payload = _projection_csv_bytes(enriched)
    signal_date = str(enriched["signal_date"])
    enriched["downloads"] = {
        "json_url": f"{OUTPUT_ROOT.as_posix()}/projection_{signal_date}.json",
        "csv_url": f"{OUTPUT_ROOT.as_posix()}/projection_{signal_date}.csv",
        "csv_sha256": _sha256_bytes(csv_payload),
        "row_count": len(enriched["rows"]),
    }
    validate_research_projection(enriched, require_downloads=True)
    return enriched, csv_payload, _pretty_json_bytes(enriched)


def _index_payload(
    projection: Mapping[str, Any],
    projection_json_sha256: str,
    statistics: Mapping[str, Any],
    statistics_json_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": INDEX_SCHEMA,
        "index_kind": INDEX_KIND,
        "data_alias": False,
        "display_name": DISPLAY_NAME,
        "latest_signal_date": projection["signal_date"],
        "latest_exec_date": projection["exec_date"],
        "latest_exit_date": projection["exit_date"],
        "latest_projection_json_url": projection["downloads"]["json_url"],
        "latest_projection_csv_url": projection["downloads"]["csv_url"],
        "latest_projection_json_sha256": projection_json_sha256,
        "latest_projection_csv_sha256": projection["downloads"]["csv_sha256"],
        "latest_projection_snapshot_sha256": projection["snapshot_sha256"],
        "latest_top10_members_sha256": projection["top10_members_sha256"],
        "latest_source_bundle_sha256": projection["source_bundle_sha256"],
        "latest_statistics_as_of_date": statistics["as_of_date"],
        "latest_statistics_url": (
            f"{OUTPUT_ROOT.as_posix()}/"
            f"shadow_statistics_{statistics['signal_date']}_"
            f"asof_{statistics['as_of_date']}.json"
        ),
        "latest_statistics_json_sha256": statistics_json_sha256,
        "latest_statistics_snapshot_sha256": statistics["snapshot_sha256"],
        "boundaries": dict(PUBLIC_BOUNDARIES),
    }


def validate_research_projection_index(index: Mapping[str, Any]) -> None:
    expected = {
        "schema_version",
        "index_kind",
        "data_alias",
        "display_name",
        "latest_signal_date",
        "latest_exec_date",
        "latest_exit_date",
        "latest_projection_json_url",
        "latest_projection_csv_url",
        "latest_projection_json_sha256",
        "latest_projection_csv_sha256",
        "latest_projection_snapshot_sha256",
        "latest_top10_members_sha256",
        "latest_source_bundle_sha256",
        "latest_statistics_as_of_date",
        "latest_statistics_url",
        "latest_statistics_json_sha256",
        "latest_statistics_snapshot_sha256",
        "boundaries",
    }
    _expect(set(index) == expected, "public research index surface drifted")
    _expect(
        index.get("schema_version") == INDEX_SCHEMA
        and index.get("index_kind") == INDEX_KIND
        and index.get("data_alias") is False
        and index.get("display_name") == DISPLAY_NAME,
        "public research index identity drifted",
    )
    signal_date = _normal_date(index.get("latest_signal_date"))
    exec_date = _normal_date(index.get("latest_exec_date"))
    exit_date = _normal_date(index.get("latest_exit_date"))
    as_of_date = _normal_date(index.get("latest_statistics_as_of_date"))
    _expect(
        signal_date >= MINIMUM_SIGNAL_DATE
        and signal_date < exec_date < exit_date
        and as_of_date >= signal_date
        and index.get("latest_signal_date") == signal_date
        and index.get("latest_exec_date") == exec_date
        and index.get("latest_exit_date") == exit_date
        and index.get("latest_statistics_as_of_date") == as_of_date,
        "public research index dates invalid",
    )
    prefix = f"{OUTPUT_ROOT.as_posix()}/projection_{signal_date}"
    _expect(
        index.get("latest_projection_json_url") == f"{prefix}.json"
        and index.get("latest_projection_csv_url") == f"{prefix}.csv"
        and index.get("latest_statistics_url")
        == (
            f"{OUTPUT_ROOT.as_posix()}/"
            f"shadow_statistics_{signal_date}_asof_{as_of_date}.json"
        ),
        "public research index is not exact dated/as-of only",
    )
    for key in (
        "latest_projection_json_sha256",
        "latest_projection_csv_sha256",
        "latest_projection_snapshot_sha256",
        "latest_top10_members_sha256",
        "latest_source_bundle_sha256",
        "latest_statistics_json_sha256",
        "latest_statistics_snapshot_sha256",
    ):
        _expect(
            SHA256_RE.fullmatch(str(index.get(key) or "")) is not None,
            f"public research index {key} invalid",
        )
    _expect(
        index.get("boundaries") == PUBLIC_BOUNDARIES,
        "public research index safety boundaries drifted",
    )


@contextmanager
def _locked(directory: Path) -> Iterable[None]:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _stage(path: Path, content: bytes) -> Path:
    with tempfile.NamedTemporaryFile(
        "wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        return Path(handle.name)


def _install_immutable_many(
    artifacts: Sequence[tuple[Path, bytes]],
) -> tuple[Path, ...]:
    staged: list[Path] = []
    created: list[Path] = []
    try:
        for target, content in artifacts:
            temporary = _stage(target, content)
            staged.append(temporary)
            try:
                os.link(temporary, target)
            except FileExistsError:
                _expect(
                    target.is_file()
                    and not target.is_symlink()
                    and target.read_bytes() == content,
                    f"immutable public artifact conflict: {target.name}",
                )
            else:
                created.append(target)
    except Exception:
        for target in reversed(created):
            try:
                target.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        for temporary in staged:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    return tuple(created)


def _atomic_replace(path: Path, content: bytes) -> None:
    temporary = _stage(path, content)
    try:
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _validate_existing_index_chain(repo_root: Path, index: Mapping[str, Any]) -> None:
    validate_research_projection_index(index)
    projection_json = _safe_existing_file(
        repo_root,
        Path(str(index["latest_projection_json_url"])),
        label="pointed public research projection JSON",
    )
    projection_csv = _safe_existing_file(
        repo_root,
        Path(str(index["latest_projection_csv_url"])),
        label="pointed public research projection CSV",
    )
    statistics_path = _safe_existing_file(
        repo_root,
        Path(str(index["latest_statistics_url"])),
        label="pointed public Shadow statistics",
    )
    _expect(
        _sha256(projection_json) == index.get("latest_projection_json_sha256")
        and _sha256(projection_csv)
        == index.get("latest_projection_csv_sha256")
        and _sha256(statistics_path)
        == index.get("latest_statistics_json_sha256"),
        "public research index target file SHA drifted",
    )
    projection = _read_json(
        projection_json,
        label="pointed public research projection JSON",
    )
    statistics = _read_json(
        statistics_path,
        label="pointed public Shadow statistics",
    )
    validate_research_projection(projection, require_downloads=True)
    _verify_projection_source_files(repo_root, projection)
    validate_shadow_statistics_projection(statistics)
    _expect(
        projection.get("signal_date") == index.get("latest_signal_date")
        and projection.get("exec_date") == index.get("latest_exec_date")
        and projection.get("exit_date") == index.get("latest_exit_date")
        and projection.get("snapshot_sha256")
        == index.get("latest_projection_snapshot_sha256")
        and projection.get("top10_members_sha256")
        == index.get("latest_top10_members_sha256")
        and projection.get("source_bundle_sha256")
        == index.get("latest_source_bundle_sha256")
        and statistics.get("as_of_date")
        == index.get("latest_statistics_as_of_date")
        and statistics.get("snapshot_sha256")
        == index.get("latest_statistics_snapshot_sha256")
        and statistics.get("projection_binding", {}).get("file_sha256")
        == index.get("latest_projection_json_sha256"),
        "public research index metadata chain drifted",
    )


def _verify_projection_source_files(
    repo_root: Path,
    projection: Mapping[str, Any],
) -> None:
    contract_path = _safe_existing_file(
        repo_root,
        CONTRACT_PATH,
        label="bound public research contract",
    )
    _expect(
        _sha256(contract_path) == projection.get("contract_file_sha256"),
        "public research contract file SHA drifted",
    )
    bindings = projection["source_bindings"]
    for name in ("selection", "three_rank"):
        binding = bindings[name]
        for kind in ("json", "csv"):
            path = _safe_existing_file(
                repo_root,
                Path(str(binding[f"{kind}_path"])),
                label=f"bound public research {name} {kind}",
            )
            _expect(
                _sha256(path) == binding[f"{kind}_sha256"],
                f"public research {name} {kind} file SHA drifted",
            )


def _verify_materialization_reconstruction(
    repo_root: Path,
    final_projection: Mapping[str, Any],
    projection_csv: bytes,
    projection_json: bytes,
    statistics: Mapping[str, Any],
) -> None:
    """Rebuild every public byte from immutable sources before installation."""

    rebuilt_projection = build_research_projection(
        repo_root,
        str(final_projection["signal_date"]),
    )
    (
        rebuilt_final_projection,
        rebuilt_projection_csv,
        rebuilt_projection_json,
    ) = _projection_output_payload(rebuilt_projection)
    _expect(
        _canonical_json_bytes(final_projection)
        == _canonical_json_bytes(rebuilt_final_projection)
        and projection_csv == rebuilt_projection_csv
        and projection_json == rebuilt_projection_json,
        (
            "public research projection does not exactly reconstruct from "
            "immutable selection/three-rank sources"
        ),
    )

    rebuilt_statistics = build_shadow_statistics_projection(
        repo_root,
        rebuilt_projection,
        str(statistics["as_of_date"]),
    )
    _expect(
        _canonical_json_bytes(statistics)
        == _canonical_json_bytes(rebuilt_statistics)
        and _pretty_json_bytes(statistics) == _pretty_json_bytes(rebuilt_statistics),
        (
            "public Shadow statistics does not exactly reconstruct from "
            "immutable verification/settlement/summary sources"
        ),
    )


def materialize_research_projection(
    repo_root: Path,
    projection: Mapping[str, Any],
    statistics: Mapping[str, Any],
) -> tuple[Path, Path, Path, Path, dict[str, Any]]:
    """Install immutable D rank and as-of truth, then atomically advance index."""

    repo_root = repo_root.resolve(strict=True)
    final_projection, projection_csv, projection_json = _projection_output_payload(
        projection
    )
    _verify_projection_source_files(repo_root, final_projection)
    validate_shadow_statistics_projection(statistics)
    _verify_materialization_reconstruction(
        repo_root,
        final_projection,
        projection_csv,
        projection_json,
        statistics,
    )
    statistics_json = _pretty_json_bytes(statistics)
    projection_json_sha = _sha256_bytes(projection_json)
    _expect(
        statistics.get("signal_date") == final_projection.get("signal_date")
        and statistics.get("projection_binding", {}).get("file_sha256")
        == projection_json_sha
        and statistics.get("projection_binding", {}).get("snapshot_sha256")
        == final_projection.get("snapshot_sha256"),
        "public Shadow statistics does not bind candidate projection bytes",
    )
    candidate_index = _index_payload(
        final_projection,
        projection_json_sha,
        statistics,
        _sha256_bytes(statistics_json),
    )
    validate_research_projection_index(candidate_index)
    output = _ensure_directory(repo_root, OUTPUT_ROOT)
    signal_date = str(final_projection["signal_date"])
    as_of_date = str(statistics["as_of_date"])
    projection_json_path = output / f"projection_{signal_date}.json"
    projection_csv_path = output / f"projection_{signal_date}.csv"
    statistics_path = output / (
        f"shadow_statistics_{signal_date}_asof_{as_of_date}.json"
    )
    index_path = output / "index.json"
    with _locked(output):
        existing_index: dict[str, Any] | None = None
        if index_path.exists():
            _expect(
                index_path.is_file() and not index_path.is_symlink(),
                "existing public research index is unsafe",
            )
            existing_index = _read_json(
                index_path,
                label="existing public research index",
            )
            _validate_existing_index_chain(repo_root, existing_index)
            _expect(
                str(existing_index["latest_signal_date"]) <= signal_date,
                "out-of-order public research projection pointer is forbidden",
            )
            _expect(
                str(existing_index["latest_statistics_as_of_date"]) <= as_of_date,
                "out-of-order public Shadow statistics pointer is forbidden",
            )
            if str(existing_index["latest_signal_date"]) == signal_date:
                _expect(
                    existing_index["latest_projection_json_sha256"]
                    == candidate_index["latest_projection_json_sha256"]
                    and existing_index["latest_projection_csv_sha256"]
                    == candidate_index["latest_projection_csv_sha256"],
                    "same-D public research projection cannot be changed",
                )
            if (
                str(existing_index["latest_signal_date"]) == signal_date
                and str(existing_index["latest_statistics_as_of_date"])
                == as_of_date
            ):
                _expect(
                    existing_index["latest_statistics_json_sha256"]
                    == candidate_index["latest_statistics_json_sha256"],
                    "same-as-of public Shadow statistics cannot be changed",
                )
        created = _install_immutable_many(
            (
                (projection_csv_path, projection_csv),
                (projection_json_path, projection_json),
                (statistics_path, statistics_json),
            )
        )
        try:
            if existing_index != candidate_index:
                _atomic_replace(index_path, _pretty_json_bytes(candidate_index))
        except Exception:
            for path in reversed(created):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            raise
    return (
        projection_json_path,
        projection_csv_path,
        statistics_path,
        index_path,
        candidate_index,
    )


def build_and_materialize_research_projection(
    repo_root: Path,
    signal_date: str,
    as_of_date: str,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    Path,
    Path,
    Path,
    Path,
    dict[str, Any],
]:
    projection = build_research_projection(repo_root, signal_date)
    statistics = build_shadow_statistics_projection(
        repo_root,
        projection,
        as_of_date,
    )
    paths = materialize_research_projection(repo_root, projection, statistics)
    return projection, statistics, *paths


__all__ = [
    "CONTRACT_ID",
    "CONTRACT_PATH",
    "DISPLAY_NAME",
    "ExecutableProfitResearchProjectionError",
    "INDEX_KIND",
    "INDEX_SCHEMA",
    "OUTPUT_ROOT",
    "PROJECTION_KIND",
    "PROJECTION_SCHEMA",
    "PUBLIC_BOUNDARIES",
    "STATISTICS_KIND",
    "STATISTICS_SCHEMA",
    "build_and_materialize_research_projection",
    "build_research_projection",
    "build_shadow_statistics_projection",
    "materialize_research_projection",
    "validate_research_projection",
    "validate_research_projection_index",
    "validate_shadow_statistics_projection",
]
