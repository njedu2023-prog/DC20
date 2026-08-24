from __future__ import annotations

import copy
import csv
import fcntl
import hashlib
import importlib.util
import io
import json
import math
import os
import pickle
import re
import sys
import tempfile
from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from top10decision.auction_v3.promotion_model import PROMOTION_SOURCE_FEATURES
from top10decision.decision.observation import observation_price_contract
from top10decision.decision.three_rank import (
    top10_members_sha256,
    validate_three_rank_contract,
    validate_three_rank_index,
)


SCHEMA_VERSION = "dc20_executable_profit_internal_forward_shadow_v1"
ARTIFACT_KIND = "d_frozen_internal_executable_profit_research_shadow"
INDEX_SCHEMA_VERSION = "dc20_executable_profit_internal_forward_shadow_index_v1"
INDEX_KIND = "internal_executable_profit_research_shadow_pointer"
INTERNAL_STATUS = "INTERNAL_CHALLENGER_NOT_READY"
ARTIFACT_STATUS = "INTERNAL_FORWARD_RESEARCH_CHALLENGER_ONLY_NOT_READY"
MINIMUM_SIGNAL_DATE = "20260824"
CONTRACT_ID = "dc20_executable_profit_internal_forward_challenger_20260824_v1"
ENTRY_POLICY_ID = "dc20_public_market_buyable_proxy_v1"

DEFAULT_CONTRACT_PATH = Path(
    "models/decision_executable_profit_internal_forward_challenger.json"
)
DEFAULT_FORMAL_CONTRACT_PATH = Path(
    "models/decision_executable_profit_shadow_contract.json"
)
DEFAULT_FEATURE_MANIFEST_PATH = Path(
    "data/decision_executable_profit/historical_oof_top10_ledger_manifest.json"
)
DEFAULT_HISTORY_LEDGER_PATH = Path(
    "data/decision_three_engines/five_year_supervised_ledger.csv.gz"
)
DEFAULT_CALENDAR_PATH = Path("data/market/trade_cal_sse.csv")
DEFAULT_WORK_ROOT = Path("work/executable-profit-lagged-features-20260824")
OUTPUT_RELATIVE_ROOT = Path("data/decision_executable_profit/forward/selections")

EXPECTED_FORMAL_CONTRACT_SHA256 = (
    "95f1953ca32afba9e92a40b717d8d02494bb71f3537e2d95a99e3b67cdd9cac2"
)
EXPECTED_ARTIFACT_INDEX_SHA256 = (
    "362ffcc4d83e7a14c68a2de71d0142ff119c62160752adbf93b2ef6c5789c66f"
)
EXPECTED_AUDIT_SHA256 = (
    "0b75a1f635e17b7663d0d68d3e505c0c21997c12331ce8a953882fa53d2ec980"
)
EXPECTED_MODEL_SHA256 = (
    "42dfb497d4457db9fbdff4180c510fee1ea18ab56696253b06220d981f88d209"
)
EXPECTED_LAGGED_PRIORS_SHA256 = (
    "b89d7bed14b0307d2aaa7c0762a98b4fda846dcdac35488938d8eae7db0bc39d"
)
EXPECTED_CALENDAR_SHA256 = (
    "150a3e29ebd6e050d55caee1df218ef5dcfc3542053d8a7478d6be50d09fd748"
)
EXPECTED_BASE_FEATURES_SHA256 = (
    "9f403117278b73653014a3682442072f026d8e73abef37d318086565dae23425"
)
EXPECTED_HISTORICAL_MANIFEST_SHA256 = (
    "3fd457dbe8438b28bbd80d0521ebd9a2ba2d17845be019412238b7898cce69f5"
)
EXPECTED_ALL_FEATURES_SHA256 = (
    "a07c3c2d688e1e0eb5aaaa891ffd3039d5ca3f6bb26f20e80f88611833893048"
)
EXPECTED_FULL_HISTORY_LEDGER_SHA256 = (
    "7cabe48da6375106b22b2c08c17a7b11780861fed319496ee26761d20fa20a46"
)

DERIVED_BASE_FEATURES = (
    "stage_2",
    "stage_3",
    "board_sh_main",
    "board_sz_main",
)
SHA256_RE = re.compile(r"[0-9a-f]{64}")


class ExecutableProfitShadowError(ValueError):
    pass


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise ExecutableProfitShadowError(message)


def _frozen_shadow_price_cap(
    row: Mapping[str, Any],
    *,
    source_sha256: str,
) -> tuple[float, str, str]:
    """Freeze a D-only research entry cap; never inspect T or later truth."""

    _expect(
        SHA256_RE.fullmatch(source_sha256) is not None,
        "shadow price source SHA is invalid",
    )
    contract = observation_price_contract(row)
    raw_price = contract.get("observation_max_price")
    try:
        price = float(raw_price)
    except (TypeError, ValueError) as exc:
        raise ExecutableProfitShadowError(
            "D feature row cannot produce a frozen shadow price cap"
        ) from exc
    _expect(
        math.isfinite(price) and price > 0.0,
        "D feature row cannot produce a frozen shadow price cap",
    )
    basis_map = {
        "formal_safe_cap": "D_FROZEN_RECOMMENDED_MAX_PRICE",
        "frozen_observation_cap": "D_FROZEN_OBSERVATION_MAX_PRICE",
        "model_diagnostic_cap": "D_ONLY_MODEL_DIAGNOSTIC_CAP",
        "legacy_d_close_cap": "D_CLOSE_CONSERVATIVE_CAP",
    }
    raw_basis = str(contract.get("observation_price_basis") or "")
    basis = basis_map.get(raw_basis)
    _expect(basis is not None, "D feature row has an unsupported shadow price basis")
    return round(price + 1e-9, 2), basis, source_sha256


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return _sha256_bytes(_canonical_json_bytes(value))


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExecutableProfitShadowError(f"invalid {label}: {path}") from exc
    _expect(isinstance(value, dict), f"{label} must be a JSON object")
    return value


def _safe_file(root: Path, relative: Path, *, label: str) -> Path:
    root = root.resolve()
    lexical = Path(os.path.abspath(root / relative))
    try:
        lexical.relative_to(root)
        resolved = lexical.resolve(strict=True)
    except (FileNotFoundError, ValueError) as exc:
        raise ExecutableProfitShadowError(f"{label} escaped or is missing") from exc
    _expect(
        resolved == lexical
        and lexical.is_file()
        and not lexical.is_symlink()
        and lexical.stat().st_size > 0,
        f"{label} is unsafe: {lexical}",
    )
    return lexical


def _safe_absolute_file(path: Path, *, label: str) -> Path:
    """Reject a file when it or any lexical ancestor is a symlink."""

    lexical = Path(os.path.abspath(path))
    try:
        resolved = lexical.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ExecutableProfitShadowError(f"{label} is missing") from exc
    _expect(
        resolved == lexical
        and lexical.is_file()
        and not lexical.is_symlink()
        and lexical.stat().st_size > 0,
        f"{label} is unsafe: {lexical}",
    )
    return lexical


def _safe_directory(
    root: Path,
    relative: Path,
    *,
    label: str,
    create: bool = False,
) -> Path:
    """Resolve a repository directory without ever following an in-tree symlink."""

    root = root.resolve(strict=True)
    lexical = Path(os.path.abspath(root / relative))
    try:
        lexical.relative_to(root)
    except ValueError as exc:
        raise ExecutableProfitShadowError(f"{label} escaped its root") from exc
    _expect(
        lexical.resolve(strict=False) == lexical,
        f"{label} contains a symlink ancestor: {lexical}",
    )
    if create:
        lexical.mkdir(parents=True, exist_ok=True)
    try:
        resolved = lexical.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ExecutableProfitShadowError(f"{label} is missing") from exc
    _expect(
        resolved == lexical and lexical.is_dir() and not lexical.is_symlink(),
        f"{label} is unsafe: {lexical}",
    )
    return lexical


def _normal_date(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _normal_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    match = re.fullmatch(r"(\d{6})\.(SH|SZ)", text)
    return f"{match.group(1)}.{match.group(2)}" if match else ""


def _parse_aware_datetime(value: Any, *, label: str) -> datetime:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExecutableProfitShadowError(f"{label} is not an ISO timestamp") from exc
    _expect(parsed.tzinfo is not None, f"{label} must be timezone-aware")
    return parsed


def _selection_window(signal_date: str, exec_date: str) -> tuple[datetime, datetime]:
    timezone = ZoneInfo("Asia/Shanghai")
    start = datetime.strptime(signal_date, "%Y%m%d").replace(
        hour=15,
        minute=0,
        second=0,
        tzinfo=timezone,
    )
    end = datetime.strptime(exec_date, "%Y%m%d").replace(
        hour=9,
        minute=20,
        second=0,
        tzinfo=timezone,
    )
    return start, end


def _read_pinned_sse_open_dates(repo_root: Path) -> list[str]:
    """Read the hash-pinned SSE calendar without loading model code or pickle."""

    calendar_path = _safe_file(
        repo_root,
        DEFAULT_CALENDAR_PATH,
        label="strict SSE calendar",
    )
    _expect(
        _sha256(calendar_path) == EXPECTED_CALENDAR_SHA256,
        "strict SSE calendar SHA drifted",
    )
    try:
        calendar = pd.read_csv(calendar_path, dtype={"cal_date": str})
    except (OSError, UnicodeError, pd.errors.ParserError) as exc:
        raise ExecutableProfitShadowError(
            "strict SSE calendar is unreadable"
        ) from exc
    _expect(
        {"cal_date", "is_open"}.issubset(calendar.columns)
        and not calendar.empty,
        "strict SSE calendar schema is invalid",
    )
    dates = calendar["cal_date"].map(_normal_date)
    flags = pd.to_numeric(calendar["is_open"], errors="coerce")
    _expect(
        dates.str.fullmatch(r"20\d{6}").all()
        and flags.notna().all()
        and flags.isin((0, 1)).all(),
        "strict SSE calendar contains invalid sessions",
    )
    normalized = pd.DataFrame(
        {"cal_date": dates, "is_open": flags.astype(int)}
    )
    conflicts = normalized.groupby("cal_date")["is_open"].nunique()
    _expect(
        not conflicts.gt(1).any(),
        "strict SSE calendar contains conflicting sessions",
    )
    open_dates = (
        normalized.loc[normalized["is_open"].eq(1), "cal_date"]
        .drop_duplicates()
        .sort_values(kind="stable")
        .tolist()
    )
    _expect(bool(open_dates), "strict SSE calendar contains no open sessions")
    return open_dates


def _validate_internal_contract(
    contract: Mapping[str, Any],
    *,
    formal_contract_sha256: str,
) -> None:
    _expect(
        contract.get("status") == INTERNAL_STATUS,
        "internal challenger contract is not strict NOT_READY",
    )
    _expect(
        contract.get("schema_version")
        == "dc20_executable_profit_internal_forward_contract_v1",
        "internal contract schema is invalid",
    )
    _expect(
        contract.get("contract_id")
        == "dc20_executable_profit_internal_forward_challenger_20260824_v1",
        "internal contract id drifted",
    )
    authority = contract.get("authority")
    bindings = contract.get("bindings")
    boundaries = contract.get("boundaries")
    semantics = contract.get("model_semantics")
    inputs = contract.get("input_contract")
    selection = contract.get("selection_contract")
    timing = contract.get("timing_contract")
    for value, label in (
        (authority, "authority"),
        (bindings, "bindings"),
        (boundaries, "boundaries"),
        (semantics, "model_semantics"),
        (inputs, "input_contract"),
        (selection, "selection_contract"),
        (timing, "timing_contract"),
    ):
        _expect(isinstance(value, Mapping), f"internal contract {label} missing")
    parent_sha = "d176e3400e4e9bd38702e8eba1d11ff55adcf7f4"
    expected_authority = {
        "repository": "njedu2023-prog/DC20",
        "branch": "main",
        "implementation_parent_commit_sha": parent_sha,
        "runtime_dependency_on_codex": False,
        "runtime_dependency_on_top10_decision": False,
        "runtime_dependency_on_recovery": False,
    }
    _expect(dict(authority) == expected_authority, "internal contract authority drifted")
    expected_bindings = {
        "parent_commit_sha": parent_sha,
        "formal_shadow_contract_path": DEFAULT_FORMAL_CONTRACT_PATH.as_posix(),
        "formal_shadow_contract_sha256": formal_contract_sha256,
        "artifact_index_path": f"{DEFAULT_WORK_ROOT.as_posix()}/ARTIFACT_INDEX.json",
        "artifact_index_sha256": EXPECTED_ARTIFACT_INDEX_SHA256,
        "audit_path": f"{DEFAULT_WORK_ROOT.as_posix()}/outputs/internal_forward_challenger_audit.json",
        "audit_sha256": EXPECTED_AUDIT_SHA256,
        "model_pickle_path": f"{DEFAULT_WORK_ROOT.as_posix()}/outputs/internal_forward_challenger.pkl",
        "model_pickle_sha256": EXPECTED_MODEL_SHA256,
        "lagged_priors_path": f"{DEFAULT_WORK_ROOT.as_posix()}/lagged_priors.py",
        "lagged_priors_sha256": EXPECTED_LAGGED_PRIORS_SHA256,
        "historical_manifest_path": DEFAULT_FEATURE_MANIFEST_PATH.as_posix(),
        "historical_manifest_sha256": EXPECTED_HISTORICAL_MANIFEST_SHA256,
        "strict_sse_calendar_path": DEFAULT_CALENDAR_PATH.as_posix(),
        "strict_sse_calendar_sha256": EXPECTED_CALENDAR_SHA256,
    }
    _expect(dict(bindings) == expected_bindings, "internal contract exact bindings drifted")
    expected_boundaries = {
        "research_only": True,
        "proxy_scores_uncalibrated": True,
        "formal_executable_profit_contract_implemented": False,
        "front_end_rank_allowed": False,
        "official_trade_action_allowed": False,
        "production_publish_allowed": False,
        "actual_execution_claimed": False,
        "historical_backfill_allowed": False,
        "post_outcome_reranking_allowed": False,
    }
    _expect(
        dict(boundaries) == expected_boundaries,
        "internal contract exact research boundaries drifted",
    )
    expected_semantics = {
        "model_kind": "hgb",
        "variant": "full_priors",
        "feature_count": 156,
        "feature_columns_sha256": EXPECTED_ALL_FEATURES_SHA256,
        "fill_output": "research_fill_proxy_score",
        "fill_label_limit": (
            "historical daily-bar non-one-price-limit-up market-buyable proxy; "
            "not actual fill and not the complete forward auction capacity truth"
        ),
        "conditional_output": "research_conditional_profit_score",
        "joint_output": "research_joint_proxy_score",
        "joint_identity": (
            "research_fill_proxy_score * research_conditional_profit_score"
        ),
        "calibration_status": "NOT_CALIBRATED_BRIER_GATE_FAILED",
        "expected_net_return_lcb_available": False,
        "big_loss_tie_break_available": False,
        "may_be_called_calibrated_probability": False,
        "may_be_called_formal_rank": False,
    }
    _expect(
        dict(semantics) == expected_semantics,
        "internal contract model semantics drifted",
    )
    expected_inputs = {
        "minimum_signal_date": MINIMUM_SIGNAL_DATE,
        "require_complete_frozen_promotion_topn": True,
        "candidate_count_rule": (
            "N = min(10, complete eligible 2-to-3/3-to-4 promotion pool)"
        ),
        "minimum_candidate_rows": 0,
        "maximum_candidate_rows": 10,
        "minimum_candidate_rows_for_shadow_top2": 2,
        "zero_candidate_policy": (
            "FREEZE_EMPTY_EVENT_NO_MODEL_LOAD_NO_INFERENCE_NO_BACKFILL"
        ),
        "single_candidate_policy": (
            "FREEZE_TOP1_ONLY_NO_TOP2_NO_BACKFILL"
        ),
        "hard_stage_scope": ["2→3", "3→4"],
        "required_promotion_source_features": list(PROMOTION_SOURCE_FEATURES),
        "derived_one_hot_features": list(DERIVED_BASE_FEATURES),
        "promotion_rank_or_probability_as_model_feature_allowed": False,
        "full_history_lagged_prior_features": 108,
        "lagged_outcome_availability_rule": (
            "historical availability date must be strictly earlier than target D"
        ),
        "old_feature_incomplete_dated_predictions_allowed": False,
    }
    _expect(
        dict(inputs) == expected_inputs,
        "internal contract input boundary drifted",
    )
    expected_selection = {
        "slots": 2,
        "scope": (
            "complete frozen promotion TopN only, where N may be zero and is at "
            "most 10 and no candidate may be added outside the hard pool"
        ),
        "selected_slots_rule": "min(2, N); no padding",
        "entry_policy_id": ENTRY_POLICY_ID,
        "shadow_price_cap_rule": (
            "freeze recommended_max_price when positive; otherwise freeze "
            "observation_price_contract from D-only fields; missing cap fails closed"
        ),
        "shadow_price_source_rule": "exact dated pred_<D>.csv bytes SHA256",
        "internal_order": [
            "descending research_joint_proxy_score",
            "descending research_conditional_profit_score",
            "descending research_fill_proxy_score",
            "ascending ts_code",
        ],
        "top2_top3_exact_joint_tie_policy": (
            "for N at least 3, FAIL_CLOSED_NO_SELECTION_EVENT; not applicable "
            "for N below 3"
        ),
        "immutable_dated_artifact": (
            "data/decision_executable_profit/forward/selections/shadow_<D>.json"
        ),
        "latest_pointer_is_data_alias": False,
        "same_date_different_payload_policy": "REJECT",
        "out_of_order_or_backfill_policy": "REJECT",
    }
    _expect(
        dict(selection) == expected_selection,
        "internal contract selection boundary drifted",
    )
    expected_timing = {
        "timezone": "Asia/Shanghai",
        "calendar": "strict pinned SSE trade calendar",
        "date_binding": "D, T and T+1 are three adjacent SSE open sessions",
        "selection_window": (
            "after D close and strictly before T 09:20 Asia/Shanghai"
        ),
        "post_T_information_as_selection_input_allowed": False,
    }
    _expect(
        dict(timing) == expected_timing,
        "internal contract timing boundary drifted",
    )
    _expect(
        contract.get("release_blockers")
        == [
            "both historical challenger families were rejected",
            "last 180 historical dates were viewed and are not untouched confirmation",
            "no forward selection dates have been accumulated yet",
            "scores are not calibrated probabilities",
            "formal return-LCB and big-loss tie-break outputs do not exist",
            "full forward auction capacity and actual fill truth are not model labels",
            "minimum 180 new forward signal dates and all formal release gates remain required",
        ],
        "internal contract release blockers drifted",
    )


@dataclass(frozen=True)
class LoadedInternalChallenger:
    bundle: Mapping[str, Any]
    audit: Mapping[str, Any]
    index: Mapping[str, Any]
    internal_contract: Mapping[str, Any]
    lagged_priors: ModuleType
    feature_columns: tuple[str, ...]
    raw_base_features: tuple[str, ...]
    lagged_features: tuple[str, ...]
    source_hashes: Mapping[str, str]


def load_canonical_frozen_promotion_topn(
    repo_root: Path,
) -> tuple[Path, dict[str, Any]]:
    repo_root = repo_root.resolve()
    pointer_path = _safe_file(
        repo_root,
        Path("outputs/decision/three_rank_index.json"),
        label="canonical three-rank pointer",
    )
    pointer = _read_json(pointer_path, label="canonical three-rank pointer")
    try:
        validate_three_rank_index(pointer)
    except Exception as exc:
        raise ExecutableProfitShadowError(
            "canonical three-rank pointer is invalid"
        ) from exc
    _expect(
        pointer.get("index_kind") == "dated_three_rank_pointer_only",
        "canonical three-rank pointer kind drifted",
    )
    signal_date = _normal_date(pointer.get("latest_signal_date"))
    expected_contract_relative = Path(
        f"outputs/decision/three_rank_top10_{signal_date}.json"
    )
    _expect(
        pointer.get("latest_contract_url")
        == expected_contract_relative.as_posix(),
        "canonical three-rank pointer target is not the exact dated contract",
    )
    contract_path = _safe_file(
        repo_root,
        expected_contract_relative,
        label="canonical dated three-rank contract",
    )
    _expect(
        _sha256(contract_path) == pointer.get("latest_contract_sha256"),
        "canonical dated three-rank contract SHA drifted",
    )
    contract = _read_json(
        contract_path,
        label="canonical dated three-rank contract",
    )
    try:
        validate_three_rank_contract(contract)
    except Exception as exc:
        raise ExecutableProfitShadowError(
            "canonical dated three-rank contract is invalid"
        ) from exc
    downloads = contract.get("downloads")
    _expect(
        isinstance(downloads, Mapping),
        "canonical dated three-rank contract lacks downloads",
    )
    csv_relative = Path(f"outputs/decision/three_rank_top10_{signal_date}.csv")
    _expect(
        pointer.get("latest_csv_url") == csv_relative.as_posix()
        and downloads.get("json_url") == expected_contract_relative.as_posix()
        and downloads.get("csv_url") == csv_relative.as_posix(),
        "canonical three-rank dated download URLs drifted",
    )
    csv_path = _safe_file(
        repo_root,
        csv_relative,
        label="canonical dated three-rank CSV",
    )
    _expect(
        _sha256(csv_path)
        == pointer.get("latest_csv_sha256")
        == downloads.get("csv_sha256"),
        "canonical dated three-rank CSV SHA drifted",
    )
    _expect(
        contract.get("signal_date") == pointer.get("latest_signal_date")
        and contract.get("exec_date") == pointer.get("latest_exec_date")
        and contract.get("exit_date") == pointer.get("latest_exit_date")
        and contract.get("status") == pointer.get("latest_status")
        and contract.get("bundle_sha256") == pointer.get("latest_bundle_sha256")
        and contract.get("top10_members_sha256")
        == pointer.get("latest_top10_members_sha256"),
        "canonical three-rank pointer disagrees with its dated contract",
    )
    return contract_path, contract


def _load_hash_bound_lagged_module(path: Path) -> ModuleType:
    # The source is executed only after both its hard pin and index pin passed.
    name = f"_dc20_hash_bound_lagged_priors_{EXPECTED_LAGGED_PRIORS_SHA256[:16]}_{id(path)}"
    spec = importlib.util.spec_from_file_location(name, path)
    _expect(spec is not None and spec.loader is not None, "cannot load hash-bound lagged-prior code")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def load_internal_challenger(
    repo_root: Path,
    *,
    work_root: Path | None = None,
    contract_path: Path | None = None,
) -> LoadedInternalChallenger:
    repo_root = repo_root.resolve()
    if work_root is None:
        work_root = _safe_directory(
            repo_root,
            DEFAULT_WORK_ROOT,
            label="canonical challenger work root",
        )
    else:
        supplied_work_root = Path(os.path.abspath(work_root))
        _expect(
            supplied_work_root.resolve(strict=True) == supplied_work_root
            and supplied_work_root.is_dir()
            and not supplied_work_root.is_symlink(),
            "supplied challenger work root is unsafe",
        )
        work_root = supplied_work_root
    if contract_path is None:
        contract_path = _safe_file(
            repo_root,
            DEFAULT_CONTRACT_PATH,
            label="internal challenger contract",
        )
    else:
        contract_path = _safe_absolute_file(
            contract_path,
            label="supplied internal challenger contract",
        )

    formal_path = _safe_file(repo_root, DEFAULT_FORMAL_CONTRACT_PATH, label="formal shadow contract")
    formal_sha = _sha256(formal_path)
    _expect(formal_sha == EXPECTED_FORMAL_CONTRACT_SHA256, "formal shadow contract SHA drifted")
    internal_contract = _read_json(contract_path, label="internal challenger contract")
    _validate_internal_contract(internal_contract, formal_contract_sha256=formal_sha)

    index_path = _safe_file(work_root, Path("ARTIFACT_INDEX.json"), label="artifact index")
    _expect(_sha256(index_path) == EXPECTED_ARTIFACT_INDEX_SHA256, "artifact index SHA drifted")
    index = _read_json(index_path, label="artifact index")
    _expect(index.get("status") == ARTIFACT_STATUS, "artifact index is not research NOT_READY")
    _expect(index.get("scope") == "research_only", "artifact index escaped research scope")
    publication = index.get("publication_boundary")
    _expect(isinstance(publication, Mapping), "artifact index publication boundary missing")
    for key in ("front_end_rank_allowed", "official_trade_action_allowed", "production_model_publish_allowed"):
        _expect(publication.get(key) is False, f"artifact index enabled {key}")

    artifact_specs = index.get("artifacts")
    code_specs = index.get("code")
    _expect(isinstance(artifact_specs, Mapping) and isinstance(code_specs, Mapping), "artifact index bindings missing")
    required_files = {
        "model": (Path("outputs/internal_forward_challenger.pkl"), EXPECTED_MODEL_SHA256),
        "audit": (Path("outputs/internal_forward_challenger_audit.json"), EXPECTED_AUDIT_SHA256),
        "lagged_manifest": (Path("outputs/lagged_priors_manifest.json"), str(artifact_specs.get("lagged_priors_manifest.json", {}).get("sha256") or "")),
        "lagged_code": (Path("lagged_priors.py"), EXPECTED_LAGGED_PRIORS_SHA256),
    }
    paths: dict[str, Path] = {}
    for label, (relative, expected) in required_files.items():
        _expect(SHA256_RE.fullmatch(expected) is not None, f"index {label} SHA missing")
        path = _safe_file(work_root, relative, label=label)
        _expect(_sha256(path) == expected, f"{label} SHA drifted")
        paths[label] = path
    _expect(code_specs.get("lagged_priors.py") == EXPECTED_LAGGED_PRIORS_SHA256, "lagged code/index binding drifted")
    for artifact_name, expected in (
        ("internal_forward_challenger.pkl", EXPECTED_MODEL_SHA256),
        ("internal_forward_challenger_audit.json", EXPECTED_AUDIT_SHA256),
    ):
        spec_value = artifact_specs.get(artifact_name)
        _expect(isinstance(spec_value, Mapping) and spec_value.get("sha256") == expected, f"index {artifact_name} binding drifted")
        _expect(paths["model" if artifact_name.endswith(".pkl") else "audit"].stat().st_size == spec_value.get("bytes"), f"{artifact_name} byte count drifted")
    _expect(
        artifact_specs["internal_forward_challenger.pkl"].get("trusted_repository_artifact_only") is True,
        "pickle is not marked trusted repository artifact",
    )

    audit = _read_json(paths["audit"], label="challenger audit")
    _expect(audit.get("status") == ARTIFACT_STATUS, "challenger audit is not research NOT_READY")
    for key in ("front_end_rank_allowed", "official_trade_action_allowed", "historical_effect_claim_allowed", "independent_untouched_confirmation_available", "forward_release_evidence_available"):
        _expect(audit.get(key) is False, f"challenger audit enabled {key}")
    _expect(audit.get("retrospective_confirmation_window_has_been_viewed") is True, "viewed retrospective disclosure missing")
    _expect(audit.get("feature_count") == 156, "challenger feature count drifted")
    _expect(audit.get("feature_columns_sha256") == EXPECTED_ALL_FEATURES_SHA256, "challenger feature hash drifted")
    _expect(audit.get("artifact", {}).get("sha256") == EXPECTED_MODEL_SHA256, "audit/model binding drifted")

    # Only now, after the contract, index, audit, code and pickle SHA checks,
    # execute the trusted repository code and deserialize the trusted pickle.
    lagged_module = _load_hash_bound_lagged_module(paths["lagged_code"])
    try:
        bundle = pickle.loads(paths["model"].read_bytes())
    except Exception as exc:
        raise ExecutableProfitShadowError("trusted challenger pickle is unreadable") from exc
    _expect(isinstance(bundle, Mapping), "challenger pickle payload is invalid")
    _expect(bundle.get("schema_version") == audit.get("schema_version"), "pickle/audit schema drifted")
    _expect(bundle.get("status") == ARTIFACT_STATUS, "pickle is not strict research NOT_READY")
    _expect(bundle.get("variant") == "full_priors", "pickle is not the full-history prior variant")
    features = bundle.get("feature_columns")
    _expect(isinstance(features, list) and len(features) == 156 and len(set(features)) == 156, "pickle feature inventory invalid")
    _expect(_canonical_sha256(features) == EXPECTED_ALL_FEATURES_SHA256, "pickle feature inventory hash drifted")
    for key in ("fill_model", "conditional_profit_model"):
        _expect(callable(getattr(bundle.get(key), "predict_proba", None)), f"pickle {key} is invalid")
    training = bundle.get("training_audit")
    _expect(isinstance(training, Mapping), "pickle training audit missing")
    _expect(_normal_date(training.get("maximum_used_scheduled_exit_date")) < MINIMUM_SIGNAL_DATE, "challenger training truth reaches first forward D")

    manifest_path = _safe_file(repo_root, DEFAULT_FEATURE_MANIFEST_PATH, label="base feature manifest")
    _expect(
        _sha256(manifest_path) == EXPECTED_HISTORICAL_MANIFEST_SHA256,
        "historical base feature manifest SHA drifted",
    )
    manifest = _read_json(manifest_path, label="base feature manifest")
    feature_contract = manifest.get("feature_contract")
    _expect(isinstance(feature_contract, Mapping), "base feature contract missing")
    base_features = feature_contract.get("columns")
    _expect(isinstance(base_features, list) and len(base_features) == 48, "base feature inventory is not 48")
    _expect(base_features[-4:] == list(DERIVED_BASE_FEATURES), "derived stage/board features drifted")
    _expect(feature_contract.get("columns_sha256") == EXPECTED_BASE_FEATURES_SHA256, "base manifest feature hash drifted")
    _expect(_canonical_sha256(base_features) == EXPECTED_BASE_FEATURES_SHA256, "base feature inventory hash invalid")
    _expect(features[:48] == base_features, "pickle/base manifest feature order drifted")
    _expect(set(PROMOTION_SOURCE_FEATURES).issubset(base_features[:44]), "18 promotion-source features are not in the D base surface")

    prior_manifest = _read_json(paths["lagged_manifest"], label="lagged-prior manifest")
    full_prior = prior_manifest.get("outputs", {}).get("full")
    expected_lagged = list(lagged_module.feature_columns("fullhist"))
    _expect(isinstance(full_prior, Mapping), "full-history prior manifest entry missing")
    _expect(full_prior.get("feature_columns") == expected_lagged, "lagged-prior manifest/code inventory drifted")
    _expect(len(expected_lagged) == 108 and features[48:] == expected_lagged, "pickle is not 48 base plus 108 full-history priors")

    inputs = index.get("inputs")
    manifest_inputs = manifest.get("inputs")
    _expect(isinstance(inputs, Mapping) and isinstance(manifest_inputs, Mapping), "source input bindings missing")
    history_spec = manifest_inputs.get("five_year_source_ledger")
    calendar_spec = manifest_inputs.get("strict_sse_calendar")
    _expect(isinstance(history_spec, Mapping) and history_spec.get("path") == DEFAULT_HISTORY_LEDGER_PATH.as_posix(), "history ledger path drifted")
    _expect(isinstance(calendar_spec, Mapping) and calendar_spec.get("path") == DEFAULT_CALENDAR_PATH.as_posix(), "calendar path drifted")
    history_path = _safe_file(repo_root, DEFAULT_HISTORY_LEDGER_PATH, label="full-history ledger")
    calendar_path = _safe_file(repo_root, DEFAULT_CALENDAR_PATH, label="strict SSE calendar")
    history_sha = _sha256(history_path)
    calendar_sha = _sha256(calendar_path)
    _expect(
        history_sha
        == EXPECTED_FULL_HISTORY_LEDGER_SHA256
        == inputs.get("five_year_hard_pool_ledger_sha256")
        == history_spec.get("sha256"),
        "history ledger SHA binding drifted",
    )
    _expect(calendar_sha == EXPECTED_CALENDAR_SHA256 == inputs.get("strict_sse_calendar_sha256") == calendar_spec.get("sha256"), "strict SSE calendar SHA binding drifted")
    historical_output = manifest.get("output")
    _expect(isinstance(historical_output, Mapping) and historical_output.get("sha256") == inputs.get("historical_oof_top10_ledger_sha256"), "training OOF ledger binding drifted")

    return LoadedInternalChallenger(
        bundle=bundle,
        audit=audit,
        index=index,
        internal_contract=internal_contract,
        lagged_priors=lagged_module,
        feature_columns=tuple(features),
        raw_base_features=tuple(base_features[:44]),
        lagged_features=tuple(expected_lagged),
        source_hashes={
            "formal_contract_sha256": formal_sha,
            "artifact_index_sha256": EXPECTED_ARTIFACT_INDEX_SHA256,
            "audit_sha256": EXPECTED_AUDIT_SHA256,
            "model_pickle_sha256": EXPECTED_MODEL_SHA256,
            "lagged_priors_sha256": EXPECTED_LAGGED_PRIORS_SHA256,
            "strict_sse_calendar_sha256": calendar_sha,
            "full_history_ledger_sha256": history_sha,
            "historical_feature_manifest_sha256": EXPECTED_HISTORICAL_MANIFEST_SHA256,
        },
    )


def _strict_top10_targets(
    frozen_top10: Mapping[str, Any],
    open_dates: Sequence[str],
) -> pd.DataFrame:
    try:
        validate_three_rank_contract(frozen_top10)
    except Exception as exc:
        raise ExecutableProfitShadowError("frozen promotion Top10 contract is invalid") from exc
    signal_date = _normal_date(frozen_top10.get("signal_date"))
    exec_date = _normal_date(frozen_top10.get("exec_date"))
    exit_date = _normal_date(frozen_top10.get("exit_date"))
    _expect(signal_date >= MINIMUM_SIGNAL_DATE, f"internal Shadow requires D >= {MINIMUM_SIGNAL_DATE}")
    positions = {date: index for index, date in enumerate(open_dates)}
    position = positions.get(signal_date)
    _expect(
        position is not None
        and position + 2 < len(open_dates)
        and open_dates[position + 1] == exec_date
        and open_dates[position + 2] == exit_date,
        "frozen Top10 does not use adjacent strict SSE D/T/T+1 sessions",
    )
    rows = frozen_top10.get("rows")
    _expect(
        isinstance(rows, list) and 0 <= len(rows) <= 10,
        "internal Shadow requires the complete frozen promotion TopN (0..10)",
    )
    candidate_count = len(rows)
    _expect(
        frozen_top10.get("top10_count") == candidate_count,
        "frozen promotion TopN count drifted",
    )
    promotion = frozen_top10.get("models", {}).get("promotion", {})
    _expect(promotion.get("status") == "READY", "frozen promotion model is not READY")
    ranks = [row.get("promotion_rank") for row in rows if isinstance(row, Mapping)]
    _expect(
        len(ranks) == candidate_count
        and all(type(rank) is int for rank in ranks)
        and sorted(ranks) == list(range(1, candidate_count + 1)),
        "frozen promotion ranks are not a complete 1..N permutation",
    )
    codes = [_normal_code(row.get("ts_code")) for row in rows]
    _expect(
        all(codes) and len(set(codes)) == candidate_count,
        "frozen promotion TopN codes are invalid",
    )
    _expect(
        frozen_top10.get("top10_members_sha256") == top10_members_sha256(signal_date, codes),
        "frozen Top10 member hash drifted",
    )
    records: list[dict[str, Any]] = []
    for row, code in zip(rows, codes):
        transition = str(row.get("stage_transition") or "")
        _expect(transition in {"2→3", "3→4"}, "frozen Top10 escaped hard stage scope")
        stage = 2 if transition == "2→3" else 3
        sh_main = re.fullmatch(r"(?:600|601|603|605)\d{3}\.SH", code) is not None
        sz_main = re.fullmatch(r"(?:000|001|002|003)\d{3}\.SZ", code) is not None
        _expect(
            sh_main or sz_main,
            f"frozen Top10 includes non-main-board code: {code}",
        )
        board = "SH_MAIN" if sh_main else "SZ_MAIN"
        records.append(
            {
                "signal_date": signal_date,
                "exec_date": exec_date,
                "exit_date": exit_date,
                "ts_code": code,
                "name": str(row.get("name") or ""),
                "industry": str(row.get("industry") or ""),
                "stage": stage,
                "stage_transition": transition,
                "board": board,
                "promotion_rank": int(row["promotion_rank"]),
                "predicted_promotion_probability": float(row["predicted_promotion_probability"]),
            }
        )
    columns = [
        "signal_date",
        "exec_date",
        "exit_date",
        "ts_code",
        "name",
        "industry",
        "stage",
        "stage_transition",
        "board",
        "promotion_rank",
        "predicted_promotion_probability",
    ]
    return (
        pd.DataFrame.from_records(records, columns=columns)
        .sort_values("promotion_rank", kind="stable")
        .reset_index(drop=True)
    )


def _prepare_base_features(
    base_features: pd.DataFrame,
    targets: pd.DataFrame,
    loaded: LoadedInternalChallenger,
    *,
    frozen_feature_snapshot_sha256: str,
) -> pd.DataFrame:
    candidate_count = len(targets)
    _expect(
        len(base_features) == candidate_count,
        "D feature file must contain the complete frozen promotion TopN",
    )
    required_identity = {
        "signal_date",
        "ts_code",
        "stage",
        "board",
        "feature_snapshot_sha256",
        "generated_at_utc",
    }
    _expect(required_identity.issubset(base_features.columns), "D feature file lacks date/code/snapshot identity")
    _expect(set(PROMOTION_SOURCE_FEATURES).issubset(base_features.columns), "old D/pred surface rejected: 18 promotion-source features are required")
    missing = sorted(set(loaded.raw_base_features) - set(base_features.columns))
    _expect(not missing, f"D feature file lacks trained base features: {missing}")
    frame = base_features.copy()
    frame["signal_date"] = frame["signal_date"].map(_normal_date)
    frame["ts_code"] = frame["ts_code"].map(_normal_code)
    signal_date = str(targets["signal_date"].iloc[0])
    _expect(frame["signal_date"].eq(signal_date).all(), "D feature file mixes or misbinds signal dates")
    _expect(not frame["ts_code"].eq("").any() and not frame["ts_code"].duplicated().any(), "D feature keys are invalid")
    _expect(set(frame["ts_code"]) == set(targets["ts_code"]), "D feature membership drifted from frozen Top10")
    snapshot_values = frame["feature_snapshot_sha256"].fillna("").astype(str).unique().tolist()
    _expect(
        snapshot_values == [frozen_feature_snapshot_sha256]
        and SHA256_RE.fullmatch(frozen_feature_snapshot_sha256) is not None,
        "D feature file is not bound to the frozen promotion feature snapshot",
    )
    generated_values = frame["generated_at_utc"].fillna("").astype(str).unique().tolist()
    _expect(len(generated_values) == 1 and generated_values[0], "D feature generated_at_utc is not uniform")
    generated = _parse_aware_datetime(
        generated_values[0],
        label="D feature generated_at_utc",
    ).astimezone(ZoneInfo("Asia/Shanghai"))
    start, end = _selection_window(
        signal_date,
        str(targets["exec_date"].iloc[0]),
    )
    _expect(
        start < generated < end,
        "D feature file was not generated after D close and before T 09:20",
    )
    joined = targets.merge(frame, on=["signal_date", "ts_code"], how="left", validate="one_to_one", suffixes=("", "_input"))
    supplied = pd.to_numeric(joined["stage_input"], errors="coerce").round()
    _expect(supplied.eq(joined["stage"]).all(), "D feature stage drifted from frozen Top10")
    supplied_board = joined["board_input"].fillna("").astype(str).str.upper()
    _expect(
        supplied_board.eq(joined["board"]).all(),
        "D feature board drifted from frozen Top10/main-board code",
    )
    for column in loaded.raw_base_features:
        original = joined[column]
        numeric = pd.to_numeric(original, errors="coerce").replace([np.inf, -np.inf], np.nan)
        invalid = original.notna() & original.astype(str).str.strip().ne("") & numeric.isna()
        _expect(not invalid.any(), f"D base feature is nonnumeric: {column}")
        _expect(numeric.notna().any(), f"D base feature is entirely missing: {column}")
        joined[column] = numeric
    joined["stage_2"] = joined["stage"].eq(2).astype(float)
    joined["stage_3"] = joined["stage"].eq(3).astype(float)
    joined["board_sh_main"] = joined["board"].eq("SH_MAIN").astype(float)
    joined["board_sz_main"] = joined["board"].eq("SZ_MAIN").astype(float)
    _expect(
        list(loaded.feature_columns[:48]) == [*loaded.raw_base_features, *DERIVED_BASE_FEATURES],
        "loaded base feature order drifted",
    )
    return joined.sort_values("promotion_rank", kind="stable").reset_index(drop=True)


def build_strict_lagged_priors(
    *,
    history: pd.DataFrame,
    targets: pd.DataFrame,
    open_dates: Sequence[str],
    lagged_module: ModuleType,
) -> pd.DataFrame:
    priors = lagged_module.build_lagged_features(
        history=history,
        targets=targets,
        open_dates=open_dates,
        source_kind="full",
        prefix="fullhist",
    )
    _expect(len(priors) == len(targets), "lagged prior row count drifted")
    _expect(
        (
            priors["lagged_prior_max_history_exit_date"].eq("")
            | priors["lagged_prior_max_history_exit_date"].lt(priors["signal_date"])
        ).all(),
        "same-day or future outcome truth leaked into lagged priors",
    )
    return priors


def _feature_snapshot_sha256(
    frame: pd.DataFrame,
    feature_columns: Sequence[str],
    *,
    source_file_sha256: str,
    generated_at_utc: str,
) -> str:
    rows: list[dict[str, Any]] = []
    for row in frame.sort_values("ts_code", kind="stable").itertuples(index=False):
        values: dict[str, str | None] = {}
        for column in feature_columns:
            value = getattr(row, column)
            number = float(value) if not pd.isna(value) else float("nan")
            values[column] = format(number, ".12g") if math.isfinite(number) else None
        rows.append({"ts_code": row.ts_code, "values": values})
    return _canonical_sha256(
        {
            "schema": "dc20_internal_executable_profit_156_feature_snapshot_v1",
            "signal_date": str(frame["signal_date"].iloc[0]),
            "source_dated_pred_file_sha256": source_file_sha256,
            "source_generated_at_utc": generated_at_utc,
            "rows": rows,
        }
    )


def _promotion_feature_snapshot_sha256(
    full_surface: pd.DataFrame,
    loaded: LoadedInternalChallenger,
    *,
    signal_date: str,
) -> str:
    _expect(
        list(loaded.feature_columns[:48])
        == [*loaded.raw_base_features, *DERIVED_BASE_FEATURES],
        "promotion snapshot feature order drifted",
    )
    required = {
        "signal_date",
        "ts_code",
        "stage",
        "board",
        *loaded.raw_base_features,
    }
    _expect(
        required.issubset(full_surface.columns),
        "full D surface lacks promotion snapshot features",
    )
    frame = full_surface.copy()
    frame["signal_date"] = frame["signal_date"].map(_normal_date)
    frame["ts_code"] = frame["ts_code"].map(_normal_code)
    frame["stage"] = pd.to_numeric(frame["stage"], errors="coerce")
    frame["board"] = frame["board"].fillna("").astype(str).str.upper()
    _expect(
        frame["signal_date"].eq(signal_date).all()
        and frame["stage"].isin((2, 3)).all(),
        "full D surface escaped the frozen date/stage scope",
    )
    sh_main = frame["ts_code"].str.fullmatch(r"(?:600|601|603|605)\d{3}\.SH")
    sz_main = frame["ts_code"].str.fullmatch(r"(?:000|001|002|003)\d{3}\.SZ")
    _expect(
        (sh_main | sz_main).all()
        and frame["board"].eq(
            pd.Series(
                np.where(sh_main, "SH_MAIN", "SZ_MAIN"),
                index=frame.index,
            )
        ).all(),
        "full D surface contains a non-main-board or mismatched board",
    )
    for column in loaded.raw_base_features:
        original = frame[column]
        numeric = pd.to_numeric(original, errors="coerce").replace(
            [np.inf, -np.inf], np.nan
        )
        invalid = (
            original.notna()
            & original.astype(str).str.strip().ne("")
            & numeric.isna()
        )
        _expect(not invalid.any(), f"full D feature is nonnumeric: {column}")
        _expect(numeric.notna().any(), f"full D feature is entirely missing: {column}")
        frame[column] = numeric
    frame["stage_2"] = frame["stage"].eq(2).astype(float)
    frame["stage_3"] = frame["stage"].eq(3).astype(float)
    frame["board_sh_main"] = frame["board"].eq("SH_MAIN").astype(float)
    frame["board_sz_main"] = frame["board"].eq("SZ_MAIN").astype(float)
    feature_columns = list(loaded.feature_columns[:48])
    records: list[dict[str, Any]] = []
    for _, row in frame.sort_values("ts_code", kind="stable").iterrows():
        values: dict[str, str | None] = {}
        for name in feature_columns:
            value = row[name]
            if pd.isna(value):
                values[name] = None
                continue
            number = float(value)
            values[name] = "0" if number == 0.0 else format(number, ".12g")
        records.append({"ts_code": str(row["ts_code"]), "values": values})
    return _canonical_sha256(
        {
            "schema": "dc20_three_engine_d_feature_snapshot_v2_quantized12",
            "signal_date": signal_date,
            "features": records,
        }
    )


def _empty_internal_forward_shadow_payload(
    *,
    frozen_top10: Mapping[str, Any],
    base_features: pd.DataFrame,
    d_feature_source_name: str,
    d_feature_source_sha256: str,
) -> dict[str, Any]:
    """Freeze an honest zero-candidate event without loading or running a model."""

    signal_date = _normal_date(frozen_top10.get("signal_date"))
    exec_date = _normal_date(frozen_top10.get("exec_date"))
    exit_date = _normal_date(frozen_top10.get("exit_date"))
    _expect(base_features.empty, "zero-candidate event contains D feature rows")
    required_headers = {
        "signal_date",
        "ts_code",
        "stage",
        "board",
        "generated_at_utc",
        "feature_snapshot_sha256",
        *PROMOTION_SOURCE_FEATURES,
    }
    _expect(
        required_headers.issubset(base_features.columns),
        "zero-candidate D source lacks its frozen feature headers",
    )
    _expect(
        Path(d_feature_source_name).name == f"pred_{signal_date}.csv",
        "D feature input is not the exact dated pred_<D>.csv surface",
    )
    _expect(
        SHA256_RE.fullmatch(d_feature_source_sha256) is not None,
        "D feature source file SHA is invalid",
    )
    generated_at_utc = str(frozen_top10.get("generated_at_utc") or "")
    generated = _parse_aware_datetime(
        generated_at_utc,
        label="empty-event frozen promotion generated_at_utc",
    ).astimezone(ZoneInfo("Asia/Shanghai"))
    start, end = _selection_window(signal_date, exec_date)
    _expect(
        start < generated < end,
        "empty-event source timing escaped D-close/T-09:20",
    )
    source_feature_snapshot = str(
        frozen_top10.get("feature_snapshot_sha256") or ""
    )
    _expect(
        not source_feature_snapshot
        or SHA256_RE.fullmatch(source_feature_snapshot) is not None,
        "empty-event frozen promotion feature snapshot is invalid",
    )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": ARTIFACT_KIND,
        "contract_id": CONTRACT_ID,
        "status": INTERNAL_STATUS,
        "research_only": True,
        "proxy_scores_uncalibrated": True,
        "score_semantics": {
            "research_fill_proxy_score": "historical public daily-bar buyability proxy; not actual order fill probability",
            "research_conditional_profit_score": "uncalibrated conditional research score among proxy-buyable rows",
            "research_joint_proxy_score": "exact product of the two uncalibrated research proxy scores",
        },
        "signal_date": signal_date,
        "exec_date": exec_date,
        "exit_date": exit_date,
        "feature_as_of_date": signal_date,
        "top10_count": 0,
        "top10_members_sha256": str(frozen_top10["top10_members_sha256"]),
        "source_promotion": {
            "authority": "complete_frozen_promotion_topn_only",
            "source_bundle_sha256": str(frozen_top10["bundle_sha256"]),
            "source_feature_snapshot_sha256": source_feature_snapshot or None,
            "source_top10_members_sha256": str(
                frozen_top10["top10_members_sha256"]
            ),
            "membership_and_promotion_ranks_may_change": False,
        },
        "source_d_feature": {
            "file_name": Path(d_feature_source_name).name,
            "file_sha256": d_feature_source_sha256,
            "generated_at_utc": generated_at_utc,
            "generated_at_source": "frozen_promotion_contract_empty_event",
            "selected_row_count": 0,
            "required_promotion_source_features_present": True,
            "old_feature_incomplete_prediction_allowed": False,
        },
        "model": {
            "status": INTERNAL_STATUS,
            "artifact_status": ARTIFACT_STATUS,
            "model_kind": "hgb",
            "variant": "full_priors",
            "artifact_sha256": EXPECTED_MODEL_SHA256,
            "model_loaded": False,
            "inference_performed": False,
            "calibrated_probability_output": False,
            "return_lcb_component_available": False,
            "big_loss_tie_break_available": False,
            "retrospective_window_was_viewed": True,
            "independent_untouched_confirmation_available": False,
            "forward_release_evidence_available": False,
        },
        "feature_contract": {
            "feature_count": 156,
            "base_feature_count": 48,
            "lagged_prior_feature_count": 108,
            "required_promotion_source_feature_count": len(
                PROMOTION_SOURCE_FEATURES
            ),
            "required_promotion_source_features": list(
                PROMOTION_SOURCE_FEATURES
            ),
            "feature_columns_sha256": EXPECTED_ALL_FEATURES_SHA256,
            "feature_snapshot_sha256": None,
            "lagged_prior_max_history_exit_date": None,
            "feature_rows_scored": 0,
            "lagged_prior_rows_built": 0,
            "empty_event_reason": "NO_HARD_SCOPE_CANDIDATES",
            "strict_history_availability_rule": "outcome availability date strictly before signal D",
        },
        "ranking_contract": {
            "candidate_scope": "complete frozen promotion TopN, 0<=N<=10",
            "primary_sort": "research_joint_proxy_score descending",
            "tie_breakers": [
                "research_conditional_profit_score descending",
                "research_fill_proxy_score descending",
                "ts_code ascending",
            ],
            "top2_top3_exact_joint_tie_policy": "FAIL_CLOSED_FOR_N_AT_LEAST_3",
            "shadow_slots": 2,
            "shadow_slot_rule": "min(2, N); no padding",
            "top2_frozen_before_outcome_truth": True,
            "entry_policy_id": ENTRY_POLICY_ID,
            "entry_price_rule": "T proxy open must not exceed D-frozen shadow_max_price",
            "actual_order_fill_claimed": False,
        },
        "boundaries": {
            "front_end_rank_allowed": False,
            "official_trade_action_allowed": False,
            "production_model_publish_allowed": False,
            "workflow_connected": False,
            "may_change_promotion_membership": False,
            "may_override_promotion_rank": False,
            "may_create_trade_action": False,
            "actual_order_fill_observed": False,
            "actual_execution_claimed": False,
        },
        "source_hashes": {
            "formal_contract_sha256": EXPECTED_FORMAL_CONTRACT_SHA256,
            "artifact_index_sha256": EXPECTED_ARTIFACT_INDEX_SHA256,
            "audit_sha256": EXPECTED_AUDIT_SHA256,
            "model_pickle_sha256": EXPECTED_MODEL_SHA256,
            "lagged_priors_sha256": EXPECTED_LAGGED_PRIORS_SHA256,
            "strict_sse_calendar_sha256": EXPECTED_CALENDAR_SHA256,
            "full_history_ledger_sha256": EXPECTED_FULL_HISTORY_LEDGER_SHA256,
            "historical_feature_manifest_sha256": EXPECTED_HISTORICAL_MANIFEST_SHA256,
        },
        "rows": [],
        "shadow_top2": {
            "status": "NO_HARD_SCOPE_CANDIDATES",
            "requested_slots": 2,
            "actual_slots": 0,
            "rows": [],
        },
    }
    payload["snapshot_sha256"] = _canonical_sha256(payload)
    validate_internal_forward_shadow_payload(payload)
    return payload


def _score_internal_forward_shadow_frame(
    *,
    repo_root: Path,
    frozen_top10: Mapping[str, Any],
    base_features: pd.DataFrame,
    loaded: LoadedInternalChallenger | None = None,
    work_root: Path | None = None,
    contract_path: Path | None = None,
    d_feature_source_name: str,
    d_feature_source_sha256: str,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    open_dates = _read_pinned_sse_open_dates(repo_root)
    targets = _strict_top10_targets(frozen_top10, open_dates)
    signal_date = _normal_date(frozen_top10.get("signal_date"))
    _expect(
        Path(d_feature_source_name).name == f"pred_{signal_date}.csv",
        "D feature input is not the exact dated pred_<D>.csv surface",
    )
    _expect(
        SHA256_RE.fullmatch(d_feature_source_sha256) is not None,
        "D feature source file SHA is invalid",
    )
    if targets.empty:
        return _empty_internal_forward_shadow_payload(
            frozen_top10=frozen_top10,
            base_features=base_features,
            d_feature_source_name=d_feature_source_name,
            d_feature_source_sha256=d_feature_source_sha256,
        )
    loaded = loaded or load_internal_challenger(
        repo_root,
        work_root=work_root,
        contract_path=contract_path,
    )
    training = loaded.bundle["training_audit"]
    _expect(
        _normal_date(training.get("maximum_used_scheduled_exit_date")) < signal_date,
        "challenger training outcome truth is not strictly before D",
    )
    prepared = _prepare_base_features(
        base_features,
        targets,
        loaded,
        frozen_feature_snapshot_sha256=str(frozen_top10.get("feature_snapshot_sha256") or ""),
    )
    history = pd.read_csv(
        _safe_file(repo_root, DEFAULT_HISTORY_LEDGER_PATH, label="full-history ledger"),
        low_memory=False,
    )
    priors = build_strict_lagged_priors(
        history=history,
        targets=targets,
        open_dates=open_dates,
        lagged_module=loaded.lagged_priors,
    )
    prior_columns = ["signal_date", "ts_code", "lagged_prior_max_history_exit_date", "lagged_prior_snapshot_sha256", *loaded.lagged_features]
    frame = prepared.merge(
        priors[prior_columns],
        on=["signal_date", "ts_code"],
        how="left",
        validate="one_to_one",
    ).sort_values("promotion_rank", kind="stable").reset_index(drop=True)
    candidate_count = len(targets)
    _expect(
        len(frame) == candidate_count
        and frame[list(loaded.lagged_features)].notna().all().all(),
        "full-history lagged prior join is incomplete",
    )
    availability = (
        frame["lagged_prior_max_history_exit_date"].fillna("").astype(str)
    )
    _expect(
        availability.str.fullmatch(r"20\d{6}").all()
        and availability.isin(open_dates).all()
        and availability.lt(signal_date).all(),
        "lagged prior availability is not a valid pinned SSE session before D",
    )
    feature_columns = list(loaded.feature_columns)
    _expect(len(feature_columns) == 156, "challenger input is not 156 features")
    x = frame[feature_columns]
    fill = np.asarray(loaded.bundle["fill_model"].predict_proba(x)[:, 1], dtype=float)
    conditional = np.asarray(
        loaded.bundle["conditional_profit_model"].predict_proba(x)[:, 1],
        dtype=float,
    )
    _expect(
        fill.shape == conditional.shape == (candidate_count,),
        "challenger score shape drifted",
    )
    _expect(np.isfinite(fill).all() and np.isfinite(conditional).all(), "challenger emitted nonfinite scores")
    fill = np.clip(fill, 0.0, 1.0)
    conditional = np.clip(conditional, 0.0, 1.0)
    joint = fill * conditional
    _expect(np.all(joint <= fill + 1e-15) and np.all(joint <= conditional + 1e-15), "two-stage proxy identity upper bound failed")

    order = sorted(
        range(candidate_count),
        key=lambda index: (
            -float(joint[index]),
            -float(conditional[index]),
            -float(fill[index]),
            str(frame.iloc[index]["ts_code"]),
        ),
    )
    if candidate_count >= 3:
        _expect(
            float(joint[order[1]]) != float(joint[order[2]]),
            "exact Top2/Top3 joint proxy tie is not selectable",
        )
    shadow_slot_count = min(2, candidate_count)
    internal_order = {source_index: rank for rank, source_index in enumerate(order, start=1)}
    frozen_price_caps = {
        index: _frozen_shadow_price_cap(
            row.to_dict(),
            source_sha256=d_feature_source_sha256,
        )
        for index, row in frame.iterrows()
    }
    generated_at_utc = str(
        base_features["generated_at_utc"].fillna("").astype(str).iloc[0]
    )
    feature_snapshot = _feature_snapshot_sha256(
        frame,
        feature_columns,
        source_file_sha256=d_feature_source_sha256,
        generated_at_utc=generated_at_utc,
    )
    rows: list[dict[str, Any]] = []
    for index, row in frame.iterrows():
        rank = internal_order[index]
        shadow_max_price, shadow_price_basis, shadow_price_source_sha256 = (
            frozen_price_caps[index]
        )
        rows.append(
            {
                "ts_code": str(row["ts_code"]),
                "name": str(row["name"]),
                "industry": str(row["industry"]),
                "stage_transition": str(row["stage_transition"]),
                "promotion_rank": int(row["promotion_rank"]),
                "predicted_promotion_probability": float(row["predicted_promotion_probability"]),
                "research_fill_proxy_score": float(fill[index]),
                "research_conditional_profit_score": float(conditional[index]),
                "research_joint_proxy_score": float(joint[index]),
                "internal_shadow_order": int(rank),
                "internal_shadow_selected": int(rank <= shadow_slot_count),
                "shadow_slot": int(rank) if rank <= shadow_slot_count else None,
                "shadow_max_price": shadow_max_price,
                "shadow_price_basis": shadow_price_basis,
                "shadow_price_source_sha256": shadow_price_source_sha256,
                "lagged_prior_max_history_exit_date": str(row["lagged_prior_max_history_exit_date"]),
                "lagged_prior_snapshot_sha256": str(row["lagged_prior_snapshot_sha256"]),
            }
        )
    rows.sort(key=lambda row: int(row["internal_shadow_order"]))
    top2 = [
        {
            "shadow_slot": int(row["shadow_slot"]),
            "ts_code": row["ts_code"],
            "promotion_rank": row["promotion_rank"],
            "research_fill_proxy_score": row["research_fill_proxy_score"],
            "research_conditional_profit_score": row["research_conditional_profit_score"],
            "research_joint_proxy_score": row["research_joint_proxy_score"],
            "shadow_max_price": row["shadow_max_price"],
            "shadow_price_basis": row["shadow_price_basis"],
            "shadow_price_source_sha256": row["shadow_price_source_sha256"],
        }
        for row in rows[:shadow_slot_count]
    ]
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": ARTIFACT_KIND,
        "contract_id": str(loaded.internal_contract.get("contract_id") or CONTRACT_ID),
        "status": INTERNAL_STATUS,
        "research_only": True,
        "proxy_scores_uncalibrated": True,
        "score_semantics": {
            "research_fill_proxy_score": "historical public daily-bar buyability proxy; not actual order fill probability",
            "research_conditional_profit_score": "uncalibrated conditional research score among proxy-buyable rows",
            "research_joint_proxy_score": "exact product of the two uncalibrated research proxy scores",
        },
        "signal_date": signal_date,
        "exec_date": str(targets["exec_date"].iloc[0]),
        "exit_date": str(targets["exit_date"].iloc[0]),
        "feature_as_of_date": signal_date,
        "top10_count": candidate_count,
        "top10_members_sha256": str(frozen_top10["top10_members_sha256"]),
        "source_promotion": {
            "authority": "complete_frozen_promotion_topn_only",
            "source_bundle_sha256": str(frozen_top10["bundle_sha256"]),
            "source_feature_snapshot_sha256": str(frozen_top10["feature_snapshot_sha256"]),
            "source_top10_members_sha256": str(frozen_top10["top10_members_sha256"]),
            "membership_and_promotion_ranks_may_change": False,
        },
        "source_d_feature": {
            "file_name": Path(d_feature_source_name).name,
            "file_sha256": d_feature_source_sha256,
            "generated_at_utc": generated_at_utc,
            "generated_at_source": "dated_pred_row_uniform",
            "selected_row_count": candidate_count,
            "required_promotion_source_features_present": True,
            "old_feature_incomplete_prediction_allowed": False,
        },
        "model": {
            "status": INTERNAL_STATUS,
            "artifact_status": ARTIFACT_STATUS,
            "model_kind": str(loaded.bundle.get("model_kind") or ""),
            "variant": str(loaded.bundle.get("variant") or ""),
            "artifact_sha256": EXPECTED_MODEL_SHA256,
            "model_loaded": True,
            "inference_performed": True,
            "calibrated_probability_output": False,
            "return_lcb_component_available": False,
            "big_loss_tie_break_available": False,
            "retrospective_window_was_viewed": True,
            "independent_untouched_confirmation_available": False,
            "forward_release_evidence_available": False,
        },
        "feature_contract": {
            "feature_count": 156,
            "base_feature_count": 48,
            "lagged_prior_feature_count": 108,
            "required_promotion_source_feature_count": len(PROMOTION_SOURCE_FEATURES),
            "required_promotion_source_features": list(PROMOTION_SOURCE_FEATURES),
            "feature_columns_sha256": EXPECTED_ALL_FEATURES_SHA256,
            "feature_snapshot_sha256": feature_snapshot,
            "lagged_prior_max_history_exit_date": max(
                str(value) for value in frame["lagged_prior_max_history_exit_date"]
            ),
            "feature_rows_scored": candidate_count,
            "lagged_prior_rows_built": candidate_count,
            "empty_event_reason": None,
            "strict_history_availability_rule": "outcome availability date strictly before signal D",
        },
        "ranking_contract": {
            "candidate_scope": "complete frozen promotion TopN, 0<=N<=10",
            "primary_sort": "research_joint_proxy_score descending",
            "tie_breakers": [
                "research_conditional_profit_score descending",
                "research_fill_proxy_score descending",
                "ts_code ascending",
            ],
            "top2_top3_exact_joint_tie_policy": (
                "FAIL_CLOSED_FOR_N_AT_LEAST_3"
            ),
            "shadow_slots": 2,
            "shadow_slot_rule": "min(2, N); no padding",
            "top2_frozen_before_outcome_truth": True,
            "entry_policy_id": ENTRY_POLICY_ID,
            "entry_price_rule": "T proxy open must not exceed D-frozen shadow_max_price",
            "actual_order_fill_claimed": False,
        },
        "boundaries": {
            "front_end_rank_allowed": False,
            "official_trade_action_allowed": False,
            "production_model_publish_allowed": False,
            "workflow_connected": False,
            "may_change_promotion_membership": False,
            "may_override_promotion_rank": False,
            "may_create_trade_action": False,
            "actual_order_fill_observed": False,
            "actual_execution_claimed": False,
        },
        "source_hashes": dict(loaded.source_hashes),
        "rows": rows,
        "shadow_top2": {
            "status": "FROZEN_INTERNAL_RESEARCH_ONLY",
            "requested_slots": 2,
            "actual_slots": shadow_slot_count,
            "rows": top2,
        },
    }
    payload["snapshot_sha256"] = _canonical_sha256(payload)
    validate_internal_forward_shadow_payload(payload)
    return payload


def score_internal_forward_shadow(
    *,
    repo_root: Path,
    d_feature_path: Path,
) -> dict[str, Any]:
    """Score one immutable dated D feature file using its actual bytes and SHA."""

    repo_root = repo_root.resolve()
    _, frozen_top10 = load_canonical_frozen_promotion_topn(repo_root)
    signal_date = _normal_date(frozen_top10.get("signal_date"))
    expected_path = (
        repo_root
        / "outputs"
        / "auction_v3"
        / "predictions"
        / f"pred_{signal_date}.csv"
    )
    supplied_path = Path(d_feature_path)
    supplied_source_path = (
        supplied_path if supplied_path.is_absolute() else repo_root / supplied_path
    ).absolute()
    _expect(
        supplied_source_path == expected_path,
        "D feature source must be the repository's exact dated Auction prediction",
    )
    source_path = _safe_file(
        repo_root,
        expected_path.relative_to(repo_root),
        label="canonical dated D feature source",
    )
    try:
        source_bytes = source_path.read_bytes()
        features = pd.read_csv(io.BytesIO(source_bytes), low_memory=False)
    except (OSError, UnicodeError, pd.errors.ParserError) as exc:
        raise ExecutableProfitShadowError(
            f"invalid D feature source: {source_path}"
        ) from exc
    required_file_identity = {
        "signal_date",
        "ts_code",
        "stage",
        "board",
        "generated_at_utc",
        "feature_snapshot_sha256",
        "top10_selected",
    }
    _expect(
        required_file_identity.issubset(features.columns),
        "dated D feature file lacks its immutable full-surface identity",
    )
    _expect(
        features["signal_date"].map(_normal_date).eq(signal_date).all(),
        "dated D feature file contains a foreign signal date",
    )
    generated_values = (
        features["generated_at_utc"].fillna("").astype(str).unique().tolist()
    )
    snapshot_values = (
        features["feature_snapshot_sha256"].fillna("").astype(str).unique().tolist()
    )
    frozen_rows = frozen_top10.get("rows")
    _expect(
        isinstance(frozen_rows, list) and 0 <= len(frozen_rows) <= 10,
        "frozen promotion TopN row count is invalid",
    )
    candidate_count = len(frozen_rows)
    if candidate_count == 0:
        _expect(
            features.empty,
            "zero-candidate promotion event has unexpected D feature rows",
        )
        _expect(
            set(PROMOTION_SOURCE_FEATURES).issubset(features.columns),
            "zero-candidate D source lacks promotion feature headers",
        )
        _expect(
            generated_values == [] and snapshot_values == [],
            "zero-candidate D source contains unexpected row identity",
        )
        return _score_internal_forward_shadow_frame(
            repo_root=repo_root,
            frozen_top10=frozen_top10,
            base_features=features,
            loaded=None,
            d_feature_source_name=source_path.name,
            d_feature_source_sha256=_sha256_bytes(source_bytes),
        )
    _expect(
        len(generated_values) == 1 and bool(generated_values[0]),
        "dated D feature file generated_at_utc is not globally uniform",
    )
    _expect(
        snapshot_values
        == [str(frozen_top10.get("feature_snapshot_sha256") or "")],
        "dated D feature file snapshot is not globally bound to frozen promotion",
    )
    codes = features["ts_code"].map(_normal_code)
    _expect(
        not codes.eq("").any() and not codes.duplicated().any(),
        "dated D feature file contains invalid or duplicate full-pool codes",
    )
    selected_values = pd.to_numeric(features["top10_selected"], errors="coerce")
    _expect(
        selected_values.notna().all()
        and selected_values.isin((0, 1)).all()
        and int(selected_values.sum()) == candidate_count,
        "dated D feature file selected count disagrees with frozen promotion TopN",
    )
    loaded = load_internal_challenger(
        repo_root,
    )
    computed_promotion_snapshot = _promotion_feature_snapshot_sha256(
        features,
        loaded,
        signal_date=signal_date,
    )
    _expect(
        snapshot_values == [computed_promotion_snapshot],
        "dated D feature contents do not reproduce the frozen promotion snapshot",
    )
    features = features.loc[selected_values.eq(1)].copy()
    _expect(
        len(features) == candidate_count,
        "dated D feature file must resolve to the complete frozen promotion TopN",
    )
    return _score_internal_forward_shadow_frame(
        repo_root=repo_root,
        frozen_top10=frozen_top10,
        base_features=features,
        loaded=loaded,
        d_feature_source_name=source_path.name,
        d_feature_source_sha256=_sha256_bytes(source_bytes),
    )


def _payload_without_materialization_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(dict(payload))
    value.pop("snapshot_sha256", None)
    value.pop("downloads", None)
    return value


def validate_internal_forward_shadow_payload(
    payload: Mapping[str, Any],
    *,
    require_downloads: bool = False,
) -> None:
    expected_top_level = {
        "schema_version",
        "artifact_kind",
        "contract_id",
        "status",
        "research_only",
        "proxy_scores_uncalibrated",
        "score_semantics",
        "signal_date",
        "exec_date",
        "exit_date",
        "feature_as_of_date",
        "top10_count",
        "top10_members_sha256",
        "source_promotion",
        "source_d_feature",
        "model",
        "feature_contract",
        "ranking_contract",
        "boundaries",
        "source_hashes",
        "rows",
        "shadow_top2",
        "snapshot_sha256",
    }
    if require_downloads or "downloads" in payload:
        expected_top_level.add("downloads")
    _expect(
        set(payload) == expected_top_level,
        "internal Shadow top-level contract surface drifted",
    )
    _expect(payload.get("schema_version") == SCHEMA_VERSION, "internal Shadow schema invalid")
    _expect(payload.get("artifact_kind") == ARTIFACT_KIND, "internal Shadow kind invalid")
    _expect(payload.get("contract_id") == CONTRACT_ID, "internal Shadow contract id invalid")
    _expect(payload.get("status") == INTERNAL_STATUS, "internal Shadow is not NOT_READY")
    _expect(payload.get("research_only") is True and payload.get("proxy_scores_uncalibrated") is True, "internal Shadow research/proxy disclosure invalid")
    expected_score_semantics = {
        "research_fill_proxy_score": (
            "historical public daily-bar buyability proxy; not actual order fill probability"
        ),
        "research_conditional_profit_score": (
            "uncalibrated conditional research score among proxy-buyable rows"
        ),
        "research_joint_proxy_score": (
            "exact product of the two uncalibrated research proxy scores"
        ),
    }
    _expect(
        payload.get("score_semantics") == expected_score_semantics,
        "internal Shadow score semantics drifted",
    )
    signal_date = _normal_date(payload.get("signal_date"))
    exec_date = _normal_date(payload.get("exec_date"))
    exit_date = _normal_date(payload.get("exit_date"))
    _expect(signal_date >= MINIMUM_SIGNAL_DATE and signal_date < exec_date < exit_date, "internal Shadow dates invalid")
    rows = payload.get("rows")
    _expect(
        isinstance(rows, list) and 0 <= len(rows) <= 10,
        "internal Shadow rows are not the complete frozen promotion TopN",
    )
    candidate_count = len(rows)
    _expect(
        payload.get("signal_date") == signal_date
        and payload.get("exec_date") == exec_date
        and payload.get("exit_date") == exit_date
        and payload.get("feature_as_of_date") == signal_date
        and payload.get("top10_count") == candidate_count,
        "internal Shadow exact date/count binding invalid",
    )
    codes = [_normal_code(row.get("ts_code")) for row in rows if isinstance(row, Mapping)]
    _expect(
        len(codes) == candidate_count
        and all(codes)
        and len(set(codes)) == candidate_count,
        "internal Shadow row codes invalid",
    )
    _expect(payload.get("top10_members_sha256") == top10_members_sha256(signal_date, codes), "internal Shadow membership hash invalid")
    source_promotion = payload.get("source_promotion")
    _expect(
        isinstance(source_promotion, Mapping)
        and set(source_promotion)
        == {
            "authority",
            "source_bundle_sha256",
            "source_feature_snapshot_sha256",
            "source_top10_members_sha256",
            "membership_and_promotion_ranks_may_change",
        }
        and source_promotion.get("authority")
        == "complete_frozen_promotion_topn_only"
        and SHA256_RE.fullmatch(
            str(source_promotion.get("source_bundle_sha256") or "")
        )
        is not None
        and (
            (
                candidate_count == 0
                and (
                    source_promotion.get("source_feature_snapshot_sha256")
                    is None
                    or SHA256_RE.fullmatch(
                        str(
                            source_promotion.get(
                                "source_feature_snapshot_sha256"
                            )
                            or ""
                        )
                    )
                    is not None
                )
            )
            or (
                candidate_count > 0
                and SHA256_RE.fullmatch(
                    str(
                        source_promotion.get(
                            "source_feature_snapshot_sha256"
                        )
                        or ""
                    )
                )
                is not None
            )
        )
        and source_promotion.get("source_top10_members_sha256")
        == payload.get("top10_members_sha256")
        and source_promotion.get("membership_and_promotion_ranks_may_change")
        is False,
        "internal Shadow frozen promotion authority invalid",
    )
    source_d = payload.get("source_d_feature")
    _expect(
        isinstance(source_d, Mapping)
        and set(source_d)
        == {
            "file_name",
            "file_sha256",
            "generated_at_utc",
            "generated_at_source",
            "selected_row_count",
            "required_promotion_source_features_present",
            "old_feature_incomplete_prediction_allowed",
        }
        and source_d.get("file_name") == f"pred_{signal_date}.csv"
        and SHA256_RE.fullmatch(str(source_d.get("file_sha256") or "")) is not None
        and source_d.get("generated_at_source")
        == (
            "frozen_promotion_contract_empty_event"
            if candidate_count == 0
            else "dated_pred_row_uniform"
        )
        and source_d.get("selected_row_count") == candidate_count
        and source_d.get("required_promotion_source_features_present") is True
        and source_d.get("old_feature_incomplete_prediction_allowed") is False,
        "internal Shadow D feature source binding invalid",
    )
    generated = _parse_aware_datetime(
        source_d.get("generated_at_utc"),
        label="internal Shadow source generated_at_utc",
    ).astimezone(ZoneInfo("Asia/Shanghai"))
    start, end = _selection_window(signal_date, exec_date)
    _expect(start < generated < end, "internal Shadow source timing escaped D-close/T-09:20")
    model = payload.get("model")
    expected_model = {
        "status": INTERNAL_STATUS,
        "artifact_status": ARTIFACT_STATUS,
        "model_kind": "hgb",
        "variant": "full_priors",
        "artifact_sha256": EXPECTED_MODEL_SHA256,
        "model_loaded": candidate_count > 0,
        "inference_performed": candidate_count > 0,
        "calibrated_probability_output": False,
        "return_lcb_component_available": False,
        "big_loss_tie_break_available": False,
        "retrospective_window_was_viewed": True,
        "independent_untouched_confirmation_available": False,
        "forward_release_evidence_available": False,
    }
    _expect(model == expected_model, "internal Shadow model disclosure invalid")
    feature_contract = payload.get("feature_contract")
    _expect(isinstance(feature_contract, Mapping), "internal Shadow feature contract missing")
    expected_feature_keys = {
        "feature_count",
        "base_feature_count",
        "lagged_prior_feature_count",
        "required_promotion_source_feature_count",
        "required_promotion_source_features",
        "feature_columns_sha256",
        "feature_snapshot_sha256",
        "lagged_prior_max_history_exit_date",
        "feature_rows_scored",
        "lagged_prior_rows_built",
        "empty_event_reason",
        "strict_history_availability_rule",
    }
    _expect(
        set(feature_contract) == expected_feature_keys
        and feature_contract.get("feature_count") == 156
        and feature_contract.get("base_feature_count") == 48
        and feature_contract.get("lagged_prior_feature_count") == 108
        and feature_contract.get("required_promotion_source_feature_count")
        == len(PROMOTION_SOURCE_FEATURES)
        and feature_contract.get("required_promotion_source_features")
        == list(PROMOTION_SOURCE_FEATURES)
        and feature_contract.get("feature_columns_sha256")
        == EXPECTED_ALL_FEATURES_SHA256
        and feature_contract.get("feature_rows_scored") == candidate_count
        and feature_contract.get("lagged_prior_rows_built")
        == candidate_count
        and (
            (
                candidate_count == 0
                and feature_contract.get("feature_snapshot_sha256") is None
                and feature_contract.get(
                    "lagged_prior_max_history_exit_date"
                )
                is None
                and feature_contract.get("empty_event_reason")
                == "NO_HARD_SCOPE_CANDIDATES"
            )
            or (
                candidate_count > 0
                and SHA256_RE.fullmatch(
                    str(
                        feature_contract.get("feature_snapshot_sha256")
                        or ""
                    )
                )
                is not None
                and re.fullmatch(
                    r"20\d{6}",
                    str(
                        feature_contract.get(
                            "lagged_prior_max_history_exit_date"
                        )
                        or ""
                    ),
                )
                is not None
                and str(
                    feature_contract.get(
                        "lagged_prior_max_history_exit_date"
                    )
                    or ""
                )
                < signal_date
                and feature_contract.get("empty_event_reason") is None
            )
        )
        and feature_contract.get("strict_history_availability_rule")
        == "outcome availability date strictly before signal D",
        "internal Shadow feature contract invalid",
    )
    expected_ranking = {
        "candidate_scope": "complete frozen promotion TopN, 0<=N<=10",
        "primary_sort": "research_joint_proxy_score descending",
        "tie_breakers": [
            "research_conditional_profit_score descending",
            "research_fill_proxy_score descending",
            "ts_code ascending",
        ],
        "top2_top3_exact_joint_tie_policy": "FAIL_CLOSED_FOR_N_AT_LEAST_3",
        "shadow_slots": 2,
        "shadow_slot_rule": "min(2, N); no padding",
        "top2_frozen_before_outcome_truth": True,
        "entry_policy_id": ENTRY_POLICY_ID,
        "entry_price_rule": (
            "T proxy open must not exceed D-frozen shadow_max_price"
        ),
        "actual_order_fill_claimed": False,
    }
    _expect(
        payload.get("ranking_contract") == expected_ranking,
        "internal Shadow ranking contract invalid",
    )
    orders = [row.get("internal_shadow_order") for row in rows]
    _expect(
        orders == list(range(1, candidate_count + 1)),
        "internal Shadow rows are not ordered 1..N",
    )
    expected_row_keys = {
        "ts_code",
        "name",
        "industry",
        "stage_transition",
        "promotion_rank",
        "predicted_promotion_probability",
        "research_fill_proxy_score",
        "research_conditional_profit_score",
        "research_joint_proxy_score",
        "internal_shadow_order",
        "internal_shadow_selected",
        "shadow_slot",
        "shadow_max_price",
        "shadow_price_basis",
        "shadow_price_source_sha256",
        "lagged_prior_max_history_exit_date",
        "lagged_prior_snapshot_sha256",
    }
    promotion_ranks: list[int] = []
    for row in rows:
        _expect(
            isinstance(row, Mapping) and set(row) == expected_row_keys,
            "internal Shadow row surface drifted",
        )
        code = _normal_code(row.get("ts_code"))
        _expect(
            re.fullmatch(r"(?:600|601|603|605)\d{3}\.SH", code) is not None
            or re.fullmatch(r"(?:000|001|002|003)\d{3}\.SZ", code)
            is not None,
            "internal Shadow row escaped main-board scope",
        )
        _expect(
            row.get("stage_transition") in {"2→3", "3→4"},
            "internal Shadow row escaped hard stage scope",
        )
        promotion_rank = row.get("promotion_rank")
        _expect(
            type(promotion_rank) is int,
            "internal Shadow promotion rank is not an integer",
        )
        promotion_ranks.append(promotion_rank)
        promotion_probability = float(row["predicted_promotion_probability"])
        _expect(
            math.isfinite(promotion_probability)
            and 0.0 <= promotion_probability <= 1.0,
            "internal Shadow promotion probability invalid",
        )
        fill = float(row["research_fill_proxy_score"])
        conditional = float(row["research_conditional_profit_score"])
        joint = float(row["research_joint_proxy_score"])
        _expect(all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in (fill, conditional, joint)), "internal Shadow score invalid")
        _expect(math.isclose(joint, fill * conditional, rel_tol=0.0, abs_tol=1e-15), "internal Shadow lost exact two-stage proxy identity")
        _expect(joint <= fill + 1e-15 and joint <= conditional + 1e-15, "internal Shadow joint score exceeds a component")
        rank = int(row["internal_shadow_order"])
        shadow_slot_count = min(2, candidate_count)
        _expect(
            row.get("internal_shadow_selected")
            == int(rank <= shadow_slot_count),
            "internal Shadow Top2 flag invalid",
        )
        _expect(
            row.get("shadow_slot")
            == (rank if rank <= shadow_slot_count else None),
            "internal Shadow slot invalid",
        )
        shadow_max_price = float(row["shadow_max_price"])
        _expect(
            math.isfinite(shadow_max_price)
            and shadow_max_price > 0.0
            and math.isclose(
                shadow_max_price * 100.0,
                round(shadow_max_price * 100.0),
                rel_tol=0.0,
                abs_tol=1e-7,
            ),
            "internal Shadow price cap invalid",
        )
        _expect(
            row.get("shadow_price_basis")
            in {
                "D_FROZEN_RECOMMENDED_MAX_PRICE",
                "D_FROZEN_OBSERVATION_MAX_PRICE",
                "D_ONLY_MODEL_DIAGNOSTIC_CAP",
                "D_CLOSE_CONSERVATIVE_CAP",
            }
            and row.get("shadow_price_source_sha256")
            == source_d.get("file_sha256"),
            "internal Shadow price cap source binding invalid",
        )
        row_availability = str(
            row.get("lagged_prior_max_history_exit_date") or ""
        )
        _expect(
            re.fullmatch(r"20\d{6}", row_availability) is not None
            and row_availability < signal_date,
            "internal Shadow history availability escaped D",
        )
        _expect(
            SHA256_RE.fullmatch(
                str(row.get("lagged_prior_snapshot_sha256") or "")
            )
            is not None,
            "internal Shadow lagged-prior snapshot invalid",
        )
    _expect(
        sorted(promotion_ranks) == list(range(1, candidate_count + 1)),
        "internal Shadow promotion ranks are not 1..N",
    )
    if candidate_count > 0:
        _expect(
            feature_contract.get("lagged_prior_max_history_exit_date")
            == max(
                str(row["lagged_prior_max_history_exit_date"])
                for row in rows
            ),
            "internal Shadow lagged-prior availability summary drifted",
        )
    expected_order = sorted(
        rows,
        key=lambda row: (
            -float(row["research_joint_proxy_score"]),
            -float(row["research_conditional_profit_score"]),
            -float(row["research_fill_proxy_score"]),
            str(row["ts_code"]),
        ),
    )
    _expect(
        [row["ts_code"] for row in rows]
        == [row["ts_code"] for row in expected_order],
        "internal Shadow rows violate the fixed proxy ordering",
    )
    if candidate_count >= 3:
        _expect(
            float(rows[1]["research_joint_proxy_score"])
            != float(rows[2]["research_joint_proxy_score"]),
            "exact Top2/Top3 joint proxy tie is not selectable",
        )
    expected_top2 = [
        {
            "shadow_slot": index,
            "ts_code": rows[index - 1]["ts_code"],
            "promotion_rank": rows[index - 1]["promotion_rank"],
            "research_fill_proxy_score": rows[index - 1]["research_fill_proxy_score"],
            "research_conditional_profit_score": rows[index - 1]["research_conditional_profit_score"],
            "research_joint_proxy_score": rows[index - 1]["research_joint_proxy_score"],
            "shadow_max_price": rows[index - 1]["shadow_max_price"],
            "shadow_price_basis": rows[index - 1]["shadow_price_basis"],
            "shadow_price_source_sha256": rows[index - 1]["shadow_price_source_sha256"],
        }
        for index in range(1, min(2, candidate_count) + 1)
    ]
    shadow_top2 = payload.get("shadow_top2")
    _expect(
        isinstance(shadow_top2, Mapping)
        and set(shadow_top2) == {"status", "requested_slots", "actual_slots", "rows"}
        and shadow_top2.get("status")
        == (
            "NO_HARD_SCOPE_CANDIDATES"
            if candidate_count == 0
            else "FROZEN_INTERNAL_RESEARCH_ONLY"
        )
        and shadow_top2.get("requested_slots") == 2
        and shadow_top2.get("actual_slots") == min(2, candidate_count)
        and shadow_top2.get("rows") == expected_top2,
        "frozen internal Top2 projection invalid",
    )
    boundaries = payload.get("boundaries")
    expected_boundaries = {
        "front_end_rank_allowed": False,
        "official_trade_action_allowed": False,
        "production_model_publish_allowed": False,
        "workflow_connected": False,
        "may_change_promotion_membership": False,
        "may_override_promotion_rank": False,
        "may_create_trade_action": False,
        "actual_order_fill_observed": False,
        "actual_execution_claimed": False,
    }
    _expect(boundaries == expected_boundaries, "internal Shadow boundaries invalid")
    expected_source_hashes = {
        "formal_contract_sha256": EXPECTED_FORMAL_CONTRACT_SHA256,
        "artifact_index_sha256": EXPECTED_ARTIFACT_INDEX_SHA256,
        "audit_sha256": EXPECTED_AUDIT_SHA256,
        "model_pickle_sha256": EXPECTED_MODEL_SHA256,
        "lagged_priors_sha256": EXPECTED_LAGGED_PRIORS_SHA256,
        "strict_sse_calendar_sha256": EXPECTED_CALENDAR_SHA256,
        "full_history_ledger_sha256": EXPECTED_FULL_HISTORY_LEDGER_SHA256,
        "historical_feature_manifest_sha256": EXPECTED_HISTORICAL_MANIFEST_SHA256,
    }
    _expect(
        payload.get("source_hashes") == expected_source_hashes,
        "internal Shadow source hashes drifted",
    )
    _expect(payload.get("snapshot_sha256") == _canonical_sha256(_payload_without_materialization_fields(payload)), "internal Shadow snapshot hash invalid")
    downloads = payload.get("downloads")
    if require_downloads or downloads is not None:
        prefix = f"{OUTPUT_RELATIVE_ROOT.as_posix()}/shadow_{signal_date}"
        _expect(
            isinstance(downloads, Mapping)
            and downloads.get("json_url") == f"{prefix}.json"
            and downloads.get("csv_url") == f"{prefix}.csv"
            and downloads.get("row_count") == candidate_count
            and SHA256_RE.fullmatch(str(downloads.get("csv_sha256") or "")) is not None,
            "internal Shadow dated download binding invalid",
        )


CSV_FIELDS = (
    "schema_version",
    "status",
    "signal_date",
    "exec_date",
    "exit_date",
    "snapshot_sha256",
    "top10_members_sha256",
    "feature_snapshot_sha256",
    "model_artifact_sha256",
    "ts_code",
    "name",
    "industry",
    "stage_transition",
    "promotion_rank",
    "predicted_promotion_probability",
    "research_fill_proxy_score",
    "research_conditional_profit_score",
    "research_joint_proxy_score",
    "internal_shadow_order",
    "internal_shadow_selected",
    "shadow_slot",
    "lagged_prior_max_history_exit_date",
    "lagged_prior_snapshot_sha256",
    "research_only",
    "front_end_rank_allowed",
    "official_trade_action_allowed",
    "production_model_publish_allowed",
    "actual_execution_claimed",
)


def _csv_bytes(payload: Mapping[str, Any]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    common = {
        "schema_version": payload["schema_version"],
        "status": payload["status"],
        "signal_date": payload["signal_date"],
        "exec_date": payload["exec_date"],
        "exit_date": payload["exit_date"],
        "snapshot_sha256": payload["snapshot_sha256"],
        "top10_members_sha256": payload["top10_members_sha256"],
        "feature_snapshot_sha256": payload["feature_contract"]["feature_snapshot_sha256"],
        "model_artifact_sha256": payload["model"]["artifact_sha256"],
        "research_only": True,
        "front_end_rank_allowed": False,
        "official_trade_action_allowed": False,
        "production_model_publish_allowed": False,
        "actual_execution_claimed": False,
    }
    for row in payload["rows"]:
        writer.writerow({**common, **row})
    return b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


@contextmanager
def _exclusive_directory_lock(path: Path) -> Iterable[None]:
    path.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _stage_payload(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        staged = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return staged


def _install_dated_pair(
    json_path: Path,
    json_payload: bytes,
    csv_path: Path,
    csv_payload: bytes,
) -> tuple[Path, ...]:
    staged: list[Path] = []
    created: list[Path] = []
    try:
        staged_json = _stage_payload(json_path, json_payload)
        staged_csv = _stage_payload(csv_path, csv_payload)
        staged.extend((staged_json, staged_csv))
        for temporary, target, expected in (
            (staged_csv, csv_path, csv_payload),
            (staged_json, json_path, json_payload),
        ):
            try:
                os.link(temporary, target)
            except FileExistsError:
                _expect(
                    target.is_file()
                    and not target.is_symlink()
                    and target.read_bytes() == expected,
                    f"immutable dated artifact conflict: {target.name}",
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


def _atomic_pointer(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _index_payload(payload: Mapping[str, Any], json_sha256: str) -> dict[str, Any]:
    downloads = payload["downloads"]
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "index_kind": INDEX_KIND,
        "data_alias": False,
        "latest_signal_date": payload["signal_date"],
        "latest_exec_date": payload["exec_date"],
        "latest_exit_date": payload["exit_date"],
        "latest_status": INTERNAL_STATUS,
        "latest_json_url": downloads["json_url"],
        "latest_csv_url": downloads["csv_url"],
        "latest_json_sha256": json_sha256,
        "latest_csv_sha256": downloads["csv_sha256"],
        "latest_snapshot_sha256": payload["snapshot_sha256"],
        "latest_top10_members_sha256": payload["top10_members_sha256"],
        "front_end_rank_allowed": False,
        "official_trade_action_allowed": False,
        "production_model_publish_allowed": False,
    }


def validate_internal_forward_shadow_index(index: Mapping[str, Any]) -> None:
    _expect(index.get("schema_version") == INDEX_SCHEMA_VERSION and index.get("index_kind") == INDEX_KIND, "internal Shadow pointer schema invalid")
    _expect(index.get("data_alias") is False and index.get("latest_status") == INTERNAL_STATUS, "internal Shadow pointer is not a NOT_READY pointer")
    signal_date = _normal_date(index.get("latest_signal_date"))
    prefix = f"{OUTPUT_RELATIVE_ROOT.as_posix()}/shadow_{signal_date}"
    _expect(signal_date >= MINIMUM_SIGNAL_DATE, "internal Shadow pointer date invalid")
    _expect(index.get("latest_json_url") == f"{prefix}.json" and index.get("latest_csv_url") == f"{prefix}.csv", "internal Shadow pointer is not dated")
    for key in ("latest_json_sha256", "latest_csv_sha256", "latest_snapshot_sha256", "latest_top10_members_sha256"):
        _expect(SHA256_RE.fullmatch(str(index.get(key) or "")) is not None, f"internal Shadow pointer {key} invalid")
    for key in ("front_end_rank_allowed", "official_trade_action_allowed", "production_model_publish_allowed"):
        _expect(index.get(key) is False, f"internal Shadow pointer enabled {key}")


def _validate_existing_pointer_chain(
    output_root: Path,
    index: Mapping[str, Any],
) -> None:
    signal_date = str(index["latest_signal_date"])
    json_relative = OUTPUT_RELATIVE_ROOT / f"shadow_{signal_date}.json"
    csv_relative = OUTPUT_RELATIVE_ROOT / f"shadow_{signal_date}.csv"
    _expect(
        index.get("latest_json_url") == json_relative.as_posix()
        and index.get("latest_csv_url") == csv_relative.as_posix(),
        "existing internal Shadow pointer URLs drifted",
    )
    json_path = _safe_file(
        output_root,
        json_relative,
        label="existing pointed internal Shadow JSON",
    )
    csv_path = _safe_file(
        output_root,
        csv_relative,
        label="existing pointed internal Shadow CSV",
    )
    _expect(
        _sha256(json_path) == index.get("latest_json_sha256")
        and _sha256(csv_path) == index.get("latest_csv_sha256"),
        "existing internal Shadow pointer target hash drifted",
    )
    payload = _read_json(
        json_path,
        label="existing pointed internal Shadow JSON",
    )
    validate_internal_forward_shadow_payload(payload, require_downloads=True)
    _expect(
        payload.get("signal_date") == index.get("latest_signal_date")
        and payload.get("exec_date") == index.get("latest_exec_date")
        and payload.get("exit_date") == index.get("latest_exit_date")
        and payload.get("status") == index.get("latest_status")
        and payload.get("snapshot_sha256")
        == index.get("latest_snapshot_sha256")
        and payload.get("top10_members_sha256")
        == index.get("latest_top10_members_sha256")
        and payload.get("downloads", {}).get("csv_sha256")
        == index.get("latest_csv_sha256"),
        "existing internal Shadow pointer metadata drifted",
    )


def _materialize_internal_forward_shadow_locked(
    output_root: Path,
    payload: Mapping[str, Any],
    *,
    _now: datetime | None = None,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    validate_internal_forward_shadow_payload(payload)
    enriched = copy.deepcopy(dict(payload))
    output_root = output_root.resolve()
    output = output_root / OUTPUT_RELATIVE_ROOT
    signal_date = str(enriched["signal_date"])
    json_path = output / f"shadow_{signal_date}.json"
    csv_path = output / f"shadow_{signal_date}.csv"
    csv_payload = _csv_bytes(enriched)
    enriched["downloads"] = {
        "json_url": f"{OUTPUT_RELATIVE_ROOT.as_posix()}/{json_path.name}",
        "csv_url": f"{OUTPUT_RELATIVE_ROOT.as_posix()}/{csv_path.name}",
        "csv_sha256": _sha256_bytes(csv_payload),
        "row_count": len(enriched["rows"]),
    }
    validate_internal_forward_shadow_payload(enriched, require_downloads=True)
    json_payload = _json_bytes(enriched)

    candidate_index = _index_payload(enriched, _sha256_bytes(json_payload))
    validate_internal_forward_shadow_index(candidate_index)
    index_path = output / "index.json"
    existing_index: dict[str, Any] | None = None
    if index_path.exists():
        _expect(index_path.is_file() and not index_path.is_symlink(), "existing internal Shadow pointer is unsafe")
        existing_index = _read_json(index_path, label="existing internal Shadow pointer")
        validate_internal_forward_shadow_index(existing_index)
        _validate_existing_pointer_chain(output_root, existing_index)
        existing_date = str(existing_index["latest_signal_date"])
        if existing_date > signal_date:
            # Reject before creating either dated file.
            raise ExecutableProfitShadowError(
                "out-of-order internal Shadow backfill is forbidden"
            )
        if existing_date == signal_date:
            _expect(existing_index == candidate_index, "same-D internal Shadow pointer cannot be retargeted")

    json_exists = json_path.exists()
    csv_exists = csv_path.exists()
    identical_existing = False
    if json_exists:
        _expect(json_path.is_file() and not json_path.is_symlink(), "existing internal Shadow JSON is unsafe")
        existing = _read_json(json_path, label="existing internal Shadow JSON")
        validate_internal_forward_shadow_payload(existing, require_downloads=True)
        _expect(existing == enriched, "frozen D internal Shadow artifact cannot be overwritten")
    if csv_exists:
        _expect(csv_path.is_file() and not csv_path.is_symlink(), "existing internal Shadow CSV is unsafe")
        _expect(
            _sha256(csv_path) == enriched["downloads"]["csv_sha256"],
            "existing internal Shadow CSV hash drifted",
        )
    identical_existing = json_exists and csv_exists

    pointer_is_current = existing_index == candidate_index
    mutation_needed = not identical_existing or not pointer_is_current
    if mutation_needed:
        current = _now or datetime.now(ZoneInfo("Asia/Shanghai"))
        _expect(current.tzinfo is not None, "materialization clock must be timezone-aware")
        current = current.astimezone(ZoneInfo("Asia/Shanghai"))
        start, end = _selection_window(signal_date, str(enriched["exec_date"]))
        source_generated = _parse_aware_datetime(
            enriched["source_d_feature"]["generated_at_utc"],
            label="internal Shadow source generated_at_utc",
        ).astimezone(ZoneInfo("Asia/Shanghai"))
        _expect(
            start < source_generated <= current < end,
            "new internal Shadow materialization is outside D-close/T-09:20 window",
        )

    created: tuple[Path, ...] = ()
    if not identical_existing:
        created = _install_dated_pair(
            json_path,
            json_payload,
            csv_path,
            csv_payload,
        )

    final_index = candidate_index
    if existing_index is not None and str(existing_index["latest_signal_date"]) == signal_date:
        final_index = existing_index
    try:
        if final_index == candidate_index and not pointer_is_current:
            _atomic_pointer(index_path, _json_bytes(candidate_index))
    except Exception:
        for path in reversed(created):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise
    return json_path, csv_path, index_path, dict(final_index)


def _materialize_internal_forward_shadow(
    output_root: Path,
    payload: Mapping[str, Any],
) -> tuple[Path, Path, Path, dict[str, Any]]:
    output_root = output_root.resolve(strict=True)
    output = _safe_directory(
        output_root,
        OUTPUT_RELATIVE_ROOT,
        label="internal Shadow output directory",
        create=True,
    )
    with _exclusive_directory_lock(output):
        return _materialize_internal_forward_shadow_locked(
            output_root,
            payload,
            _now=None,
        )


def score_and_materialize_internal_forward_shadow(
    *,
    repo_root: Path,
    d_feature_path: Path,
) -> tuple[
    dict[str, Any],
    Path,
    Path,
    Path,
    dict[str, Any],
]:
    payload = score_internal_forward_shadow(
        repo_root=repo_root,
        d_feature_path=d_feature_path,
    )
    json_path, csv_path, index_path, pointer = (
        _materialize_internal_forward_shadow(repo_root, payload)
    )
    return payload, json_path, csv_path, index_path, pointer


def _materialize_internal_forward_shadow_for_test(
    output_root: Path,
    payload: Mapping[str, Any],
    *,
    now: datetime,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    output_root = output_root.resolve(strict=True)
    output = _safe_directory(
        output_root,
        OUTPUT_RELATIVE_ROOT,
        label="internal Shadow test output directory",
        create=True,
    )
    with _exclusive_directory_lock(output):
        return _materialize_internal_forward_shadow_locked(
            output_root,
            payload,
            _now=now,
        )


__all__ = [
    "ARTIFACT_KIND",
    "DEFAULT_CALENDAR_PATH",
    "DEFAULT_CONTRACT_PATH",
    "DEFAULT_HISTORY_LEDGER_PATH",
    "DEFAULT_WORK_ROOT",
    "ExecutableProfitShadowError",
    "INDEX_KIND",
    "INTERNAL_STATUS",
    "LoadedInternalChallenger",
    "MINIMUM_SIGNAL_DATE",
    "OUTPUT_RELATIVE_ROOT",
    "SCHEMA_VERSION",
    "build_strict_lagged_priors",
    "load_internal_challenger",
    "score_and_materialize_internal_forward_shadow",
    "score_internal_forward_shadow",
    "validate_internal_forward_shadow_index",
    "validate_internal_forward_shadow_payload",
]
