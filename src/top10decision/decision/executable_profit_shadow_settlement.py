from __future__ import annotations

import copy
import csv
import fcntl
import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from statistics import median
from typing import Any

from top10decision.decision.executable_profit_shadow import (
    MINIMUM_SIGNAL_DATE,
    OUTPUT_RELATIVE_ROOT as SELECTION_ROOT,
    SCHEMA_VERSION as LEGACY_SELECTION_SCHEMA,
    validate_internal_forward_shadow_payload,
)


T_VERIFICATION_SCHEMA = "dc20_executable_profit_shadow_t_verification_v1"
SETTLEMENT_SCHEMA = "dc20_executable_profit_shadow_t1_settlement_v1"
STATISTICS_SCHEMA = "dc20_executable_profit_shadow_statistics_v1"
CONTRACT_ID = "dc20_executable_profit_forward_settlement_20260824_v1"
CONTRACT_PATH = Path(
    "models/decision_executable_profit_forward_settlement_contract.json"
)
CALENDAR_PATH = Path("data/market/trade_cal_sse.csv")
CALENDAR_SHA256 = (
    "150a3e29ebd6e050d55caee1df218ef5dcfc3542053d8a7478d6be50d09fd748"
)
VERIFICATION_ROOT = Path("data/decision_executable_profit/forward/verifications")
SETTLEMENT_ROOT = Path("data/decision_executable_profit/forward/settlements")
STATISTICS_PATH = Path(
    "data/decision_executable_profit/forward/statistics/summary.json"
)
COST_VERSION = "dc20_shadow_cost_v1_45bp"
COST_RATE = 0.0045
ENTRY_POLICY_ID = "dc20_public_market_buyable_proxy_v1"
PRIMARY_MIXED_SELECTION_SCHEMA = "dc20_primary_profit_forward_shadow_selection_v2"
PRIMARY_MIXED_SELECTION_CONTRACT_ID = (
    "dc20_primary_profit_forward_shadow_bridge_20260829_v1"
)
SHADOW_NOTIONAL_CNY = 100_000.0
MAX_AUCTION_PARTICIPATION = 0.01
TARGET_FORWARD_DATES = 180
DATE_RE = re.compile(r"20\d{6}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
PRICE_TICK = Decimal("0.01")


class ExecutableProfitSettlementError(RuntimeError):
    """Raised when immutable Shadow truth or statistics fail closed."""


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise ExecutableProfitSettlementError(message)


def _normal_date(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _normal_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if re.fullmatch(r"\d{6}\.(?:SH|SZ)", text):
        return text
    digits = "".join(character for character in text if character.isdigit())[:6]
    if len(digits) != 6:
        return ""
    suffix = "SH" if digits.startswith(("600", "601", "603", "605")) else "SZ"
    return f"{digits}.{suffix}"


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _payload_snapshot(payload: Mapping[str, Any]) -> str:
    copied = copy.deepcopy(dict(payload))
    copied.pop("snapshot_sha256", None)
    return _canonical_sha256(copied)


def _safe_existing_file(root: Path, relative: Path, *, label: str) -> Path:
    root = root.resolve(strict=True)
    _expect(not relative.is_absolute() and ".." not in relative.parts, f"unsafe {label} path")
    candidate = root / relative
    current = root
    for part in relative.parts:
        current = current / part
        _expect(not current.is_symlink(), f"{label} has a symlink ancestor")
    _expect(candidate.is_file(), f"{label} is missing")
    _expect(candidate.resolve(strict=True).is_relative_to(root), f"{label} escaped repository")
    return candidate


def _ensure_directory(root: Path, relative: Path) -> Path:
    root = root.resolve(strict=True)
    _expect(not relative.is_absolute() and ".." not in relative.parts, "unsafe output path")
    current = root
    for part in relative.parts:
        current = current / part
        if current.exists():
            _expect(current.is_dir() and not current.is_symlink(), "output has a symlink ancestor")
        else:
            current.mkdir()
    return current


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExecutableProfitSettlementError(f"invalid {label}") from exc
    _expect(isinstance(value, dict), f"{label} is not an object")
    return value


@contextmanager
def _locked(directory: Path):
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _install_immutable(path: Path, payload: Mapping[str, Any]) -> Path:
    content = _canonical_bytes(payload)
    if path.exists():
        _expect(path.is_file() and not path.is_symlink(), "immutable artifact path is unsafe")
        _expect(path.read_bytes() == content, f"immutable artifact cannot be rewritten: {path.name}")
        return path
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        try:
            os.link(temporary, path)
        except FileExistsError:
            _expect(path.read_bytes() == content, f"immutable artifact race changed: {path.name}")
        return path
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _replace_projection(path: Path, payload: Mapping[str, Any]) -> Path:
    content = _canonical_bytes(payload)
    if path.exists():
        _expect(path.is_file() and not path.is_symlink(), "statistics projection is unsafe")
        if path.read_bytes() == content:
            return path
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
        return path
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _load_contract(repo_root: Path) -> dict[str, Any]:
    path = _safe_existing_file(repo_root, CONTRACT_PATH, label="settlement contract")
    contract = _read_json(path, label="settlement contract")
    _expect(
        contract.get("schema_version")
        == "dc20_executable_profit_forward_settlement_contract_v1"
        and contract.get("contract_id") == CONTRACT_ID
        and contract.get("status") == "INTERNAL_RESEARCH_SHADOW_ONLY",
        "settlement contract identity drifted",
    )
    _expect(
        contract.get("calendar", {}).get("sha256") == CALENDAR_SHA256,
        "settlement calendar contract drifted",
    )
    _expect(
        contract.get("as_of_contract")
        == {
            "required": True,
            "must_be_pinned_sse_open_session": True,
            "minimum_date": "signal D",
            "T_truth_allowed_only_when": "exec_date <= as_of_date",
            "exit_scan_upper_bound": "as_of_date inclusive",
            "future_market_source_allowed": False,
            "statistics_future_truth_allowed": False,
        },
        "settlement as-of contract drifted",
    )
    _expect(
        contract.get("costs", {}).get("round_trip_cost_rate") == COST_RATE
        and contract.get("costs", {}).get("stress_round_trip_cost_rate")
        == 0.009
        and contract.get("costs", {}).get("version") == COST_VERSION,
        "settlement cost contract drifted",
    )
    truth = contract.get("truth", {})
    _expect(
        truth.get("entry_policy_id") == ENTRY_POLICY_ID
        and truth.get("shadow_notional_cny") == SHADOW_NOTIONAL_CNY
        and truth.get("maximum_auction_participation")
        == MAX_AUCTION_PARTICIPATION,
        "settlement entry truth contract drifted",
    )
    selection_input = contract.get("selection_input", {})
    source_variants = selection_input.get("entry_source_variants")
    required_entry_fields = selection_input.get("required_frozen_entry_fields")
    _expect(
        selection_input.get("selected_slots_rule")
        == "actual_slots = min(2, N); never add a candidate outside the frozen promotion TopN"
        and selection_input.get("accepted_schema_versions")
        == [LEGACY_SELECTION_SCHEMA, PRIMARY_MIXED_SELECTION_SCHEMA]
        and required_entry_fields
        == {
            "ranking_contract": ["entry_policy_id"],
            "selected_row": [
                "shadow_max_price",
                "shadow_price_basis",
                "shadow_price_source_sha256",
            ],
            "source_d_feature": ["file_name", "file_sha256"],
            "source_bindings.runtime_features": ["path", "sha256"],
        }
        and source_variants
        == {
            LEGACY_SELECTION_SCHEMA: {
                "binding": "source_d_feature",
                "path_pattern": "pred_<D>.csv",
                "legacy_behavior_unchanged": True,
            },
            PRIMARY_MIXED_SELECTION_SCHEMA: {
                "binding": "source_bindings.runtime_features",
                "path_pattern": (
                    "outputs/decision/primary_d_runtime_features_<D>.csv"
                ),
                "repository_file_sha256_must_match": True,
                "selected_row_cap_sha256_must_match": True,
                "contract_id": PRIMARY_MIXED_SELECTION_CONTRACT_ID,
            },
        }
        and selection_input.get("historical_backfill_allowed") is False
        and selection_input.get("minimum_signal_date") == MINIMUM_SIGNAL_DATE,
        "settlement selection contract drifted",
    )
    append_only = contract.get("append_only_artifacts", {})
    _expect(
        append_only.get("T_verification")
        == "data/decision_executable_profit/forward/verifications/t_verification_<D>.json"
        and append_only.get("T_plus_1_settlement")
        == "data/decision_executable_profit/forward/settlements/settlement_<D>.json"
        and append_only.get("same_date_rewrite")
        == "REJECT_UNLESS_BYTE_IDENTICAL",
        "settlement append-only artifact contract drifted",
    )
    statistics_contract = contract.get("statistics", {})
    _expect(
        statistics_contract.get("cohorts")
        == [
            "all_selected_slots",
            "shadow_slot_1",
            "shadow_slot_2",
            "stage_2_to_3",
            "stage_3_to_4",
        ]
        and statistics_contract.get("excluded_ledgers")
        == [
            "historical_oof_top10_ledger",
            "p_fill_shadow_top2_ledger",
            "legacy_observation_statistics",
            "manual_actual_trade_ledger",
            "official_trade_action_ledger",
        ]
        and statistics_contract.get("as_of_date_required") is True
        and statistics_contract.get("summary_pointer_rule")
        == "as_of_date may only move forward; same-date deterministic refresh is allowed",
        "settlement statistics contract drifted",
    )
    boundaries = contract.get("boundaries", {})
    _expect(
        boundaries.get("research_only") is True
        and boundaries.get("official_trade_action_allowed") is False
        and boundaries.get("actual_human_trade_ledger_is_separate") is True,
        "settlement safety boundaries drifted",
    )
    return contract


def _strict_open_dates(repo_root: Path) -> list[str]:
    path = _safe_existing_file(repo_root, CALENDAR_PATH, label="strict SSE calendar")
    _expect(_sha256(path) == CALENDAR_SHA256, "strict SSE calendar SHA drifted")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ExecutableProfitSettlementError("strict SSE calendar is unreadable") from exc
    dates = sorted(
        {
            _normal_date(row.get("cal_date"))
            for row in rows
            if str(row.get("exchange") or "").strip().upper() == "SSE"
            and str(row.get("is_open") or "").strip() == "1"
        }
    )
    _expect(dates and all(DATE_RE.fullmatch(value) for value in dates), "strict SSE calendar is empty")
    return dates


def _strict_as_of_date(
    open_dates: Sequence[str],
    as_of_date: str,
    *,
    signal_date: str,
) -> str:
    normalized = _normal_date(as_of_date)
    _expect(
        normalized == as_of_date
        and normalized in open_dates
        and normalized >= signal_date,
        "as-of date must be a pinned SSE open session on or after signal D",
    )
    return normalized


def _validate_adjacent_dates(
    open_dates: Sequence[str],
    signal_date: str,
    exec_date: str,
    exit_date: str,
) -> None:
    try:
        index = open_dates.index(signal_date)
    except ValueError as exc:
        raise ExecutableProfitSettlementError("selection D is not a strict SSE open session") from exc
    _expect(
        index + 2 < len(open_dates)
        and open_dates[index + 1] == exec_date
        and open_dates[index + 2] == exit_date,
        "selection D/T/T+1 are not adjacent strict SSE sessions",
    )


def _selection_path(signal_date: str) -> Path:
    return SELECTION_ROOT / f"shadow_{signal_date}.json"


def _validate_primary_mixed_selection(
    repo_root: Path,
    payload: Mapping[str, Any],
    *,
    require_downloads: bool,
) -> None:
    """Delegate the v2 selection contract without coupling the v1 scorer to it."""

    from top10decision.decision.primary_profit_forward_shadow_bridge import (
        CONTRACT_ID as BRIDGE_CONTRACT_ID,
        ENTRY_POLICY_ID as BRIDGE_ENTRY_POLICY_ID,
        SCHEMA_VERSION as BRIDGE_SCHEMA_VERSION,
        PrimaryProfitForwardShadowError,
        validate_primary_profit_forward_shadow,
    )

    _expect(
        BRIDGE_SCHEMA_VERSION == PRIMARY_MIXED_SELECTION_SCHEMA
        and BRIDGE_CONTRACT_ID == PRIMARY_MIXED_SELECTION_CONTRACT_ID
        and BRIDGE_ENTRY_POLICY_ID == ENTRY_POLICY_ID,
        "primary mixed Shadow bridge identity drifted",
    )
    try:
        validate_primary_profit_forward_shadow(
            payload,
            repo_root=repo_root,
            require_downloads=require_downloads,
        )
    except PrimaryProfitForwardShadowError as exc:
        raise ExecutableProfitSettlementError(
            f"invalid primary mixed Shadow selection: {exc}"
        ) from exc


def _validate_selection_payload(
    repo_root: Path,
    payload: Mapping[str, Any],
    *,
    require_downloads: bool,
) -> str:
    schema = str(payload.get("schema_version") or "")
    if schema == LEGACY_SELECTION_SCHEMA:
        validate_internal_forward_shadow_payload(
            payload,
            require_downloads=require_downloads,
        )
        return schema
    if schema == PRIMARY_MIXED_SELECTION_SCHEMA:
        _validate_primary_mixed_selection(
            repo_root,
            payload,
            require_downloads=require_downloads,
        )
        return schema
    raise ExecutableProfitSettlementError(
        f"unsupported frozen Shadow selection schema: {schema or 'missing'}"
    )


def _selected_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("rows")
    _expect(isinstance(rows, list), "selection rows missing")
    selected = [
        dict(row)
        for row in rows
        if isinstance(row, Mapping) and row.get("internal_shadow_selected") == 1
    ]
    selected.sort(key=lambda row: int(row.get("shadow_slot") or 999))
    slots = [int(row.get("shadow_slot") or 0) for row in selected]
    _expect(slots == list(range(1, len(selected) + 1)), "selection slots are not contiguous")
    _expect(len(selected) <= 2, "selection has more than two Shadow slots")
    projection = payload.get("shadow_top2", {}).get("rows")
    _expect(isinstance(projection, list), "selection Top2 projection missing")
    _expect(
        [(row.get("shadow_slot"), row.get("ts_code")) for row in projection]
        == [(row.get("shadow_slot"), row.get("ts_code")) for row in selected],
        "selection Top2 projection drifted from frozen rows",
    )
    return selected


def load_selection(repo_root: Path, signal_date: str) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    repo_root = repo_root.resolve(strict=True)
    signal_date = _normal_date(signal_date)
    _expect(signal_date >= MINIMUM_SIGNAL_DATE, "historical Shadow backfill is forbidden")
    path = _safe_existing_file(repo_root, _selection_path(signal_date), label="frozen Shadow selection")
    payload = _read_json(path, label="frozen Shadow selection")
    _validate_selection_payload(repo_root, payload, require_downloads=True)
    _expect(payload.get("signal_date") == signal_date, "selection filename/date binding drifted")
    selected = _selected_rows(payload)
    return path, payload, selected


def _entry_source_binding(
    repo_root: Path,
    selection: Mapping[str, Any],
    signal_date: str,
) -> tuple[str, str, bool]:
    """Return the frozen cap source and recheck v2 repository bytes.

    The v1 selection predates the primary P0 runtime bundle and retains its
    original filename-only contract.  The v2 bridge is stronger: its exact-D
    runtime CSV remains in the repository, so settlement re-hashes those bytes
    before any T truth can be materialized.
    """

    schema = str(selection.get("schema_version") or "")
    if schema == LEGACY_SELECTION_SCHEMA:
        source = selection.get("source_d_feature")
        file_name = (
            str(source.get("file_name") or "").strip()
            if isinstance(source, Mapping)
            else ""
        )
        file_sha = (
            str(source.get("file_sha256") or "").strip()
            if isinstance(source, Mapping)
            else ""
        )
        return file_name, file_sha, False

    _expect(
        schema == PRIMARY_MIXED_SELECTION_SCHEMA,
        "unsupported selection schema for frozen entry source",
    )
    source_bindings = selection.get("source_bindings")
    runtime = (
        source_bindings.get("runtime_features")
        if isinstance(source_bindings, Mapping)
        else None
    )
    _expect(
        isinstance(runtime, Mapping),
        "primary mixed Shadow runtime feature binding missing",
    )
    relative_text = str(runtime.get("path") or "").strip()
    expected_relative = (
        f"outputs/decision/primary_d_runtime_features_{signal_date}.csv"
    )
    source_sha = str(runtime.get("sha256") or "").strip()
    _expect(
        relative_text == expected_relative
        and SHA256_RE.fullmatch(source_sha) is not None,
        "primary mixed Shadow runtime feature path/SHA binding invalid",
    )
    runtime_path = _safe_existing_file(
        repo_root,
        Path(relative_text),
        label="primary mixed Shadow runtime features",
    )
    _expect(
        _sha256(runtime_path) == source_sha,
        "primary mixed Shadow runtime feature bytes changed after D freeze",
    )
    return relative_text, source_sha, True


def _market_relative_candidates(trade_date: str, name: str) -> tuple[Path, ...]:
    return (
        Path("data/market/raw") / trade_date[:4] / trade_date / f"{name}.csv",
        Path("data/market/raw") / trade_date / f"{name}.csv",
        Path("data/market/raw") / f"{name}_{trade_date}.csv",
    )


def _find_market_file(repo_root: Path, trade_date: str, name: str) -> Path | None:
    for relative in _market_relative_candidates(trade_date, name):
        candidate = repo_root.resolve(strict=True) / relative
        if candidate.exists():
            return _safe_existing_file(repo_root, relative, label=f"{trade_date} {name}")
    return None


def _market_rows(path: Path, trade_date: str) -> dict[str, dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            source = list(csv.DictReader(handle))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ExecutableProfitSettlementError(f"invalid market file: {path.name}") from exc
    _expect(
        source and "ts_code" in source[0] and "trade_date" in source[0],
        f"market file has no rows, ts_code or trade_date: {path.name}",
    )
    output: dict[str, dict[str, str]] = {}
    for row in source:
        row_date = str(row.get("trade_date") or "").strip()
        _expect(
            row_date == trade_date,
            (
                f"market row trade_date must exactly equal {trade_date}: "
                f"{path.name}"
            ),
        )
        code = _normal_code(row.get("ts_code"))
        if not code:
            continue
        _expect(code not in output, f"duplicate market row for {code} in {path.name}")
        output[code] = dict(row)
    _expect(output, f"market file has no rows for {trade_date}: {path.name}")
    return output


def _source_binding(repo_root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.resolve(strict=True).relative_to(repo_root.resolve(strict=True)).as_posix(),
        "sha256": _sha256(path),
    }


def _ohlc(row: Mapping[str, Any]) -> tuple[float | None, float | None, float | None, float | None]:
    return tuple(_finite(row.get(column)) for column in ("open", "high", "low", "close"))  # type: ignore[return-value]


def _rounded_price_tick(value: Any) -> int | None:
    try:
        price = Decimal(str(value))
        if not price.is_finite() or price <= 0:
            return None
        rounded = price.quantize(PRICE_TICK, rounding=ROUND_HALF_UP)
        return int(rounded / PRICE_TICK)
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _same_rounded_price(left: Any, right: Any) -> bool:
    left_tick = _rounded_price_tick(left)
    right_tick = _rounded_price_tick(right)
    return left_tick is not None and left_tick == right_tick


def _all_at_limit(values: Sequence[float | None], limit_value: float | None) -> bool:
    return bool(
        _rounded_price_tick(limit_value) is not None
        and all(_same_rounded_price(value, limit_value) for value in values)
    )


def _selection_binding(path: Path, payload: Mapping[str, Any], selected: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "path": _selection_path(str(payload["signal_date"])).as_posix(),
        "file_sha256": _sha256(path),
        "snapshot_sha256": str(payload["snapshot_sha256"]),
        "top10_members_sha256": str(payload["top10_members_sha256"]),
        "selected_slots": len(selected),
        "selected_members": [
            {"shadow_slot": int(row["shadow_slot"]), "ts_code": str(row["ts_code"])}
            for row in selected
        ],
    }


def _validate_selection_binding(value: Any) -> Mapping[str, Any]:
    _expect(isinstance(value, Mapping), "artifact selection binding missing")
    _expect(
        set(value)
        == {
            "path",
            "file_sha256",
            "snapshot_sha256",
            "top10_members_sha256",
            "selected_slots",
            "selected_members",
        },
        "artifact selection binding surface drifted",
    )
    _expect(
        re.fullmatch(
            r"data/decision_executable_profit/forward/selections/shadow_20\d{6}\.json",
            str(value.get("path") or ""),
        )
        is not None,
        "artifact selection path invalid",
    )
    for key in ("file_sha256", "snapshot_sha256", "top10_members_sha256"):
        _expect(SHA256_RE.fullmatch(str(value.get(key) or "")) is not None, f"artifact selection {key} invalid")
    members = value.get("selected_members")
    _expect(isinstance(members, list) and 0 <= len(members) <= 2, "artifact selected members invalid")
    _expect(value.get("selected_slots") == len(members), "artifact selected slot count invalid")
    _expect(all(isinstance(row, Mapping) for row in members), "artifact selected member row invalid")
    _expect(
        members
        == [
            {"shadow_slot": index, "ts_code": str(row.get("ts_code") or "")}
            for index, row in enumerate(members, start=1)
        ],
        "artifact selected members are not exact contiguous slots",
    )
    return value


def _validate_source_files(value: Any) -> None:
    _expect(isinstance(value, list), "artifact source files missing")
    paths: list[str] = []
    for item in value:
        _expect(
            isinstance(item, Mapping)
            and set(item) == {"path", "sha256"}
            and str(item.get("path") or "").startswith("data/market/raw/")
            and SHA256_RE.fullmatch(str(item.get("sha256") or "")) is not None,
            "artifact market source binding invalid",
        )
        paths.append(str(item["path"]))
    _expect(len(paths) == len(set(paths)), "artifact market source is duplicated")


def build_t_verification(
    repo_root: Path,
    signal_date: str,
    *,
    as_of_date: str,
) -> tuple[dict[str, Any] | None, str]:
    repo_root = repo_root.resolve(strict=True)
    _load_contract(repo_root)
    selection_path, selection, selected = load_selection(repo_root, signal_date)
    signal_date = str(selection["signal_date"])
    exec_date = str(selection["exec_date"])
    exit_date = str(selection["exit_date"])
    open_dates = _strict_open_dates(repo_root)
    _validate_adjacent_dates(open_dates, signal_date, exec_date, exit_date)
    as_of_date = _strict_as_of_date(
        open_dates,
        as_of_date,
        signal_date=signal_date,
    )
    if selected and exec_date > as_of_date:
        return None, "PENDING_T_NOT_REACHED"

    source_files: list[dict[str, Any]] = []
    daily_rows: dict[str, dict[str, str]] = {}
    limit_rows: dict[str, dict[str, str]] = {}
    auction_rows: dict[str, dict[str, str]] = {}
    if selected:
        daily_path = _find_market_file(repo_root, exec_date, "daily")
        limit_path = _find_market_file(repo_root, exec_date, "stk_limit")
        auction_path = _find_market_file(repo_root, exec_date, "stk_auction_o")
        if daily_path is None or limit_path is None or auction_path is None:
            return None, "PENDING_T_SOURCE_FILES"
        daily_rows = _market_rows(daily_path, exec_date)
        limit_rows = _market_rows(limit_path, exec_date)
        auction_rows = _market_rows(auction_path, exec_date)
        source_files = [
            _source_binding(repo_root, daily_path),
            _source_binding(repo_root, limit_path),
            _source_binding(repo_root, auction_path),
        ]

    rows: list[dict[str, Any]] = []
    selection_ranking = selection.get("ranking_contract")
    source_file_name, source_file_sha, strict_repository_source = (
        _entry_source_binding(repo_root, selection, signal_date)
    )
    entry_policy_id = (
        str(selection_ranking.get("entry_policy_id") or "").strip()
        if isinstance(selection_ranking, Mapping)
        else ""
    )
    for frozen in selected:
        code = str(frozen["ts_code"])
        cap = _finite(frozen.get("shadow_max_price"))
        cap_basis = str(frozen.get("shadow_price_basis") or "").strip()
        cap_source_sha = str(frozen.get("shadow_price_source_sha256") or "").strip()
        if strict_repository_source:
            _expect(
                cap_source_sha == source_file_sha,
                f"primary mixed Shadow cap source SHA drifted: {code}",
            )
        if (
            entry_policy_id != ENTRY_POLICY_ID
            or cap is None
            or cap <= 0
            or not cap_basis
            or (
                not strict_repository_source
                and source_file_name != f"pred_{signal_date}.csv"
            )
            or SHA256_RE.fullmatch(cap_source_sha) is None
            or cap_source_sha != source_file_sha
        ):
            return None, f"PENDING_T_FROZEN_ENTRY_CAP:{code}"
        daily = daily_rows.get(code)
        if daily is None:
            return None, f"PENDING_T_DAILY_ROW:{code}"
        prices = _ohlc(daily)
        if any(value is None or value <= 0 for value in prices):
            return None, f"PENDING_T_INVALID_DAILY_ROW:{code}"
        limit = limit_rows.get(code)
        up_limit = _finite(limit.get("up_limit")) if limit is not None else None
        if up_limit is None or up_limit <= 0:
            return None, f"PENDING_T_LIMIT_ROW:{code}"
        auction = auction_rows.get(code)
        if auction is None:
            return None, f"PENDING_T_AUCTION_ROW:{code}"
        auction_price = next(
            (
                value
                for value in (
                    _finite(auction.get("close")),
                    _finite(auction.get("price")),
                    _finite(auction.get("auction_price")),
                    _finite(auction.get("vwap")),
                    _finite(auction.get("open")),
                )
                if value is not None and value > 0
            ),
            None,
        )
        auction_amount = next(
            (
                value
                for value in (
                    _finite(auction.get("amount")),
                    _finite(auction.get("auction_amount")),
                )
                if value is not None and value > 0
            ),
            None,
        )
        if auction_price is None:
            return None, f"PENDING_T_AUCTION_PRICE:{code}"
        if auction_amount is None:
            return None, f"PENDING_T_AUCTION_AMOUNT:{code}"
        one_price = _all_at_limit(prices, up_limit)
        open_conflict = not _same_rounded_price(prices[0], auction_price)
        auction_tick = _rounded_price_tick(auction_price)
        cap_tick = _rounded_price_tick(cap)
        _expect(
            auction_tick is not None and cap_tick is not None,
            f"invalid rounded entry price contract: {code}",
        )
        cap_accept = bool(
            auction_tick is not None
            and cap_tick is not None
            and auction_tick <= cap_tick
        )
        opening_limit_up = _same_rounded_price(auction_price, up_limit)
        capacity_cny = auction_amount * MAX_AUCTION_PARTICIPATION
        capacity_accept = capacity_cny + 1e-9 >= SHADOW_NOTIONAL_CNY
        proxy_fill = int(
            not open_conflict
            and cap_accept
            and not opening_limit_up
            and not one_price
            and capacity_accept
        )
        if open_conflict:
            validation_status = "T_VERIFIED_PROXY_NO_FILL_AUCTION_DAILY_CONFLICT"
        elif not cap_accept:
            validation_status = "T_VERIFIED_PROXY_NO_FILL_ABOVE_FROZEN_CAP"
        elif opening_limit_up:
            validation_status = "T_VERIFIED_PROXY_NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED"
        elif one_price:
            validation_status = "T_VERIFIED_PROXY_NO_FILL_ONE_PRICE_LIMIT_UP"
        elif not capacity_accept:
            validation_status = "T_VERIFIED_PROXY_NO_FILL_CAPACITY"
        else:
            validation_status = "T_VERIFIED_PROXY_FILLED"
        rows.append(
            {
                "shadow_slot": int(frozen["shadow_slot"]),
                "ts_code": code,
                "stage_transition": str(frozen["stage_transition"]),
                "research_joint_proxy_score": float(frozen["research_joint_proxy_score"]),
                "entry_policy_id": ENTRY_POLICY_ID,
                "shadow_max_price": float(cap),
                "shadow_price_basis": cap_basis,
                "shadow_price_source_file": source_file_name,
                "shadow_price_source_sha256": cap_source_sha,
                "truth_state": "OBSERVED_PROXY_FILL" if proxy_fill else "OBSERVED_PROXY_NO_FILL",
                "validation_status": validation_status,
                "proxy_fill": proxy_fill,
                "entry_open_price": float(auction_price),
                "daily_open_price": float(prices[0]),
                "t_close_price": float(prices[3]),
                "one_price_limit_up": one_price,
                "auction_daily_open_conflict": open_conflict,
                "open_at_or_below_frozen_cap": cap_accept,
                "auction_amount": float(auction_amount),
                "shadow_capacity_cny": float(capacity_cny),
                "shadow_capacity_accepted": capacity_accept,
                "actual_order_fill_observed": False,
            }
        )

    payload: dict[str, Any] = {
        "schema_version": T_VERIFICATION_SCHEMA,
        "artifact_kind": "immutable_t_public_market_proxy_verification",
        "contract_id": CONTRACT_ID,
        "status": "T_VERIFIED_RESEARCH_PROXY_ONLY",
        "signal_date": signal_date,
        "exec_date": exec_date,
        "exit_date": exit_date,
        "selection": _selection_binding(selection_path, selection, selected),
        "calendar": {"path": CALENDAR_PATH.as_posix(), "sha256": CALENDAR_SHA256},
        "source_files": source_files,
        "rows": rows,
        "boundaries": {
            "research_only": True,
            "official_trade_action_allowed": False,
            "actual_order_fill_observed": False,
            "actual_human_trade_included": False,
            "selection_changed": False,
        },
    }
    payload["snapshot_sha256"] = _payload_snapshot(payload)
    validate_t_verification(payload)
    return payload, "T_VERIFIED"


def validate_t_verification(payload: Mapping[str, Any]) -> None:
    expected = {
        "schema_version",
        "artifact_kind",
        "contract_id",
        "status",
        "signal_date",
        "exec_date",
        "exit_date",
        "selection",
        "calendar",
        "source_files",
        "rows",
        "boundaries",
        "snapshot_sha256",
    }
    _expect(set(payload) == expected, "T verification surface drifted")
    _expect(
        payload.get("schema_version") == T_VERIFICATION_SCHEMA
        and payload.get("contract_id") == CONTRACT_ID
        and payload.get("status") == "T_VERIFIED_RESEARCH_PROXY_ONLY",
        "T verification identity drifted",
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
        "T verification dates invalid",
    )
    selection = _validate_selection_binding(payload.get("selection"))
    _expect(str(selection["path"]).endswith(f"shadow_{signal_date}.json"), "T verification selection date drifted")
    rows = payload.get("rows")
    _expect(isinstance(rows, list), "T verification members missing")
    members = selection.get("selected_members")
    _expect(isinstance(members, list) and selection.get("selected_slots") == len(members), "T verification selection count drifted")
    _expect(
        [(row.get("shadow_slot"), row.get("ts_code")) for row in rows if isinstance(row, Mapping)]
        == [(row.get("shadow_slot"), row.get("ts_code")) for row in members if isinstance(row, Mapping)],
        "T verification changed frozen members",
    )
    for row in rows:
        _expect(isinstance(row, Mapping), "T verification row invalid")
        _expect(
            set(row)
            == {
                "shadow_slot",
                "ts_code",
                "stage_transition",
                "research_joint_proxy_score",
                "entry_policy_id",
                "shadow_max_price",
                "shadow_price_basis",
                "shadow_price_source_file",
                "shadow_price_source_sha256",
                "truth_state",
                "validation_status",
                "proxy_fill",
                "entry_open_price",
                "daily_open_price",
                "t_close_price",
                "one_price_limit_up",
                "auction_daily_open_conflict",
                "open_at_or_below_frozen_cap",
                "auction_amount",
                "shadow_capacity_cny",
                "shadow_capacity_accepted",
                "actual_order_fill_observed",
            },
            "T verification row surface drifted",
        )
        _expect(row.get("proxy_fill") in {0, 1}, "T verification proxy fill invalid")
        _expect(
            row.get("entry_policy_id") == ENTRY_POLICY_ID
            and _finite(row.get("shadow_max_price")) is not None
            and str(row.get("shadow_price_basis") or "").strip()
            and str(row.get("shadow_price_source_file") or "").strip()
            and SHA256_RE.fullmatch(str(row.get("shadow_price_source_sha256") or ""))
            is not None,
            "T verification frozen entry cap invalid",
        )
        _expect(
            row.get("truth_state")
            == (
                "OBSERVED_PROXY_FILL"
                if row.get("proxy_fill") == 1
                else "OBSERVED_PROXY_NO_FILL"
            ),
            "T verification three-state truth invalid",
        )
        _expect(row.get("actual_order_fill_observed") is False, "T verification claims an actual fill")
    _validate_source_files(payload.get("source_files"))
    if rows:
        names = {Path(item["path"]).name for item in payload["source_files"]}
        _expect(
            names == {"daily.csv", "stk_limit.csv", "stk_auction_o.csv"},
            "T verification does not bind all strict proxy truth sources",
        )
    _expect(payload.get("calendar") == {"path": CALENDAR_PATH.as_posix(), "sha256": CALENDAR_SHA256}, "T verification calendar drifted")
    boundaries = payload.get("boundaries", {})
    _expect(
        boundaries.get("official_trade_action_allowed") is False
        and boundaries.get("actual_human_trade_included") is False
        and boundaries.get("selection_changed") is False,
        "T verification safety boundary drifted",
    )
    _expect(payload.get("snapshot_sha256") == _payload_snapshot(payload), "T verification snapshot hash drifted")


def _resolve_delayed_public_exit(
    *,
    repo_root: Path,
    open_dates: Sequence[str],
    scheduled_exit_date: str,
    as_of_date: str,
    code: str,
    entry_price: float,
    t_close_price: float,
    table_cache: dict[tuple[str, str], tuple[Path, dict[str, dict[str, str]]]],
) -> tuple[dict[str, Any] | None, str, list[dict[str, Any]]]:
    """Resolve the first non-one-price-limit-down open without skipping truth."""

    try:
        start = open_dates.index(scheduled_exit_date)
    except ValueError as exc:
        raise ExecutableProfitSettlementError(
            "scheduled exit is not a strict SSE open session"
        ) from exc
    try:
        cutoff = open_dates.index(as_of_date)
    except ValueError as exc:
        raise ExecutableProfitSettlementError(
            "as-of date is not a strict SSE open session"
        ) from exc
    if cutoff < start:
        return None, f"PENDING_EXIT_NOT_REACHED:{scheduled_exit_date}", []
    wealth = t_close_price / entry_price
    _expect(math.isfinite(wealth) and wealth > 0, "T close/entry wealth is invalid")
    examined_sources: dict[str, dict[str, Any]] = {}
    blocked_sessions = 0
    suspended_sessions = 0

    def table(trade_date: str, name: str) -> tuple[Path, dict[str, dict[str, str]]] | None:
        key = (trade_date, name)
        if key in table_cache:
            return table_cache[key]
        path = _find_market_file(repo_root, trade_date, name)
        if path is None:
            return None
        loaded = (path, _market_rows(path, trade_date))
        table_cache[key] = loaded
        return loaded

    for offset, trade_date in enumerate(open_dates[start : cutoff + 1]):
        daily_loaded = table(trade_date, "daily")
        limit_loaded = table(trade_date, "stk_limit")
        if daily_loaded is None or limit_loaded is None:
            return None, f"PENDING_EXIT_SOURCE_FILES:{trade_date}", list(examined_sources.values())
        for path in (daily_loaded[0], limit_loaded[0]):
            binding = _source_binding(repo_root, path)
            examined_sources[binding["path"]] = binding
        daily = daily_loaded[1].get(code)
        if daily is None:
            # A complete official daily partition with no stock row is a
            # suspension/non-trading observation. It cannot be used as an exit,
            # but it is not silently dropped from delayed trading-day counts.
            suspended_sessions += 1
            continue
        limit = limit_loaded[1].get(code)
        if limit is None:
            return None, f"PENDING_EXIT_LIMIT_ROW:{trade_date}:{code}", list(examined_sources.values())
        prices = _ohlc(daily)
        pre_close = _finite(daily.get("pre_close"))
        down_limit = _finite(limit.get("down_limit"))
        if (
            any(value is None or value <= 0 for value in prices)
            or pre_close is None
            or pre_close <= 0
            or down_limit is None
            or down_limit <= 0
        ):
            return None, f"PENDING_EXIT_PRICE_OR_LIMIT:{trade_date}:{code}", list(examined_sources.values())
        if _all_at_limit(prices, down_limit):
            wealth *= float(prices[3]) / pre_close
            _expect(math.isfinite(wealth) and wealth > 0, "blocked-exit wealth chain is invalid")
            blocked_sessions += 1
            continue
        wealth *= float(prices[0]) / pre_close
        _expect(math.isfinite(wealth) and wealth > 0, "delayed-exit wealth chain is invalid")
        gross = wealth - 1.0
        if offset == 0:
            reason = "SCHEDULED_T1_OPEN"
        elif blocked_sessions and suspended_sessions:
            reason = "DELAYED_FIRST_TRADABLE_OPEN_AFTER_LIMIT_DOWN_AND_SUSPENSION"
        elif blocked_sessions:
            reason = "DELAYED_FIRST_TRADABLE_OPEN_AFTER_ONE_PRICE_LIMIT_DOWN"
        else:
            reason = "DELAYED_FIRST_TRADABLE_OPEN_AFTER_SUSPENSION"
        return (
            {
                "scheduled_exit_date": scheduled_exit_date,
                "actual_exit_date": trade_date,
                "delayed_trading_days": offset,
                "exit_reason": reason,
                "exit_open_price": float(prices[0]),
                "gross_return": float(gross),
                "blocked_exit_sessions": blocked_sessions,
                "suspended_exit_sessions": suspended_sessions,
            },
            "EXIT_RESOLVED",
            list(examined_sources.values()),
        )
    return (
        None,
        f"PENDING_EXIT_AS_OF_CUTOFF:{as_of_date}",
        list(examined_sources.values()),
    )


def build_t1_settlement(
    repo_root: Path,
    signal_date: str,
    verification: Mapping[str, Any] | None = None,
    *,
    as_of_date: str,
) -> tuple[dict[str, Any] | None, str]:
    repo_root = repo_root.resolve(strict=True)
    _load_contract(repo_root)
    selection_path, selection, selected = load_selection(repo_root, signal_date)
    signal_date = str(selection["signal_date"])
    exec_date = str(selection["exec_date"])
    exit_date = str(selection["exit_date"])
    open_dates = _strict_open_dates(repo_root)
    _validate_adjacent_dates(open_dates, signal_date, exec_date, exit_date)
    as_of_date = _strict_as_of_date(
        open_dates,
        as_of_date,
        signal_date=signal_date,
    )
    if selected and exec_date > as_of_date:
        return None, "PENDING_T_NOT_REACHED"
    verification_path = repo_root / VERIFICATION_ROOT / f"t_verification_{signal_date}.json"
    verification_file_bytes: bytes | None = None
    if verification_path.exists() or verification_path.is_symlink():
        _expect(
            verification_path.is_file() and not verification_path.is_symlink(),
            "immutable T verification path is unsafe",
        )
        try:
            verification_file_bytes = verification_path.read_bytes()
            persisted_verification = json.loads(
                verification_file_bytes.decode("utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ExecutableProfitSettlementError(
                "invalid immutable T verification"
            ) from exc
        _expect(
            isinstance(persisted_verification, dict),
            "immutable T verification is not an object",
        )
        validate_t_verification(persisted_verification)
        persisted_canonical = _canonical_bytes(persisted_verification)
        _expect(
            verification_file_bytes == persisted_canonical,
            "immutable T verification bytes are not canonical",
        )
        if verification is not None:
            validate_t_verification(verification)
            _expect(
                dict(verification) == persisted_verification
                and _canonical_bytes(verification) == persisted_canonical,
                "supplied T verification differs from immutable file",
            )
        verification = persisted_verification
    elif verification is None:
        return None, "PENDING_T_VERIFICATION"
    validate_t_verification(verification)
    selection_binding = _selection_binding(selection_path, selection, selected)
    _expect(verification.get("selection") == selection_binding, "T verification no longer binds the immutable selection")
    verification_file_sha = (
        _sha256_bytes(verification_file_bytes)
        if verification_file_bytes is not None
        else _sha256_bytes(_canonical_bytes(verification))
    )

    verified_rows = verification.get("rows")
    _expect(isinstance(verified_rows, list), "T verification rows missing")
    if any(row.get("proxy_fill") == 1 for row in verified_rows) and exit_date > as_of_date:
        return None, f"PENDING_EXIT_NOT_REACHED:{exit_date}"
    source_file_map: dict[str, dict[str, Any]] = {}
    table_cache: dict[
        tuple[str, str], tuple[Path, dict[str, dict[str, str]]]
    ] = {}

    rows: list[dict[str, Any]] = []
    for verified in verified_rows:
        slot = int(verified["shadow_slot"])
        code = str(verified["ts_code"])
        if verified.get("proxy_fill") == 0:
            rows.append(
                {
                    "shadow_slot": slot,
                    "ts_code": code,
                    "settlement_status": "FINAL_PROXY_NO_FILL",
                    "proxy_fill": 0,
                    "entry_open_price": verified.get("entry_open_price"),
                    "scheduled_exit_date": str(selection["exit_date"]),
                    "actual_exit_date": None,
                    "delayed_trading_days": None,
                    "exit_reason": "NO_PROXY_FILL",
                    "exit_open_price": None,
                    "gross_return": None,
                    "net_return_after_cost": None,
                    "stress_net_return": None,
                    "strategy_slot_return": 0.0,
                    "profit_after_cost": None,
                    "blocked_exit_sessions": 0,
                    "suspended_exit_sessions": 0,
                    "actual_human_trade_return": None,
                }
            )
            continue
        entry = _finite(verified.get("entry_open_price"))
        t_close = _finite(verified.get("t_close_price"))
        _expect(entry is not None and entry > 0, "verified entry price is invalid")
        _expect(t_close is not None and t_close > 0, "verified T close price is invalid")
        exit_truth, exit_status, examined_sources = _resolve_delayed_public_exit(
            repo_root=repo_root,
            open_dates=open_dates,
            scheduled_exit_date=exit_date,
            as_of_date=as_of_date,
            code=code,
            entry_price=float(entry),
            t_close_price=float(t_close),
            table_cache=table_cache,
        )
        for binding in examined_sources:
            source_file_map[str(binding["path"])] = binding
        if exit_truth is None:
            return None, f"{exit_status}:{code}"
        gross = float(exit_truth["gross_return"])
        net = gross - COST_RATE
        stress_net = gross - 0.009
        rows.append(
            {
                "shadow_slot": slot,
                "ts_code": code,
                "settlement_status": "FINAL_FIRST_TRADABLE_OPEN_PUBLIC_MARKET_PROXY",
                "proxy_fill": 1,
                "entry_open_price": float(entry),
                "scheduled_exit_date": exit_truth["scheduled_exit_date"],
                "actual_exit_date": exit_truth["actual_exit_date"],
                "delayed_trading_days": exit_truth["delayed_trading_days"],
                "exit_reason": exit_truth["exit_reason"],
                "exit_open_price": exit_truth["exit_open_price"],
                "gross_return": gross,
                "net_return_after_cost": net,
                "stress_net_return": stress_net,
                "strategy_slot_return": net,
                "profit_after_cost": int(net > 0.0),
                "blocked_exit_sessions": exit_truth["blocked_exit_sessions"],
                "suspended_exit_sessions": exit_truth["suspended_exit_sessions"],
                "actual_human_trade_return": None,
            }
        )

    payload: dict[str, Any] = {
        "schema_version": SETTLEMENT_SCHEMA,
        "artifact_kind": "immutable_t1_public_market_proxy_settlement",
        "contract_id": CONTRACT_ID,
        "status": "FINAL_RESEARCH_PROXY_SETTLEMENT",
        "signal_date": signal_date,
        "exec_date": str(selection["exec_date"]),
        "exit_date": str(selection["exit_date"]),
        "selection": selection_binding,
        "t_verification": {
            "path": (VERIFICATION_ROOT / f"t_verification_{signal_date}.json").as_posix(),
            "file_sha256": verification_file_sha,
            "snapshot_sha256": str(verification["snapshot_sha256"]),
        },
        "cost_contract": {
            "version": COST_VERSION,
            "round_trip_cost_rate": COST_RATE,
            "stress_round_trip_cost_rate": 0.009,
            "profit_event": "net_return_after_cost > 0",
            "nonfill_strategy_slot_return": 0.0,
        },
        "source_files": [source_file_map[key] for key in sorted(source_file_map)],
        "rows": rows,
        "boundaries": {
            "research_only": True,
            "official_trade_action_allowed": False,
            "actual_execution_claimed": False,
            "actual_human_trade_included": False,
            "selection_changed": False,
        },
    }
    payload["snapshot_sha256"] = _payload_snapshot(payload)
    validate_t1_settlement(payload)
    return payload, "FINAL_SETTLED"


def validate_t1_settlement(payload: Mapping[str, Any]) -> None:
    expected = {
        "schema_version",
        "artifact_kind",
        "contract_id",
        "status",
        "signal_date",
        "exec_date",
        "exit_date",
        "selection",
        "t_verification",
        "cost_contract",
        "source_files",
        "rows",
        "boundaries",
        "snapshot_sha256",
    }
    _expect(set(payload) == expected, "T+1 settlement surface drifted")
    _expect(
        payload.get("schema_version") == SETTLEMENT_SCHEMA
        and payload.get("contract_id") == CONTRACT_ID
        and payload.get("status") == "FINAL_RESEARCH_PROXY_SETTLEMENT",
        "T+1 settlement identity drifted",
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
        "T+1 settlement dates invalid",
    )
    selection = _validate_selection_binding(payload.get("selection"))
    _expect(str(selection["path"]).endswith(f"shadow_{signal_date}.json"), "T+1 settlement selection date drifted")
    rows = payload.get("rows")
    _expect(isinstance(rows, list), "T+1 settlement members missing")
    members = selection.get("selected_members")
    _expect(
        isinstance(members, list)
        and [(row.get("shadow_slot"), row.get("ts_code")) for row in rows if isinstance(row, Mapping)]
        == [(row.get("shadow_slot"), row.get("ts_code")) for row in members if isinstance(row, Mapping)],
        "T+1 settlement changed frozen members",
    )
    _expect(
        payload.get("cost_contract")
        == {
            "version": COST_VERSION,
            "round_trip_cost_rate": COST_RATE,
            "stress_round_trip_cost_rate": 0.009,
            "profit_event": "net_return_after_cost > 0",
            "nonfill_strategy_slot_return": 0.0,
        },
        "T+1 settlement cost contract drifted",
    )
    for row in rows:
        _expect(isinstance(row, Mapping), "T+1 settlement row invalid")
        _expect(
            set(row)
            == {
                "shadow_slot",
                "ts_code",
                "settlement_status",
                "proxy_fill",
                "entry_open_price",
                "scheduled_exit_date",
                "actual_exit_date",
                "delayed_trading_days",
                "exit_reason",
                "exit_open_price",
                "gross_return",
                "net_return_after_cost",
                "stress_net_return",
                "strategy_slot_return",
                "profit_after_cost",
                "blocked_exit_sessions",
                "suspended_exit_sessions",
                "actual_human_trade_return",
            },
            "T+1 settlement row surface drifted",
        )
        if row.get("proxy_fill") == 1:
            gross = _finite(row.get("gross_return"))
            net = _finite(row.get("net_return_after_cost"))
            stress = _finite(row.get("stress_net_return"))
            scheduled = _normal_date(row.get("scheduled_exit_date"))
            actual = _normal_date(row.get("actual_exit_date"))
            delay = row.get("delayed_trading_days")
            _expect(
                gross is not None
                and net is not None
                and stress is not None
                and math.isclose(net, gross - COST_RATE, rel_tol=0.0, abs_tol=1e-15)
                and math.isclose(stress, gross - 0.009, rel_tol=0.0, abs_tol=1e-15)
                and row.get("strategy_slot_return") == net,
                "filled settlement return invalid",
            )
            _expect(
                scheduled == exit_date
                and actual >= scheduled
                and type(delay) is int
                and delay >= 0
                and int(row.get("blocked_exit_sessions") or 0) >= 0
                and int(row.get("suspended_exit_sessions") or 0) >= 0
                and int(row.get("blocked_exit_sessions") or 0)
                + int(row.get("suspended_exit_sessions") or 0)
                == delay
                and str(row.get("exit_reason") or ""),
                "filled settlement delayed-exit contract invalid",
            )
            _expect(row.get("profit_after_cost") == int(net > 0.0), "filled settlement profit label invalid")
        else:
            _expect(
                row.get("proxy_fill") == 0
                and row.get("net_return_after_cost") is None
                and row.get("stress_net_return") is None
                and row.get("strategy_slot_return") == 0.0,
                "no-fill settlement return invalid",
            )
            _expect(
                row.get("scheduled_exit_date") == exit_date
                and row.get("actual_exit_date") is None
                and row.get("delayed_trading_days") is None
                and row.get("exit_reason") == "NO_PROXY_FILL",
                "no-fill settlement exit fields invalid",
            )
        _expect(row.get("actual_human_trade_return") is None, "human trade leaked into research settlement")
    verification = payload.get("t_verification")
    _expect(
        isinstance(verification, Mapping)
        and set(verification) == {"path", "file_sha256", "snapshot_sha256"}
        and re.fullmatch(
            r"data/decision_executable_profit/forward/verifications/t_verification_20\d{6}\.json",
            str(verification.get("path") or ""),
        )
        is not None
        and SHA256_RE.fullmatch(str(verification.get("file_sha256") or "")) is not None
        and SHA256_RE.fullmatch(str(verification.get("snapshot_sha256") or "")) is not None,
        "T+1 settlement T verification binding invalid",
    )
    _validate_source_files(payload.get("source_files"))
    if any(row.get("proxy_fill") == 1 for row in rows):
        names = {Path(item["path"]).name for item in payload["source_files"]}
        _expect(
            names == {"daily.csv", "stk_limit.csv"},
            "T+1 settlement does not bind both exit truth sources",
        )
    boundaries = payload.get("boundaries", {})
    _expect(
        boundaries.get("official_trade_action_allowed") is False
        and boundaries.get("actual_execution_claimed") is False
        and boundaries.get("actual_human_trade_included") is False
        and boundaries.get("selection_changed") is False,
        "T+1 settlement safety boundary drifted",
    )
    _expect(payload.get("snapshot_sha256") == _payload_snapshot(payload), "T+1 settlement snapshot hash drifted")


def materialize_t_verification(repo_root: Path, payload: Mapping[str, Any]) -> Path:
    validate_t_verification(payload)
    repo_root = repo_root.resolve(strict=True)
    output = _ensure_directory(repo_root, VERIFICATION_ROOT)
    path = output / f"t_verification_{payload['signal_date']}.json"
    with _locked(output):
        return _install_immutable(path, payload)


def materialize_t1_settlement(repo_root: Path, payload: Mapping[str, Any]) -> Path:
    validate_t1_settlement(payload)
    repo_root = repo_root.resolve(strict=True)
    output = _ensure_directory(repo_root, SETTLEMENT_ROOT)
    path = output / f"settlement_{payload['signal_date']}.json"
    with _locked(output):
        return _install_immutable(path, payload)


def _metric(value: Any) -> float | None:
    number = _finite(value)
    return round(number, 12) if number is not None else None


def _portfolio_metrics(daily_returns: Sequence[tuple[str, float]]) -> dict[str, Any]:
    nav = 1.0
    peak = 1.0
    max_drawdown = 0.0
    path: list[dict[str, Any]] = []
    for signal_date, daily_return in sorted(daily_returns):
        nav *= 1.0 + daily_return
        peak = max(peak, nav)
        drawdown = nav / peak - 1.0
        max_drawdown = min(max_drawdown, drawdown)
        path.append(
            {
                "signal_date": signal_date,
                "equal_weight_strategy_return": _metric(daily_return),
                "nav": _metric(nav),
                "drawdown": _metric(drawdown),
            }
        )
    return {
        "equal_weight_cumulative_return": _metric(nav - 1.0) if path else None,
        "maximum_drawdown": _metric(max_drawdown) if path else None,
        "daily_portfolio": path,
    }


def _cohort_metrics(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    selection_dates = sorted({str(row["signal_date"]) for row in records})
    effective_dates = []
    for signal_date in selection_dates:
        date_rows = [row for row in records if row.get("signal_date") == signal_date]
        if date_rows and all(row.get("terminal") is True for row in date_rows):
            effective_dates.append(signal_date)
    filled_returns = [
        float(row["net_return_after_cost"])
        for row in records
        if _finite(row.get("net_return_after_cost")) is not None
    ]
    stress_returns = [
        float(row["stress_net_return"])
        for row in records
        if _finite(row.get("stress_net_return")) is not None
    ]
    t_validated_slots = sum(row.get("t_validated") is True for row in records)
    proxy_fill_slots = sum(row.get("proxy_fill") == 1 for row in records)
    pending_validation_slots = sum(
        row.get("t_validated") is not True for row in records
    )
    pending_settlement_slots = sum(
        row.get("t_validated") is True and row.get("terminal") is not True
        for row in records
    )
    blocked_exit_slots = sum(
        int(row.get("blocked_exit_sessions") or 0) > 0 for row in records
    )
    blocked_exit_sessions = sum(
        int(row.get("blocked_exit_sessions") or 0) for row in records
    )
    tail_count = max(1, math.ceil(len(filled_returns) * 0.10)) if filled_returns else 0
    daily: list[tuple[str, float]] = []
    for signal_date in effective_dates:
        date_rows = [row for row in records if row.get("signal_date") == signal_date]
        if date_rows and all(row.get("terminal") is True for row in date_rows):
            daily.append(
                (
                    signal_date,
                    sum(float(row["strategy_slot_return"]) for row in date_rows)
                    / len(date_rows),
                )
            )
    portfolio = _portfolio_metrics(daily)
    return {
        "selection_dates": len(selection_dates),
        "effective_dates": len(effective_dates),
        "selected_slots": len(records),
        "t_validated_slots": t_validated_slots,
        "proxy_fill_slots": proxy_fill_slots,
        "proxy_no_fill_slots": sum(row.get("proxy_fill") == 0 for row in records),
        "proxy_buyable_rate": _metric(
            proxy_fill_slots / t_validated_slots if t_validated_slots else None
        ),
        "t1_settled_slots": len(filled_returns),
        "terminal_slots": sum(row.get("terminal") is True for row in records),
        "pending_validation_slots": pending_validation_slots,
        "pending_settlement_slots": pending_settlement_slots,
        "pending_slots": pending_validation_slots + pending_settlement_slots,
        "pending_exit_slots": sum(
            row.get("proxy_fill") == 1 and row.get("terminal") is not True
            for row in records
        ),
        "blocked_exit_slots": blocked_exit_slots,
        "blocked_exit_sessions": blocked_exit_sessions,
        "historically_blocked_exit_slots": blocked_exit_slots,
        "historically_blocked_exit_sessions": blocked_exit_sessions,
        "delayed_exit_slots": sum(
            int(row.get("delayed_trading_days") or 0) > 0 for row in records
        ),
        "wins_after_cost": sum(value > 0.0 for value in filled_returns),
        "win_rate": _metric(
            sum(value > 0.0 for value in filled_returns) / len(filled_returns)
            if filled_returns
            else None
        ),
        "mean_net_return_after_cost": _metric(
            sum(filled_returns) / len(filled_returns) if filled_returns else None
        ),
        "median_net_return_after_cost": _metric(
            median(filled_returns) if filled_returns else None
        ),
        "realized_big_loss_rate_at_minus_3pct": _metric(
            sum(value <= -0.03 for value in filled_returns) / len(filled_returns)
            if filled_returns
            else None
        ),
        "worst_10pct_mean_net_return": _metric(
            sum(sorted(filled_returns)[:tail_count]) / tail_count
            if tail_count
            else None
        ),
        "worst_trade_net_return": _metric(min(filled_returns) if filled_returns else None),
        "mean_stress_net_return_90bp": _metric(
            sum(stress_returns) / len(stress_returns) if stress_returns else None
        ),
        **portfolio,
    }


def build_statistics(repo_root: Path, *, as_of_date: str) -> dict[str, Any]:
    repo_root = repo_root.resolve(strict=True)
    _load_contract(repo_root)
    open_dates = _strict_open_dates(repo_root)
    as_of_date = _strict_as_of_date(
        open_dates,
        as_of_date,
        signal_date=MINIMUM_SIGNAL_DATE,
    )
    selection_directory = repo_root / SELECTION_ROOT
    records: list[dict[str, Any]] = []
    input_files: list[dict[str, str]] = []
    no_selected_dates = 0
    if selection_directory.is_dir() and not selection_directory.is_symlink():
        selection_paths = sorted(selection_directory.glob("shadow_20??????.json"))
    else:
        selection_paths = []
    for path in selection_paths:
        match = re.fullmatch(r"shadow_(20\d{6})\.json", path.name)
        if match is None:
            continue
        signal_date = match.group(1)
        if signal_date > as_of_date:
            continue
        selection_path, selection, selected = load_selection(repo_root, signal_date)
        input_files.append({"path": selection_path.relative_to(repo_root).as_posix(), "sha256": _sha256(selection_path)})
        if not selected:
            no_selected_dates += 1
            continue
        verification_path = repo_root / VERIFICATION_ROOT / f"t_verification_{signal_date}.json"
        verification_rows: dict[int, Mapping[str, Any]] = {}
        if verification_path.exists() and str(selection["exec_date"]) <= as_of_date:
            _expect(verification_path.is_file() and not verification_path.is_symlink(), "T verification path is unsafe")
            verification = _read_json(verification_path, label="T verification")
            validate_t_verification(verification)
            _expect(
                verification.get("selection") == _selection_binding(selection_path, selection, selected),
                "statistics found a T verification with a stale selection binding",
            )
            verification_rows = {int(row["shadow_slot"]): row for row in verification["rows"]}
            input_files.append({"path": verification_path.relative_to(repo_root).as_posix(), "sha256": _sha256(verification_path)})
        settlement_path = repo_root / SETTLEMENT_ROOT / f"settlement_{signal_date}.json"
        settlement_rows: dict[int, Mapping[str, Any]] = {}
        if settlement_path.exists() and str(selection["exec_date"]) <= as_of_date:
            _expect(settlement_path.is_file() and not settlement_path.is_symlink(), "T+1 settlement path is unsafe")
            settlement = _read_json(settlement_path, label="T+1 settlement")
            validate_t1_settlement(settlement)
            settlement_is_observable = all(
                row.get("proxy_fill") == 0
                or str(row.get("actual_exit_date") or "") <= as_of_date
                for row in settlement.get("rows", [])
                if isinstance(row, Mapping)
            )
            if not settlement_is_observable:
                settlement = None
            if settlement is None:
                settlement_rows = {}
            else:
                _expect(
                    settlement.get("selection") == _selection_binding(selection_path, selection, selected),
                    "statistics found a settlement with a stale selection binding",
                )
                _expect(verification_path.is_file(), "settlement exists without immutable T verification")
                _expect(
                    settlement.get("t_verification", {}).get("file_sha256") == _sha256(verification_path),
                    "settlement no longer binds immutable T verification bytes",
                )
                for row in settlement["rows"]:
                    if row.get("proxy_fill") != 1:
                        continue
                    scheduled = str(row["scheduled_exit_date"])
                    actual = str(row["actual_exit_date"])
                    start_index = open_dates.index(scheduled) if scheduled in open_dates else -1
                    end_index = open_dates.index(actual) if actual in open_dates else -1
                    _expect(
                        start_index >= 0
                        and end_index >= start_index
                        and end_index - start_index
                        == int(row["delayed_trading_days"]),
                        "settlement delayed exit is not bound to strict SSE sessions",
                    )
                    source_paths = {
                        str(item["path"]) for item in settlement["source_files"]
                    }
                    for examined_date in open_dates[start_index : end_index + 1]:
                        _expect(
                            any(
                                examined_date in path and Path(path).name == "daily.csv"
                                for path in source_paths
                            )
                            and any(
                                examined_date in path
                                and Path(path).name == "stk_limit.csv"
                                for path in source_paths
                            ),
                            "settlement skipped a strict SSE exit truth partition",
                        )
                settlement_rows = {int(row["shadow_slot"]): row for row in settlement["rows"]}
                input_files.append({"path": settlement_path.relative_to(repo_root).as_posix(), "sha256": _sha256(settlement_path)})
        for frozen in selected:
            slot = int(frozen["shadow_slot"])
            verified = verification_rows.get(slot)
            settled = settlement_rows.get(slot)
            proxy_fill = verified.get("proxy_fill") if verified is not None else None
            terminal = bool(settled is not None or proxy_fill == 0)
            strategy_slot_return = (
                settled.get("strategy_slot_return")
                if settled is not None
                else 0.0
                if proxy_fill == 0
                else None
            )
            records.append(
                {
                    "signal_date": signal_date,
                    "shadow_slot": slot,
                    "ts_code": str(frozen["ts_code"]),
                    "stage_transition": str(frozen["stage_transition"]),
                    "t_validated": verified is not None,
                    "proxy_fill": proxy_fill,
                    "terminal": terminal,
                    "net_return_after_cost": settled.get("net_return_after_cost") if settled is not None else None,
                    "stress_net_return": settled.get("stress_net_return") if settled is not None else None,
                    "strategy_slot_return": strategy_slot_return,
                    "delayed_trading_days": settled.get("delayed_trading_days") if settled is not None else None,
                    "blocked_exit_sessions": settled.get("blocked_exit_sessions") if settled is not None else 0,
                }
            )

    all_metrics = _cohort_metrics(records)
    slot1 = _cohort_metrics([row for row in records if row["shadow_slot"] == 1])
    slot2 = _cohort_metrics([row for row in records if row["shadow_slot"] == 2])
    stage_2_to_3 = _cohort_metrics(
        [row for row in records if row["stage_transition"] == "2→3"]
    )
    stage_3_to_4 = _cohort_metrics(
        [row for row in records if row["stage_transition"] == "3→4"]
    )
    dates = sorted({str(row["signal_date"]) for row in records})
    selection_dates = len(dates) + no_selected_dates
    progress = {
        "observed_signal_dates": selection_dates,
        "target_signal_dates": TARGET_FORWARD_DATES,
        "remaining_signal_dates": max(0, TARGET_FORWARD_DATES - selection_dates),
        "progress_pct": _metric(min(1.0, selection_dates / TARGET_FORWARD_DATES) * 100.0),
        "release_sample_reached": selection_dates >= TARGET_FORWARD_DATES,
    }
    input_files.sort(key=lambda row: row["path"])
    payload: dict[str, Any] = {
        "schema_version": STATISTICS_SCHEMA,
        "artifact_kind": "deterministic_executable_profit_shadow_statistics",
        "contract_id": CONTRACT_ID,
        "status": "INTERNAL_RESEARCH_SHADOW_ONLY",
        "as_of_date": as_of_date,
        "scope": {
            "minimum_signal_date": MINIMUM_SIGNAL_DATE,
            "selection_dates": selection_dates,
            "no_selected_dates": no_selected_dates,
            "historical_backfill_included": False,
            "human_actual_trade_ledger_included": False,
        },
        "forward_signal_date_progress_180": progress,
        "cohorts": {
            "all_selected_slots": all_metrics,
            "shadow_slot_1": slot1,
            "shadow_slot_2": slot2,
            "stage_2_to_3": stage_2_to_3,
            "stage_3_to_4": stage_3_to_4,
        },
        "probability_diagnostics": {
            "status": "UNCALIBRATED",
            "brier_score": None,
            "expected_calibration_error": None,
            "log_loss": None,
            "reason": "research_joint_proxy_score is not a calibrated probability",
        },
        "excluded_ledgers": [
            "historical_oof_top10_ledger",
            "p_fill_shadow_top2_ledger",
            "legacy_observation_statistics",
            "manual_actual_trade_ledger",
            "official_trade_action_ledger",
        ],
        "pending_definitions": {
            "pending_validation_slots": "frozen selected slots without immutable complete T truth",
            "pending_exit_slots": "proxy-filled slots without a resolved first tradable open from scheduled T+1 onward",
            "historically_blocked_exit_slots": "settled slots that crossed at least one observed one-price limit-down session",
        },
        "input_files": input_files,
        "input_files_sha256": _canonical_sha256(input_files),
        "boundaries": {
            "research_only": True,
            "official_trade_action_allowed": False,
            "actual_execution_claimed": False,
            "actual_human_trade_statistics_are_separate": True,
        },
    }
    payload["snapshot_sha256"] = _payload_snapshot(payload)
    validate_statistics(payload)
    return payload


def validate_statistics(payload: Mapping[str, Any]) -> None:
    _expect(
        payload.get("schema_version") == STATISTICS_SCHEMA
        and payload.get("contract_id") == CONTRACT_ID
        and payload.get("status") == "INTERNAL_RESEARCH_SHADOW_ONLY",
        "statistics identity drifted",
    )
    as_of_date = _normal_date(payload.get("as_of_date"))
    _expect(
        payload.get("as_of_date") == as_of_date
        and DATE_RE.fullmatch(as_of_date) is not None
        and as_of_date >= MINIMUM_SIGNAL_DATE,
        "statistics as-of date invalid",
    )
    cohorts = payload.get("cohorts")
    _expect(
        isinstance(cohorts, Mapping)
        and set(cohorts)
        == {
            "all_selected_slots",
            "shadow_slot_1",
            "shadow_slot_2",
            "stage_2_to_3",
            "stage_3_to_4",
        },
        "statistics cohorts drifted",
    )
    _expect(
        payload.get("probability_diagnostics")
        == {
            "status": "UNCALIBRATED",
            "brier_score": None,
            "expected_calibration_error": None,
            "log_loss": None,
            "reason": "research_joint_proxy_score is not a calibrated probability",
        },
        "statistics probability disclosure drifted",
    )
    _expect(
        payload.get("excluded_ledgers")
        == [
            "historical_oof_top10_ledger",
            "p_fill_shadow_top2_ledger",
            "legacy_observation_statistics",
            "manual_actual_trade_ledger",
            "official_trade_action_ledger",
        ],
        "statistics ledger separation drifted",
    )
    progress = payload.get("forward_signal_date_progress_180", {})
    _expect(progress.get("target_signal_dates") == TARGET_FORWARD_DATES, "statistics 180-day target drifted")
    boundaries = payload.get("boundaries", {})
    _expect(
        boundaries.get("official_trade_action_allowed") is False
        and boundaries.get("actual_execution_claimed") is False
        and boundaries.get("actual_human_trade_statistics_are_separate") is True,
        "statistics safety boundaries drifted",
    )
    _expect(payload.get("snapshot_sha256") == _payload_snapshot(payload), "statistics snapshot hash drifted")


def materialize_statistics(repo_root: Path, payload: Mapping[str, Any]) -> Path:
    validate_statistics(payload)
    repo_root = repo_root.resolve(strict=True)
    output = _ensure_directory(repo_root, STATISTICS_PATH.parent)
    path = output / STATISTICS_PATH.name
    with _locked(output):
        if path.exists():
            _expect(
                path.is_file() and not path.is_symlink(),
                "statistics projection is unsafe",
            )
            existing = _read_json(path, label="existing statistics projection")
            validate_statistics(existing)
            _expect(
                str(existing["as_of_date"]) <= str(payload["as_of_date"]),
                "statistics as-of pointer cannot move backward",
            )
        return _replace_projection(path, payload)


def settle_signal_date(
    repo_root: Path,
    signal_date: str,
    *,
    as_of_date: str,
) -> dict[str, Any]:
    """Append available immutable T/T+1 truth and rebuild deterministic statistics."""

    repo_root = repo_root.resolve(strict=True)
    verification_payload, verification_status = build_t_verification(
        repo_root,
        signal_date,
        as_of_date=as_of_date,
    )
    verification_path: Path | None = None
    if verification_payload is not None:
        verification_path = materialize_t_verification(repo_root, verification_payload)
    else:
        existing_verification = (
            repo_root
            / VERIFICATION_ROOT
            / f"t_verification_{_normal_date(signal_date)}.json"
        )
        if (
            verification_status != "PENDING_T_NOT_REACHED"
            and existing_verification.is_file()
            and not existing_verification.is_symlink()
        ):
            verification_payload = _read_json(
                existing_verification,
                label="existing immutable T verification",
            )
            validate_t_verification(verification_payload)
            verification_path = existing_verification
            verification_status = "T_VERIFIED_IMMUTABLE_EXISTING"
    settlement_payload, settlement_status = build_t1_settlement(
        repo_root,
        signal_date,
        verification=verification_payload,
        as_of_date=as_of_date,
    )
    settlement_path: Path | None = None
    if settlement_payload is not None:
        _expect(verification_path is not None, "cannot settle before immutable T verification")
        settlement_path = materialize_t1_settlement(repo_root, settlement_payload)
    else:
        existing_settlement = (
            repo_root
            / SETTLEMENT_ROOT
            / f"settlement_{_normal_date(signal_date)}.json"
        )
        future_cutoff = settlement_status.startswith(
            (
                "PENDING_T_NOT_REACHED",
                "PENDING_EXIT_NOT_REACHED",
                "PENDING_EXIT_AS_OF_CUTOFF",
            )
        )
        if (
            not future_cutoff
            and existing_settlement.is_file()
            and not existing_settlement.is_symlink()
        ):
            existing_payload = _read_json(
                existing_settlement,
                label="existing immutable T+1 settlement",
            )
            validate_t1_settlement(existing_payload)
            settlement_path = existing_settlement
            settlement_status = "FINAL_SETTLED_IMMUTABLE_EXISTING"
    statistics = build_statistics(repo_root, as_of_date=as_of_date)
    statistics_path = materialize_statistics(repo_root, statistics)
    return {
        "signal_date": _normal_date(signal_date),
        "as_of_date": _normal_date(as_of_date),
        "t_verification_status": verification_status,
        "t_verification_path": (
            verification_path.relative_to(repo_root).as_posix()
            if verification_path is not None
            else None
        ),
        "t1_settlement_status": settlement_status,
        "t1_settlement_path": (
            settlement_path.relative_to(repo_root).as_posix()
            if settlement_path is not None
            else None
        ),
        "statistics_path": statistics_path.relative_to(repo_root).as_posix(),
        "progress": statistics["forward_signal_date_progress_180"],
        "official_trade_action_created": False,
    }


__all__ = [
    "CALENDAR_PATH",
    "CALENDAR_SHA256",
    "CONTRACT_ID",
    "COST_RATE",
    "ExecutableProfitSettlementError",
    "SETTLEMENT_ROOT",
    "STATISTICS_PATH",
    "T_VERIFICATION_SCHEMA",
    "VERIFICATION_ROOT",
    "build_statistics",
    "build_t1_settlement",
    "build_t_verification",
    "load_selection",
    "materialize_statistics",
    "materialize_t1_settlement",
    "materialize_t_verification",
    "settle_signal_date",
    "validate_statistics",
    "validate_t1_settlement",
    "validate_t_verification",
]
