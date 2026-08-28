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
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

SCHEMA_VERSION = "dc20_primary_profit_forward_shadow_selection_v2"
ARTIFACT_KIND = "d_frozen_primary_mixed_profit_forward_shadow"
INDEX_SCHEMA_VERSION = "dc20_primary_profit_forward_shadow_index_v2"
INDEX_KIND = "primary_mixed_profit_forward_shadow_pointer"
CONTRACT_SCHEMA = "dc20_primary_profit_forward_shadow_bridge_contract_v1"
CONTRACT_ID = "dc20_primary_profit_forward_shadow_bridge_20260829_v1"
STATUS = "INTERNAL_RESEARCH_SHADOW_ONLY"
ENTRY_POLICY_ID = "dc20_public_market_buyable_proxy_v1"

CONTRACT_PATH = Path("models/decision_primary_profit_forward_shadow_bridge_contract.json")
CALENDAR_PATH = Path("data/market/trade_cal_sse.csv")
MODEL_PATH = Path(
    "work/executable-profit-lagged-features-20260824/outputs/"
    "internal_forward_challenger.pkl"
)
MIXED_ROOT = Path("outputs/decision/executable_profit_research")
OUTPUT_ROOT = Path("data/decision_executable_profit/forward/selections")
PRIMARY_INDEX_PATH = OUTPUT_ROOT / "primary_mixed_index.json"
STATISTICS_PATH = Path("data/decision_executable_profit/forward/statistics/summary.json")
VERIFICATION_ROOT = Path("data/decision_executable_profit/forward/verifications")
SETTLEMENT_ROOT = Path("data/decision_executable_profit/forward/settlements")
PUBLIC_ROOT = Path("outputs/decision/executable_profit_research")
PUBLIC_INDEX_PATH = PUBLIC_ROOT / "shadow_index.json"
PUBLIC_STATE_SCHEMA = "dc20_primary_profit_forward_shadow_public_state_v1"
PUBLIC_INDEX_SCHEMA = "dc20_primary_profit_forward_shadow_public_index_v1"

EXPECTED_CALENDAR_SHA256 = (
    "150a3e29ebd6e050d55caee1df218ef5dcfc3542053d8a7478d6be50d09fd748"
)
EXPECTED_MODEL_SHA256 = (
    "42dfb497d4457db9fbdff4180c510fee1ea18ab56696253b06220d981f88d209"
)
EXPECTED_FEATURE_COLUMNS_SHA256 = (
    "a07c3c2d688e1e0eb5aaaa891ffd3039d5ca3f6bb26f20e80f88611833893048"
)

DATE_RE = re.compile(r"20\d{6}")
CODE_RE = re.compile(r"\d{6}\.(?:SH|SZ)")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
SHANGHAI = ZoneInfo("Asia/Shanghai")

BOUNDARIES = {
    "research_only": True,
    "proxy_scores_uncalibrated": True,
    "formal_probability_allowed": False,
    "formal_rank_allowed": False,
    "may_change_promotion_membership_or_rank": False,
    "official_trade_action_allowed": False,
    "may_create_trade_action": False,
    "broker_or_order_integration_allowed": False,
    "actual_execution_claimed": False,
}

RANKING_CONTRACT = {
    "candidate_scope": "exact P0 frozen promotion TopN only",
    "candidate_count_rule": "show exactly N for 0<=N<=10; never pad",
    "ranking_authority": "already-published P1 mixed projection order; never rescore",
    "primary_sort": "research_joint_proxy_score descending",
    "tie_breakers": [
        "research_conditional_profit_score descending",
        "research_fill_proxy_score descending",
        "ts_code ascending",
    ],
    "top2_top3_exact_joint_tie_policy": "FAIL_CLOSED_FOR_N_AT_LEAST_3",
    "shadow_slots": 2,
    "shadow_slot_rule": "min(2, N); no padding",
    "entry_policy_id": ENTRY_POLICY_ID,
    "entry_price_rule": "T proxy open must not exceed D-frozen shadow_max_price",
    "membership_or_promotion_rank_may_change": False,
    "actual_order_fill_claimed": False,
}

ROW_FIELDS = (
    "ts_code",
    "name",
    "industry",
    "stage_transition",
    "promotion_rank",
    "predicted_promotion_probability",
    "research_fill_proxy_score",
    "research_conditional_profit_score",
    "research_joint_proxy_score",
    "source_executable_profit_research_rank",
    "internal_shadow_order",
    "internal_shadow_selected",
    "shadow_slot",
    "shadow_max_price",
    "shadow_price_basis",
    "shadow_price_source_sha256",
)

PUBLIC_STATE_ROW_FIELDS = (
    "shadow_slot",
    "ts_code",
    "name",
    "promotion_rank",
    "t_status",
    "t_truth_state",
    "proxy_fill",
    "t1_status",
    "scheduled_exit_date",
    "actual_exit_date",
    "net_return_after_cost",
    "strategy_slot_return",
)


class PrimaryProfitForwardShadowError(ValueError):
    pass


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise PrimaryProfitForwardShadowError(message)


def _normal_date(value: Any) -> str:
    text = str(value or "").strip()
    return text if DATE_RE.fullmatch(text) else ""


def _normal_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text if CODE_RE.fullmatch(text) else ""


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    try:
        if hasattr(value, "item"):
            return _json_safe(value.item())
    except (TypeError, ValueError):
        pass
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
    return _sha256_bytes(_canonical_json_bytes(value))


def _safe_file(root: Path, relative: Path, *, label: str) -> Path:
    root = root.resolve(strict=True)
    _expect(not relative.is_absolute() and ".." not in relative.parts, f"{label} path is unsafe")
    lexical = Path(os.path.abspath(root / relative))
    try:
        lexical.relative_to(root)
        resolved = lexical.resolve(strict=True)
    except (FileNotFoundError, ValueError) as exc:
        raise PrimaryProfitForwardShadowError(f"{label} is missing or escaped") from exc
    _expect(
        resolved == lexical
        and lexical.is_file()
        and not lexical.is_symlink()
        and lexical.stat().st_size > 0,
        f"{label} is unsafe",
    )
    return lexical


def _safe_directory(root: Path, relative: Path, *, label: str) -> Path:
    """Create one repo-owned directory without following ancestor symlinks."""

    root = root.resolve(strict=True)
    _expect(
        not relative.is_absolute()
        and bool(relative.parts)
        and ".." not in relative.parts,
        f"{label} path is unsafe",
    )
    lexical = Path(os.path.abspath(root / relative))
    try:
        lexical.relative_to(root)
    except ValueError as exc:
        raise PrimaryProfitForwardShadowError(
            f"{label} escaped repository root"
        ) from exc

    current = root
    for part in relative.parts:
        current = current / part
        _expect(not current.is_symlink(), f"{label} ancestor is a symlink")
        if current.exists():
            _expect(current.is_dir(), f"{label} ancestor is not a directory")
        else:
            try:
                current.mkdir()
            except OSError as exc:
                raise PrimaryProfitForwardShadowError(
                    f"{label} could not be created safely"
                ) from exc
        try:
            resolved = current.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise PrimaryProfitForwardShadowError(f"{label} is unsafe") from exc
        _expect(
            resolved == current and not current.is_symlink(),
            f"{label} escaped repository root",
        )
    _expect(current == lexical, f"{label} path is unsafe")
    return current


def _safe_output_directory(root: Path) -> Path:
    return _safe_directory(root, OUTPUT_ROOT, label="Shadow output directory")


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PrimaryProfitForwardShadowError(f"invalid {label}: {path}") from exc
    _expect(isinstance(value, dict), f"{label} must be an object")
    return value


def _parse_aware(value: Any, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise PrimaryProfitForwardShadowError(f"{label} is invalid") from exc
    _expect(parsed.tzinfo is not None, f"{label} must be timezone-aware")
    return parsed


def _selection_window(signal_date: str, exec_date: str) -> tuple[datetime, datetime]:
    start = datetime.strptime(
        signal_date + " 15:00:00 +0800", "%Y%m%d %H:%M:%S %z"
    ).astimezone(SHANGHAI)
    end = datetime.strptime(
        exec_date + " 09:20:00 +0800", "%Y%m%d %H:%M:%S %z"
    ).astimezone(SHANGHAI)
    return start, end


def _load_contract(repo_root: Path) -> tuple[Path, dict[str, Any]]:
    path = _safe_file(repo_root, CONTRACT_PATH, label="P1 Shadow bridge contract")
    contract = _read_json(path, label="P1 Shadow bridge contract")
    _expect(
        contract.get("schema_version") == CONTRACT_SCHEMA
        and contract.get("contract_id") == CONTRACT_ID
        and contract.get("status") == STATUS,
        "P1 Shadow bridge contract identity drifted",
    )
    authority = contract.get("authority")
    inputs = contract.get("input")
    calendar = contract.get("calendar")
    model = contract.get("model")
    selection = contract.get("selection")
    timing = contract.get("timing")
    _expect(
        isinstance(authority, Mapping)
        and authority.get("repository") == "njedu2023-prog/DC20"
        and authority.get("branch") == "main"
        and authority.get("runtime_dependency_on_codex") is False
        and authority.get("runtime_dependency_on_top10_decision") is False,
        "P1 Shadow authority drifted",
    )
    _expect(
        isinstance(inputs, Mapping)
        and inputs.get("generation_mode") == "NATURAL"
        and inputs.get("projection_status") == "PROSPECTIVE_RESEARCH"
        and inputs.get("mixed_index_role")
        == "MUTABLE_POINTER_OBSERVED_AT_FREEZE_AUDIT_ONLY"
        and inputs.get("historical_revalidation_requires_current_mixed_index")
        is False
        and inputs.get("complete_primary_profit_bundle_validation_required") is True
        and inputs.get("model_rescoring_allowed") is False
        and inputs.get("action_input_allowed") is False
        and inputs.get("auction_prediction_input_allowed") is False
        and inputs.get("network_market_input_allowed") is False,
        "P1 Shadow input boundary drifted",
    )
    _expect(
        isinstance(calendar, Mapping)
        and calendar.get("exchange") == "SSE"
        and calendar.get("strict") is True
        and calendar.get("path") == CALENDAR_PATH.as_posix()
        and calendar.get("sha256") == EXPECTED_CALENDAR_SHA256,
        "P1 Shadow calendar contract drifted",
    )
    _expect(
        isinstance(model, Mapping)
        and model.get("artifact_path") == MODEL_PATH.as_posix()
        and model.get("artifact_sha256") == EXPECTED_MODEL_SHA256
        and model.get("feature_columns_sha256") == EXPECTED_FEATURE_COLUMNS_SHA256
        and model.get("feature_count") == 156
        and model.get("calibrated_probability") is False,
        "P1 Shadow model contract drifted",
    )
    _expect(
        isinstance(selection, Mapping)
        and selection.get("schema_version") == SCHEMA_VERSION
        and selection.get("artifact_pattern")
        == f"{OUTPUT_ROOT.as_posix()}/shadow_<D>.json"
        and selection.get("csv_pattern")
        == f"{OUTPUT_ROOT.as_posix()}/shadow_<D>.csv"
        and selection.get("primary_pointer") == PRIMARY_INDEX_PATH.as_posix()
        and selection.get("statistics_projection") == STATISTICS_PATH.as_posix()
        and selection.get("public_state_pattern")
        == f"{PUBLIC_ROOT.as_posix()}/shadow_state_<D>_asof_<A>.json"
        and selection.get("public_pointer") == PUBLIC_INDEX_PATH.as_posix()
        and selection.get("ranking_authority")
        == "already-published P1 mixed projection order; never rescore"
        and selection.get("selected_slots_rule") == "min(2, N); no padding"
        and selection.get("entry_policy_id") == ENTRY_POLICY_ID
        and selection.get("same_date_different_payload") == "REJECT"
        and selection.get("out_of_order_or_backfill") == "REJECT",
        "P1 Shadow selection contract drifted",
    )
    _expect(
        isinstance(timing, Mapping)
        and timing.get("timezone") == "Asia/Shanghai"
        and timing.get("source_generated_no_later_than_selection") is True
        and timing.get("post_T_information_allowed") is False
        and timing.get("retrospective_recovery_allowed") is False,
        "P1 Shadow timing contract drifted",
    )
    _expect(contract.get("boundaries") == BOUNDARIES, "P1 Shadow boundaries drifted")
    return path, contract


def _open_dates(repo_root: Path) -> tuple[Path, list[str]]:
    path = _safe_file(repo_root, CALENDAR_PATH, label="strict SSE calendar")
    _expect(_sha256(path) == EXPECTED_CALENDAR_SHA256, "strict SSE calendar SHA drifted")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise PrimaryProfitForwardShadowError("strict SSE calendar is invalid") from exc
    opened = sorted(
        str(row.get("cal_date") or "")
        for row in rows
        if row.get("exchange") == "SSE"
        and row.get("is_open") == "1"
        and DATE_RE.fullmatch(str(row.get("cal_date") or ""))
    )
    _expect(opened and len(opened) == len(set(opened)), "strict SSE open sessions are invalid")
    return path, opened


def _validate_adjacent_dates(
    open_dates: Sequence[str], signal_date: str, exec_date: str, exit_date: str
) -> None:
    try:
        position = open_dates.index(signal_date)
    except ValueError as exc:
        raise PrimaryProfitForwardShadowError("P1 Shadow D is not an SSE open session") from exc
    _expect(
        position + 2 < len(open_dates)
        and open_dates[position + 1] == exec_date
        and open_dates[position + 2] == exit_date,
        "P1 Shadow D/T/T+1 are not adjacent strict SSE sessions",
    )


def _load_primary_bundle(repo_root: Path, signal_date: str) -> dict[str, Any]:
    # This validator only re-reads and hashes the already-published P1/P0 files.
    # It does not call either sealed model or perform inference.
    try:
        from scripts.publish_primary_profit_rankings import (
            PrimaryProfitRankingError,
            validate_primary_profit_bundle,
        )
    except ImportError as exc:
        raise PrimaryProfitForwardShadowError(
            "published P1 validator is unavailable"
        ) from exc
    try:
        return validate_primary_profit_bundle(
            repo_root,
            expected_signal_date=signal_date,
            expected_generation_mode="NATURAL",
        )
    except (OSError, ValueError, PrimaryProfitRankingError) as exc:
        raise PrimaryProfitForwardShadowError(
            "published P1 single/mixed bundle validation failed"
        ) from exc


def _load_dated_primary_mixed_projection(
    repo_root: Path,
    signal_date: str,
) -> dict[str, Any]:
    """Validate immutable dated P1 mixed bytes without consulting latest pointers."""

    try:
        from scripts.publish_primary_profit_rankings import (
            MIXED_ROW_FIELDS,
            PrimaryProfitRankingError,
            _csv_bytes as _primary_projection_csv_bytes,
            load_primary_inputs,
            validate_mixed_projection,
        )
    except ImportError as exc:
        raise PrimaryProfitForwardShadowError(
            "dated P1 mixed validator is unavailable"
        ) from exc
    try:
        inputs = load_primary_inputs(repo_root, signal_date, "NATURAL")
        json_path = _safe_file(
            repo_root,
            MIXED_ROOT / f"projection_{signal_date}.json",
            label="dated P1 mixed projection",
        )
        csv_path = _safe_file(
            repo_root,
            MIXED_ROOT / f"projection_{signal_date}.csv",
            label="dated P1 mixed projection CSV",
        )
        projection = _read_json(json_path, label="dated P1 mixed projection")
        validate_mixed_projection(projection)
    except (OSError, ValueError, PrimaryProfitRankingError) as exc:
        raise PrimaryProfitForwardShadowError(
            "dated P1 mixed projection validation failed"
        ) from exc
    _expect(
        projection.get("signal_date") == signal_date
        and projection.get("generation_mode") == "NATURAL"
        and projection.get("prospective") is True
        and projection.get("retrospective_non_forward") is False
        and projection.get("source_bindings") == inputs.source_bindings
        and projection.get("candidate_count") == len(inputs.selected_runtime)
        and csv_path.read_bytes()
        == _primary_projection_csv_bytes(projection, MIXED_ROW_FIELDS),
        "dated P1 mixed projection/P0/CSV binding failed",
    )
    return {
        "projection": projection,
        "json_path": json_path,
        "csv_path": csv_path,
        "inputs": inputs,
    }


def _price_cap(row: Mapping[str, Any], *, runtime_sha256: str) -> tuple[float, str]:
    try:
        from top10decision.decision.observation import observation_price_contract
    except ImportError as exc:
        raise PrimaryProfitForwardShadowError(
            "DC20 observation price contract is unavailable"
        ) from exc
    contract = observation_price_contract(row)
    value = _finite(contract.get("observation_max_price"))
    _expect(value is not None and value > 0, "P0 runtime row has no D-only Shadow price cap")
    basis = {
        "formal_safe_cap": "D_FROZEN_RECOMMENDED_MAX_PRICE",
        "frozen_observation_cap": "D_FROZEN_OBSERVATION_MAX_PRICE",
        "model_diagnostic_cap": "D_ONLY_MODEL_DIAGNOSTIC_CAP",
        "legacy_d_close_cap": "D_CLOSE_CONSERVATIVE_CAP",
    }.get(str(contract.get("observation_price_basis") or ""))
    _expect(basis is not None, "P0 runtime row has unsupported Shadow price basis")
    _expect(SHA256_RE.fullmatch(runtime_sha256) is not None, "P0 runtime SHA is invalid")
    return round(value + 1e-9, 2), basis


def _shadow_rows_from_published_projection(
    published_rows: Sequence[Mapping[str, Any]],
    runtime: Any,
    *,
    runtime_sha256: str,
) -> list[dict[str, Any]]:
    """Bind every Shadow field to exact P1 rows and exact P0 D-only caps."""

    runtime_rows = {
        _normal_code(row.get("ts_code")): row
        for row in runtime.to_dict("records")
    }
    _expect(len(runtime_rows) == len(runtime), "P0 runtime row identity drifted")
    slot_count = min(2, len(published_rows))
    output_rows: list[dict[str, Any]] = []
    for position, projected in enumerate(published_rows, start=1):
        _expect(isinstance(projected, Mapping), "P1 mixed row is invalid")
        code = _normal_code(projected.get("ts_code"))
        _expect(code and code in runtime_rows, "P1 mixed row is absent from exact P0 runtime")
        source = runtime_rows[code]
        rank = projected.get("executable_profit_research_rank")
        promotion_rank = projected.get("promotion_rank")
        _expect(
            type(rank) is int
            and rank == position
            and type(promotion_rank) is int
            and int(float(source.get("promotion_rank"))) == promotion_rank
            and int(float(source.get("top10_selected"))) == 1
            and str(source.get("stage_transition") or "")
            == str(projected.get("stage_transition") or ""),
            "P1 mixed row changed frozen promotion identity/rank",
        )
        fill = _finite(projected.get("research_fill_proxy_score"))
        conditional = _finite(projected.get("research_conditional_profit_score"))
        joint = _finite(projected.get("research_joint_proxy_score"))
        promotion_probability = _finite(projected.get("predicted_promotion_probability"))
        _expect(
            all(
                value is not None and 0.0 <= value <= 1.0
                for value in (fill, conditional, joint, promotion_probability)
            )
            and math.isclose(
                float(joint),
                float(fill) * float(conditional),
                rel_tol=0.0,
                abs_tol=1e-15,
            ),
            "P1 mixed row proxy score identity drifted",
        )
        cap, cap_basis = _price_cap(source, runtime_sha256=runtime_sha256)
        output_rows.append(
            {
                "ts_code": code,
                "name": str(projected.get("name") or ""),
                "industry": str(projected.get("industry") or ""),
                "stage_transition": str(projected.get("stage_transition") or ""),
                "promotion_rank": promotion_rank,
                "predicted_promotion_probability": float(promotion_probability),
                "research_fill_proxy_score": float(fill),
                "research_conditional_profit_score": float(conditional),
                "research_joint_proxy_score": float(joint),
                "source_executable_profit_research_rank": position,
                "internal_shadow_order": position,
                "internal_shadow_selected": int(position <= slot_count),
                "shadow_slot": position if position <= slot_count else None,
                "shadow_max_price": cap,
                "shadow_price_basis": cap_basis,
                "shadow_price_source_sha256": runtime_sha256,
            }
        )
    return output_rows


def _identity_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(dict(payload))
    for key in ("selected_at_utc", "selection_identity_sha256", "snapshot_sha256", "downloads"):
        value.pop(key, None)
    return value


def _snapshot_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(dict(payload))
    value.pop("snapshot_sha256", None)
    value.pop("downloads", None)
    return value


def build_primary_profit_forward_shadow(
    repo_root: Path,
    signal_date: str,
    *,
    selected_at: datetime | None = None,
) -> dict[str, Any]:
    """Freeze P1 mixed Top1/Top2 without loading or re-running any model."""

    repo_root = repo_root.resolve(strict=True)
    signal_date = _normal_date(signal_date)
    _expect(signal_date, "P1 Shadow signal date must be YYYYMMDD")
    contract_path, _ = _load_contract(repo_root)
    bundle = _load_primary_bundle(repo_root, signal_date)
    inputs = bundle["inputs"]
    mixed = bundle["mixed"]
    projection = mixed["projection"]
    index = mixed["index"]
    _expect(
        projection.get("generation_mode") == "NATURAL"
        and projection.get("status") == "PROSPECTIVE_RESEARCH"
        and projection.get("prospective") is True
        and projection.get("retrospective_non_forward") is False,
        "only a prospective NATURAL P1 projection may create Shadow",
    )
    _expect(
        projection.get("research_only") is True
        and projection.get("boundaries", {}).get("proxy_scores_uncalibrated") is True
        and projection.get("boundaries", {}).get("may_create_trade_action") is False
        and projection.get("boundaries", {}).get("action_input_consumed") is False,
        "P1 projection crossed the research/no-Action boundary",
    )
    exec_date = _normal_date(projection.get("exec_date"))
    exit_date = _normal_date(projection.get("exit_date"))
    _expect(exec_date and exit_date, "P1 Shadow T/T+1 dates are invalid")
    calendar_path, open_dates = _open_dates(repo_root)
    _validate_adjacent_dates(open_dates, signal_date, exec_date, exit_date)

    current = selected_at or datetime.now(SHANGHAI)
    _expect(current.tzinfo is not None, "P1 Shadow selection clock must be timezone-aware")
    current = current.astimezone(SHANGHAI)
    start, end = _selection_window(signal_date, exec_date)
    _expect(start < current < end, "P1 Shadow selection is outside D-close/T-09:20 window")

    rows = projection.get("rows")
    _expect(isinstance(rows, list) and 0 <= len(rows) <= 10, "P1 mixed rows are invalid")
    _expect(projection.get("candidate_count") == len(rows), "P1 mixed candidate count drifted")
    if len(rows) >= 3:
        _expect(
            float(rows[1]["research_joint_proxy_score"])
            != float(rows[2]["research_joint_proxy_score"]),
            "exact P1 Top2/Top3 joint proxy tie is not selectable",
        )

    runtime = inputs.full_runtime
    runtime_binding = projection["source_bindings"]["runtime_features"]
    runtime_sha = str(runtime_binding["sha256"])
    generated_at_utc: str | None = None
    if rows:
        generated_values = runtime["generated_at_utc"].fillna("").astype(str).unique().tolist()
        _expect(len(generated_values) == 1 and generated_values[0], "P0 runtime generation time drifted")
        generated = _parse_aware(
            generated_values[0], label="P0 runtime generated_at_utc"
        ).astimezone(SHANGHAI)
        _expect(start < generated <= current < end, "P0 runtime escaped the forward selection window")
        generated_at_utc = generated.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")

    model = projection.get("model")
    _expect(isinstance(model, Mapping), "P1 mixed model disclosure is missing")
    maximum_truth = _normal_date(model.get("maximum_used_scheduled_exit_date"))
    maximum_prior = _normal_date(model.get("lagged_prior_max_history_exit_date"))
    availability_is_valid = bool(
        maximum_truth
        and maximum_truth < signal_date
        and maximum_prior
        and maximum_prior < signal_date
    )
    if not rows:
        availability_is_valid = (
            not maximum_truth
            and not maximum_prior
            and model.get("model_loaded") is False
            and model.get("empty_event_reason") == "P0_FROZEN_TOPN_EMPTY"
        )
    calibration_disclosure_is_valid = (
        model.get("calibrated_probability_output") is False
        if rows
        else model.get("calibrated_probability_output") in {False, None}
    )
    _expect(
        model.get("artifact_sha256") == EXPECTED_MODEL_SHA256
        and model.get("feature_columns_sha256") == EXPECTED_FEATURE_COLUMNS_SHA256
        and model.get("feature_count") == 156
        and calibration_disclosure_is_valid
        and availability_is_valid,
        "P1 mixed model/training availability boundary drifted",
    )
    model_path = _safe_file(repo_root, MODEL_PATH, label="sealed mixed-profit model")
    _expect(_sha256(model_path) == EXPECTED_MODEL_SHA256, "sealed mixed-profit model SHA drifted")

    output_rows = _shadow_rows_from_published_projection(
        rows,
        runtime,
        runtime_sha256=runtime_sha,
    )
    slot_count = min(2, len(output_rows))

    mixed_json_path = Path(mixed["json_path"])
    mixed_csv_path = Path(mixed["csv_path"])
    mixed_index_path = Path(mixed["index_path"])
    original_bindings = projection["source_bindings"]
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": ARTIFACT_KIND,
        "contract_id": CONTRACT_ID,
        "status": STATUS,
        "research_only": True,
        "proxy_scores_uncalibrated": True,
        "score_semantics": {
            "research_fill_proxy_score": "historical daily-bar buyability proxy; not actual fill probability",
            "research_conditional_profit_score": "uncalibrated conditional profit research score",
            "research_joint_proxy_score": "exact product of the two uncalibrated proxy scores",
        },
        "signal_date": signal_date,
        "exec_date": exec_date,
        "exit_date": exit_date,
        "feature_as_of_date": signal_date,
        "selected_at_utc": current.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
        "source_generated_at_utc": generated_at_utc,
        "top10_count": len(output_rows),
        "top10_members_sha256": str(projection["top10_members_sha256"]),
        "source_bindings": {
            "bridge_contract": {
                "path": CONTRACT_PATH.as_posix(),
                "sha256": _sha256(contract_path),
                "contract_id": CONTRACT_ID,
            },
            "mixed_projection": {
                "path": mixed_json_path.relative_to(repo_root).as_posix(),
                "sha256": _sha256(mixed_json_path),
                "snapshot_sha256": str(projection["snapshot_sha256"]),
                "csv_path": mixed_csv_path.relative_to(repo_root).as_posix(),
                "csv_sha256": _sha256(mixed_csv_path),
                "generation_mode": "NATURAL",
            },
            "mixed_index": {
                "path": mixed_index_path.relative_to(repo_root).as_posix(),
                "sha256": _sha256(mixed_index_path),
                "role": "MUTABLE_POINTER_OBSERVED_AT_FREEZE_AUDIT_ONLY",
                "historical_revalidation_required": False,
            },
            "primary_receipt": copy.deepcopy(original_bindings["primary_receipt"]),
            "runtime_features": copy.deepcopy(runtime_binding),
            "three_rank": copy.deepcopy(original_bindings["three_rank"]),
            "calendar": {
                "path": CALENDAR_PATH.as_posix(),
                "sha256": _sha256(calendar_path),
                "exchange": "SSE",
            },
            "model": {
                "path": MODEL_PATH.as_posix(),
                "artifact_sha256": EXPECTED_MODEL_SHA256,
                "feature_columns_sha256": EXPECTED_FEATURE_COLUMNS_SHA256,
                "feature_count": 156,
                "maximum_used_scheduled_exit_date": maximum_truth or None,
                "lagged_prior_max_history_exit_date": maximum_prior or None,
                "calibrated_probability_output": False,
                "inference_performed_by_bridge": False,
            },
        },
        "ranking_contract": copy.deepcopy(RANKING_CONTRACT),
        "boundaries": copy.deepcopy(BOUNDARIES),
        "rows": output_rows,
        "shadow_top2": {
            "status": "NO_HARD_SCOPE_CANDIDATES" if not output_rows else "FROZEN_INTERNAL_RESEARCH_ONLY",
            "requested_slots": 2,
            "actual_slots": slot_count,
            "rows": [
                {
                    "shadow_slot": row["shadow_slot"],
                    "ts_code": row["ts_code"],
                    "name": row["name"],
                    "promotion_rank": row["promotion_rank"],
                    "research_fill_proxy_score": row["research_fill_proxy_score"],
                    "research_conditional_profit_score": row["research_conditional_profit_score"],
                    "research_joint_proxy_score": row["research_joint_proxy_score"],
                    "shadow_max_price": row["shadow_max_price"],
                    "shadow_price_basis": row["shadow_price_basis"],
                    "shadow_price_source_sha256": row["shadow_price_source_sha256"],
                }
                for row in output_rows[:slot_count]
            ],
        },
    }
    payload["selection_identity_sha256"] = _canonical_sha256(_identity_payload(payload))
    payload["snapshot_sha256"] = _canonical_sha256(_snapshot_payload(payload))
    validate_primary_profit_forward_shadow(payload, repo_root=repo_root)
    return payload


def validate_primary_profit_forward_shadow(
    payload: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
    require_downloads: bool = False,
) -> None:
    expected_keys = {
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
        "selected_at_utc",
        "source_generated_at_utc",
        "top10_count",
        "top10_members_sha256",
        "source_bindings",
        "ranking_contract",
        "boundaries",
        "rows",
        "shadow_top2",
        "selection_identity_sha256",
        "snapshot_sha256",
    }
    if require_downloads:
        expected_keys.add("downloads")
    _expect(set(payload) == expected_keys, "P1 Shadow payload surface drifted")
    _expect(
        payload.get("schema_version") == SCHEMA_VERSION
        and payload.get("artifact_kind") == ARTIFACT_KIND
        and payload.get("contract_id") == CONTRACT_ID
        and payload.get("status") == STATUS
        and payload.get("research_only") is True
        and payload.get("proxy_scores_uncalibrated") is True,
        "P1 Shadow payload identity drifted",
    )
    signal_date = _normal_date(payload.get("signal_date"))
    exec_date = _normal_date(payload.get("exec_date"))
    exit_date = _normal_date(payload.get("exit_date"))
    _expect(
        signal_date
        and exec_date
        and exit_date
        and payload.get("feature_as_of_date") == signal_date,
        "P1 Shadow dates are invalid",
    )
    selected_at = _parse_aware(payload.get("selected_at_utc"), label="P1 Shadow selected_at_utc").astimezone(SHANGHAI)
    start, end = _selection_window(signal_date, exec_date)
    _expect(start < selected_at < end, "P1 Shadow selected_at escaped selection window")
    rows = payload.get("rows")
    _expect(
        isinstance(rows, list)
        and 0 <= len(rows) <= 10
        and payload.get("top10_count") == len(rows)
        and SHA256_RE.fullmatch(str(payload.get("top10_members_sha256") or "")) is not None,
        "P1 Shadow row count/fingerprint invalid",
    )
    source_generated = payload.get("source_generated_at_utc")
    if rows:
        generated = _parse_aware(source_generated, label="P1 Shadow source_generated_at_utc").astimezone(SHANGHAI)
        _expect(start < generated <= selected_at, "P1 Shadow source was not frozen before selection")
    else:
        _expect(source_generated is None, "empty P1 Shadow event has a source row time")
    _expect(payload.get("ranking_contract") == RANKING_CONTRACT, "P1 Shadow ranking contract drifted")
    _expect(payload.get("boundaries") == BOUNDARIES, "P1 Shadow boundaries drifted")

    sources = payload.get("source_bindings")
    source_keys = {
        "bridge_contract",
        "mixed_projection",
        "mixed_index",
        "primary_receipt",
        "runtime_features",
        "three_rank",
        "calendar",
        "model",
    }
    _expect(isinstance(sources, Mapping) and set(sources) == source_keys, "P1 Shadow source binding surface drifted")
    runtime_source = sources["runtime_features"]
    projection_source = sources["mixed_projection"]
    mixed_index_source = sources["mixed_index"]
    model_source = sources["model"]
    _expect(
        isinstance(runtime_source, Mapping)
        and runtime_source.get("path") == f"outputs/decision/primary_d_runtime_features_{signal_date}.csv"
        and runtime_source.get("selected_count") == len(rows)
        and SHA256_RE.fullmatch(str(runtime_source.get("sha256") or "")) is not None,
        "P1 Shadow runtime binding invalid",
    )
    _expect(
        isinstance(projection_source, Mapping)
        and projection_source.get("path") == f"outputs/decision/executable_profit_research/projection_{signal_date}.json"
        and projection_source.get("csv_path") == f"outputs/decision/executable_profit_research/projection_{signal_date}.csv"
        and projection_source.get("generation_mode") == "NATURAL"
        and all(
            SHA256_RE.fullmatch(str(projection_source.get(key) or "")) is not None
            for key in ("sha256", "snapshot_sha256", "csv_sha256")
        ),
        "P1 Shadow mixed projection binding invalid",
    )
    _expect(
        isinstance(mixed_index_source, Mapping)
        and mixed_index_source.get("path")
        == "outputs/decision/executable_profit_research/index.json"
        and mixed_index_source.get("role")
        == "MUTABLE_POINTER_OBSERVED_AT_FREEZE_AUDIT_ONLY"
        and mixed_index_source.get("historical_revalidation_required") is False
        and SHA256_RE.fullmatch(str(mixed_index_source.get("sha256") or ""))
        is not None,
        "P1 Shadow mixed pointer audit binding invalid",
    )
    maximum_truth = _normal_date(
        model_source.get("maximum_used_scheduled_exit_date")
    )
    maximum_prior = _normal_date(
        model_source.get("lagged_prior_max_history_exit_date")
    )
    model_availability_is_valid = bool(
        maximum_truth
        and maximum_truth < signal_date
        and maximum_prior
        and maximum_prior < signal_date
    )
    if not rows:
        model_availability_is_valid = not maximum_truth and not maximum_prior
    _expect(
        isinstance(model_source, Mapping)
        and model_source.get("path") == MODEL_PATH.as_posix()
        and model_source.get("artifact_sha256") == EXPECTED_MODEL_SHA256
        and model_source.get("feature_columns_sha256") == EXPECTED_FEATURE_COLUMNS_SHA256
        and model_source.get("feature_count") == 156
        and model_source.get("calibrated_probability_output") is False
        and model_source.get("inference_performed_by_bridge") is False
        and model_availability_is_valid,
        "P1 Shadow model binding invalid",
    )

    expected_orders = list(range(1, len(rows) + 1))
    _expect(
        [row.get("internal_shadow_order") for row in rows] == expected_orders,
        "P1 Shadow rows are not in published P1 order",
    )
    slot_count = min(2, len(rows))
    promotion_ranks: list[int] = []
    for position, row in enumerate(rows, start=1):
        _expect(isinstance(row, Mapping) and set(row) == set(ROW_FIELDS), "P1 Shadow row surface drifted")
        code = _normal_code(row.get("ts_code"))
        promotion_rank = row.get("promotion_rank")
        _expect(
            code
            and row.get("stage_transition") in {"2→3", "3→4"}
            and type(promotion_rank) is int
            and row.get("source_executable_profit_research_rank") == position
            and row.get("internal_shadow_selected") == int(position <= slot_count)
            and row.get("shadow_slot") == (position if position <= slot_count else None),
            "P1 Shadow row identity/rank/slot invalid",
        )
        promotion_ranks.append(promotion_rank)
        fill = _finite(row.get("research_fill_proxy_score"))
        conditional = _finite(row.get("research_conditional_profit_score"))
        joint = _finite(row.get("research_joint_proxy_score"))
        promotion_probability = _finite(row.get("predicted_promotion_probability"))
        _expect(
            all(value is not None and 0 <= value <= 1 for value in (fill, conditional, joint, promotion_probability))
            and math.isclose(float(joint), float(fill) * float(conditional), rel_tol=0.0, abs_tol=1e-15),
            "P1 Shadow row score invalid",
        )
        cap = _finite(row.get("shadow_max_price"))
        _expect(
            cap is not None
            and cap > 0
            and math.isclose(cap * 100, round(cap * 100), rel_tol=0.0, abs_tol=1e-7)
            and row.get("shadow_price_basis")
            in {
                "D_FROZEN_RECOMMENDED_MAX_PRICE",
                "D_FROZEN_OBSERVATION_MAX_PRICE",
                "D_ONLY_MODEL_DIAGNOSTIC_CAP",
                "D_CLOSE_CONSERVATIVE_CAP",
            }
            and row.get("shadow_price_source_sha256") == runtime_source.get("sha256"),
            "P1 Shadow D-only price cap binding invalid",
        )
    _expect(sorted(promotion_ranks) == expected_orders, "P1 Shadow changed promotion ranks")
    expected_order = sorted(
        rows,
        key=lambda row: (
            -float(row["research_joint_proxy_score"]),
            -float(row["research_conditional_profit_score"]),
            -float(row["research_fill_proxy_score"]),
            str(row["ts_code"]),
        ),
    )
    _expect([row["ts_code"] for row in rows] == [row["ts_code"] for row in expected_order], "P1 Shadow ranking differs from mixed projection semantics")
    if len(rows) >= 3:
        _expect(float(rows[1]["research_joint_proxy_score"]) != float(rows[2]["research_joint_proxy_score"]), "exact P1 Top2/Top3 joint proxy tie is not selectable")

    expected_top2 = [
        {
            "shadow_slot": row["shadow_slot"],
            "ts_code": row["ts_code"],
            "name": row["name"],
            "promotion_rank": row["promotion_rank"],
            "research_fill_proxy_score": row["research_fill_proxy_score"],
            "research_conditional_profit_score": row["research_conditional_profit_score"],
            "research_joint_proxy_score": row["research_joint_proxy_score"],
            "shadow_max_price": row["shadow_max_price"],
            "shadow_price_basis": row["shadow_price_basis"],
            "shadow_price_source_sha256": row["shadow_price_source_sha256"],
        }
        for row in rows[:slot_count]
    ]
    top2 = payload.get("shadow_top2")
    _expect(
        isinstance(top2, Mapping)
        and top2.get("status") == ("NO_HARD_SCOPE_CANDIDATES" if not rows else "FROZEN_INTERNAL_RESEARCH_ONLY")
        and top2.get("requested_slots") == 2
        and top2.get("actual_slots") == slot_count
        and top2.get("rows") == expected_top2,
        "P1 Shadow Top1/Top2 projection drifted",
    )
    _expect(
        payload.get("selection_identity_sha256") == _canonical_sha256(_identity_payload(payload))
        and payload.get("snapshot_sha256") == _canonical_sha256(_snapshot_payload(payload)),
        "P1 Shadow identity/snapshot SHA drifted",
    )
    downloads = payload.get("downloads")
    if require_downloads:
        prefix = f"{OUTPUT_ROOT.as_posix()}/shadow_{signal_date}"
        _expect(
            isinstance(downloads, Mapping)
            and downloads.get("json_url") == f"{prefix}.json"
            and downloads.get("csv_url") == f"{prefix}.csv"
            and downloads.get("row_count") == len(rows)
            and SHA256_RE.fullmatch(str(downloads.get("csv_sha256") or "")) is not None,
            "P1 Shadow downloads binding invalid",
        )

    if repo_root is not None:
        repo_root = repo_root.resolve(strict=True)
        _load_contract(repo_root)
        calendar_path, open_dates = _open_dates(repo_root)
        _validate_adjacent_dates(open_dates, signal_date, exec_date, exit_date)
        checks = (
            (sources["bridge_contract"], "path", "sha256", "bridge contract"),
            (sources["mixed_projection"], "path", "sha256", "mixed projection"),
            (sources["mixed_projection"], "csv_path", "csv_sha256", "mixed projection CSV"),
            (sources["primary_receipt"], "path", "sha256", "P0 receipt"),
            (sources["runtime_features"], "path", "sha256", "P0 runtime"),
            (sources["three_rank"], "json_path", "json_sha256", "P0 three-rank JSON"),
            (sources["three_rank"], "csv_path", "csv_sha256", "P0 three-rank CSV"),
        )
        for binding, path_key, sha_key, label in checks:
            _expect(isinstance(binding, Mapping), f"{label} binding missing")
            path = _safe_file(repo_root, Path(str(binding.get(path_key) or "")), label=label)
            _expect(_sha256(path) == binding.get(sha_key), f"{label} SHA drifted")
        _expect(_sha256(calendar_path) == sources["calendar"].get("sha256"), "calendar binding SHA drifted")
        model_path = _safe_file(repo_root, Path(str(model_source["path"])), label="sealed model")
        _expect(_sha256(model_path) == model_source["artifact_sha256"], "sealed model binding SHA drifted")
        dated = _load_dated_primary_mixed_projection(repo_root, signal_date)
        published = dated["projection"]
        published_rows = published.get("rows", [])
        _expect(isinstance(published_rows, list), "published P1 rows are invalid")
        expected_rows = _shadow_rows_from_published_projection(
            published_rows,
            dated["inputs"].full_runtime,
            runtime_sha256=str(runtime_source.get("sha256") or ""),
        )
        _expect(
            _sha256(dated["json_path"]) == projection_source.get("sha256")
            and _sha256(dated["csv_path"]) == projection_source.get("csv_sha256")
            and published.get("snapshot_sha256") == projection_source.get("snapshot_sha256")
            and published.get("top10_members_sha256") == payload.get("top10_members_sha256")
            and rows == expected_rows,
            "materialized Shadow no longer matches published P1 mixed projection",
        )


def _csv_bytes(payload: Mapping[str, Any]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(ROW_FIELDS), lineterminator="\n")
    writer.writeheader()
    writer.writerows(payload.get("rows") or [])
    return buffer.getvalue().encode("utf-8")


def _index_payload(payload: Mapping[str, Any], *, json_sha256: str) -> dict[str, Any]:
    signal_date = str(payload["signal_date"])
    prefix = f"{OUTPUT_ROOT.as_posix()}/shadow_{signal_date}"
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "index_kind": INDEX_KIND,
        "data_alias": False,
        "latest_signal_date": signal_date,
        "latest_exec_date": payload["exec_date"],
        "latest_exit_date": payload["exit_date"],
        "latest_status": payload["status"],
        "latest_json_url": f"{prefix}.json",
        "latest_csv_url": f"{prefix}.csv",
        "latest_json_sha256": json_sha256,
        "latest_csv_sha256": payload["downloads"]["csv_sha256"],
        "latest_snapshot_sha256": payload["snapshot_sha256"],
        "latest_selection_identity_sha256": payload["selection_identity_sha256"],
        "latest_top10_members_sha256": payload["top10_members_sha256"],
        "boundaries": copy.deepcopy(BOUNDARIES),
    }


def validate_primary_profit_forward_shadow_index(index: Mapping[str, Any]) -> None:
    _expect(
        index.get("schema_version") == INDEX_SCHEMA_VERSION
        and index.get("index_kind") == INDEX_KIND
        and index.get("data_alias") is False
        and index.get("latest_status") == STATUS
        and _normal_date(index.get("latest_signal_date"))
        and _normal_date(index.get("latest_exec_date"))
        and _normal_date(index.get("latest_exit_date"))
        and index.get("boundaries") == BOUNDARIES,
        "P1 Shadow index identity drifted",
    )
    signal_date = str(index["latest_signal_date"])
    prefix = f"{OUTPUT_ROOT.as_posix()}/shadow_{signal_date}"
    _expect(
        index.get("latest_json_url") == f"{prefix}.json"
        and index.get("latest_csv_url") == f"{prefix}.csv",
        "P1 Shadow index path drifted",
    )
    for key in (
        "latest_json_sha256",
        "latest_csv_sha256",
        "latest_snapshot_sha256",
        "latest_selection_identity_sha256",
        "latest_top10_members_sha256",
    ):
        _expect(SHA256_RE.fullmatch(str(index.get(key) or "")) is not None, f"P1 Shadow index {key} invalid")


@contextmanager
def _lock(output: Path):
    path = output / ".primary-profit-shadow.lock"
    with path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _install_new(path: Path, payload: bytes) -> bool:
    if path.exists():
        _expect(path.is_file() and not path.is_symlink(), f"existing artifact is unsafe: {path}")
        _expect(path.read_bytes() == payload, f"immutable artifact rewrite rejected: {path.name}")
        return False
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return True


def _atomic_pointer(path: Path, payload: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def materialize_primary_profit_forward_shadow(
    repo_root: Path,
    payload: Mapping[str, Any],
    *,
    _now: datetime | None = None,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    repo_root = repo_root.resolve(strict=True)
    validate_primary_profit_forward_shadow(payload, repo_root=repo_root)
    enriched = copy.deepcopy(dict(payload))
    signal_date = str(enriched["signal_date"])
    csv_payload = _csv_bytes(enriched)
    enriched["downloads"] = {
        "json_url": f"{OUTPUT_ROOT.as_posix()}/shadow_{signal_date}.json",
        "csv_url": f"{OUTPUT_ROOT.as_posix()}/shadow_{signal_date}.csv",
        "csv_sha256": _sha256_bytes(csv_payload),
        "row_count": len(enriched["rows"]),
    }
    validate_primary_profit_forward_shadow(enriched, repo_root=repo_root, require_downloads=True)
    json_payload = _pretty_json_bytes(enriched)
    index = _index_payload(enriched, json_sha256=_sha256_bytes(json_payload))
    validate_primary_profit_forward_shadow_index(index)
    output = _safe_output_directory(repo_root)
    json_path = output / f"shadow_{signal_date}.json"
    csv_path = output / f"shadow_{signal_date}.csv"
    index_path = repo_root / PRIMARY_INDEX_PATH
    with _lock(output):
        existing_index: dict[str, Any] | None = None
        if index_path.exists():
            existing_index = _read_json(index_path, label="existing Shadow pointer")
            validate_primary_profit_forward_shadow_index(existing_index)
            existing_date = _normal_date(existing_index.get("latest_signal_date"))
            _expect(existing_date and existing_date <= signal_date, "out-of-order P1 Shadow backfill rejected")
            if existing_date == signal_date:
                _expect(
                    existing_index.get("latest_selection_identity_sha256")
                    == enriched["selection_identity_sha256"],
                    "same-date P1 Shadow selection identity rewrite rejected",
                )
                existing_payload = _read_json(json_path, label="existing P1 Shadow selection")
                validate_primary_profit_forward_shadow(existing_payload, repo_root=repo_root, require_downloads=True)
                _expect(existing_payload["selection_identity_sha256"] == enriched["selection_identity_sha256"], "same-date P1 Shadow payload rewrite rejected")
                return json_path, csv_path, index_path, existing_index

        current = (_now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        start, end = _selection_window(signal_date, str(enriched["exec_date"]))
        selected_at = _parse_aware(enriched["selected_at_utc"], label="P1 Shadow selected_at_utc").astimezone(SHANGHAI)
        _expect(start < selected_at <= current < end, "new P1 Shadow materialization escaped D-close/T-09:20")
        created_json = _install_new(json_path, json_payload)
        try:
            _install_new(csv_path, csv_payload)
        except Exception:
            if created_json:
                json_path.unlink(missing_ok=True)
            raise
        _atomic_pointer(index_path, _pretty_json_bytes(index))
    return json_path, csv_path, index_path, index


def _rebuild_forward_statistics(repo_root: Path, *, as_of_date: str) -> tuple[Path, dict[str, Any]]:
    try:
        from top10decision.decision.executable_profit_shadow_settlement import (
            ExecutableProfitSettlementError,
            build_statistics,
            materialize_statistics,
            validate_statistics,
        )
    except ImportError as exc:
        raise PrimaryProfitForwardShadowError("Shadow statistics runtime is unavailable") from exc
    try:
        summary = build_statistics(repo_root, as_of_date=as_of_date)
        validate_statistics(summary)
        path = materialize_statistics(repo_root, summary)
    except (OSError, ValueError, ExecutableProfitSettlementError) as exc:
        raise PrimaryProfitForwardShadowError("P1 Shadow statistics rebuild failed") from exc
    _expect(
        path.resolve(strict=True) == (repo_root / STATISTICS_PATH).resolve(strict=True)
        and summary.get("as_of_date") == as_of_date,
        "P1 Shadow statistics path/as-of drifted",
    )
    return path, summary


def _public_state_snapshot(payload: Mapping[str, Any]) -> str:
    value = copy.deepcopy(dict(payload))
    value.pop("snapshot_sha256", None)
    return _canonical_sha256(value)


def _strict_as_of_date(
    repo_root: Path,
    *,
    signal_date: str,
    as_of_date: str,
) -> str:
    as_of_date = _normal_date(as_of_date)
    _expect(as_of_date, "P1 Shadow public as-of date must be YYYYMMDD")
    _, open_dates = _open_dates(repo_root)
    _expect(
        signal_date in open_dates
        and as_of_date in open_dates
        and as_of_date >= signal_date,
        "P1 Shadow public as-of date is not an SSE session on/after D",
    )
    return as_of_date


def _truth_binding(repo_root: Path, path: Path) -> dict[str, str]:
    _expect(path.is_relative_to(repo_root), "P1 Shadow truth path escaped repository")
    return {
        "path": path.relative_to(repo_root).as_posix(),
        "sha256": _sha256(path),
    }


def _selected_rows_for_public_state(
    repo_root: Path,
    *,
    selection: Mapping[str, Any],
    as_of_date: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    signal_date = str(selection["signal_date"])
    exec_date = str(selection["exec_date"])
    exit_date = str(selection["exit_date"])
    selected = [
        dict(row)
        for row in selection["rows"]
        if row.get("internal_shadow_selected") == 1
    ]
    selected.sort(key=lambda row: int(row["shadow_slot"]))
    selection_path = repo_root / OUTPUT_ROOT / f"shadow_{signal_date}.json"
    _expect(
        selection_path.is_file() and not selection_path.is_symlink(),
        "P1 Shadow public state has no immutable exact-D selection",
    )
    expected_selection_binding = {
        "path": (OUTPUT_ROOT / f"shadow_{signal_date}.json").as_posix(),
        "file_sha256": _sha256(selection_path),
        "snapshot_sha256": str(selection["snapshot_sha256"]),
        "top10_members_sha256": str(selection["top10_members_sha256"]),
        "selected_slots": len(selected),
        "selected_members": [
            {
                "shadow_slot": int(row["shadow_slot"]),
                "ts_code": str(row["ts_code"]),
            }
            for row in selected
        ],
    }
    verification_rows: dict[int, Mapping[str, Any]] = {}
    settlement_rows: dict[int, Mapping[str, Any]] = {}
    verification_binding: dict[str, str] | None = None
    settlement_binding: dict[str, str] | None = None

    if as_of_date >= exec_date:
        verification_path = repo_root / VERIFICATION_ROOT / f"t_verification_{signal_date}.json"
        if verification_path.exists():
            _expect(
                verification_path.is_file() and not verification_path.is_symlink(),
                "P1 Shadow T verification path is unsafe",
            )
            verification = _read_json(
                verification_path,
                label="P1 Shadow T verification",
            )
            try:
                from top10decision.decision.executable_profit_shadow_settlement import (
                    validate_t_verification,
                )
                validate_t_verification(verification)
            except (ImportError, RuntimeError, ValueError) as exc:
                raise PrimaryProfitForwardShadowError(
                    "P1 Shadow T verification is invalid"
                ) from exc
            _expect(
                verification.get("signal_date") == signal_date
                and verification.get("exec_date") == exec_date
                and verification.get("exit_date") == exit_date
                and verification.get("selection") == expected_selection_binding,
                "P1 Shadow T verification date binding drifted",
            )
            verification_rows = {
                int(row["shadow_slot"]): row for row in verification["rows"]
            }
            verification_binding = _truth_binding(repo_root, verification_path)

        settlement_path = repo_root / SETTLEMENT_ROOT / f"settlement_{signal_date}.json"
        if settlement_path.exists():
            _expect(
                verification_binding is not None,
                "P1 Shadow settlement exists without immutable T verification",
            )
            _expect(
                settlement_path.is_file() and not settlement_path.is_symlink(),
                "P1 Shadow T+1 settlement path is unsafe",
            )
            settlement = _read_json(
                settlement_path,
                label="P1 Shadow T+1 settlement",
            )
            try:
                from top10decision.decision.executable_profit_shadow_settlement import (
                    validate_t1_settlement,
                )
                validate_t1_settlement(settlement)
            except (ImportError, RuntimeError, ValueError) as exc:
                raise PrimaryProfitForwardShadowError(
                    "P1 Shadow T+1 settlement is invalid"
                ) from exc
            _expect(
                settlement.get("signal_date") == signal_date
                and settlement.get("exec_date") == exec_date
                and settlement.get("exit_date") == exit_date
                and settlement.get("selection") == expected_selection_binding
                and settlement.get("t_verification", {}).get("file_sha256")
                == verification_binding["sha256"],
                "P1 Shadow T+1 settlement date binding drifted",
            )
            settlement_rows = {
                int(row["shadow_slot"]): row for row in settlement["rows"]
            }
            for row in settlement_rows.values():
                actual_exit_date = _normal_date(row.get("actual_exit_date"))
                _expect(
                    row.get("proxy_fill") == 0
                    or (actual_exit_date and actual_exit_date <= as_of_date),
                    "P1 Shadow public state leaked future T+1 truth",
                )
            settlement_binding = _truth_binding(repo_root, settlement_path)

    output: list[dict[str, Any]] = []
    for frozen in selected:
        slot = int(frozen["shadow_slot"])
        verified = verification_rows.get(slot)
        settled = settlement_rows.get(slot)
        if verified is None:
            t_status = (
                "PENDING_T_NOT_REACHED"
                if as_of_date < exec_date
                else "PENDING_T_TRUTH"
            )
            t_truth_state = None
            proxy_fill = None
        else:
            t_status = str(verified["validation_status"])
            t_truth_state = str(verified["truth_state"])
            proxy_fill = int(verified["proxy_fill"])
        if settled is not None:
            t1_status = str(settled["settlement_status"])
        elif verified is None:
            t1_status = (
                "PENDING_T1_NOT_REACHED"
                if as_of_date < exec_date
                else "PENDING_T_VERIFICATION"
            )
        elif as_of_date < exit_date and proxy_fill == 1:
            t1_status = "PENDING_T1_NOT_REACHED"
        else:
            t1_status = "PENDING_T1_TRUTH"
        output.append(
            {
                "shadow_slot": slot,
                "ts_code": str(frozen["ts_code"]),
                "name": str(frozen["name"]),
                "promotion_rank": int(frozen["promotion_rank"]),
                "t_status": t_status,
                "t_truth_state": t_truth_state,
                "proxy_fill": proxy_fill,
                "t1_status": t1_status,
                "scheduled_exit_date": (
                    settled.get("scheduled_exit_date") if settled is not None else exit_date
                ),
                "actual_exit_date": (
                    settled.get("actual_exit_date") if settled is not None else None
                ),
                "net_return_after_cost": (
                    settled.get("net_return_after_cost") if settled is not None else None
                ),
                "strategy_slot_return": (
                    settled.get("strategy_slot_return") if settled is not None else None
                ),
            }
        )
    return output, {
        "t_verification": verification_binding,
        "t1_settlement": settlement_binding,
    }


def build_primary_profit_shadow_public_state(
    repo_root: Path,
    *,
    selection_path: Path,
    selection: Mapping[str, Any],
    summary_path: Path,
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    repo_root = repo_root.resolve(strict=True)
    validate_primary_profit_forward_shadow(
        selection,
        repo_root=repo_root,
        require_downloads=True,
    )
    signal_date = str(selection["signal_date"])
    as_of_date = _strict_as_of_date(
        repo_root,
        signal_date=signal_date,
        as_of_date=str(summary.get("as_of_date") or ""),
    )
    summary_snapshot = str(summary.get("snapshot_sha256") or "")
    _expect(SHA256_RE.fullmatch(summary_snapshot) is not None, "Shadow summary snapshot is invalid")
    selected_rows, truth_bindings = _selected_rows_for_public_state(
        repo_root,
        selection=selection,
        as_of_date=as_of_date,
    )
    source_mixed = selection["source_bindings"]["mixed_projection"]
    state: dict[str, Any] = {
        "schema_version": PUBLIC_STATE_SCHEMA,
        "artifact_kind": "immutable_primary_profit_forward_shadow_public_state",
        "status": STATUS,
        "signal_date": signal_date,
        "exec_date": selection["exec_date"],
        "exit_date": selection["exit_date"],
        "as_of_date": as_of_date,
        "candidate_count": selection["top10_count"],
        "selected_slots": selection["shadow_top2"]["actual_slots"],
        "top10_members_sha256": selection["top10_members_sha256"],
        "source_bindings": {
            "mixed_projection": copy.deepcopy(source_mixed),
            "selection": {
                "path": selection_path.relative_to(repo_root).as_posix(),
                "sha256": _sha256(selection_path),
                "snapshot_sha256": selection["snapshot_sha256"],
                "selection_identity_sha256": selection["selection_identity_sha256"],
            },
            "statistics": {
                "path": summary_path.relative_to(repo_root).as_posix(),
                "sha256": _sha256(summary_path),
                "snapshot_sha256": summary_snapshot,
                "as_of_date": as_of_date,
            },
            "t_verification": truth_bindings["t_verification"],
            "t1_settlement": truth_bindings["t1_settlement"],
        },
        "latest_selected_rows": selected_rows,
        "cohorts": copy.deepcopy(summary.get("cohorts")),
        "forward_signal_date_progress_180": copy.deepcopy(
            summary.get("forward_signal_date_progress_180")
        ),
        "probability_diagnostics": copy.deepcopy(summary.get("probability_diagnostics")),
        "boundaries": copy.deepcopy(BOUNDARIES),
    }
    _expect(isinstance(state["cohorts"], Mapping), "Shadow public cohorts missing")
    _expect(
        isinstance(state["forward_signal_date_progress_180"], Mapping),
        "Shadow public progress missing",
    )
    state["snapshot_sha256"] = _public_state_snapshot(state)
    validate_primary_profit_forward_shadow_public_state(state)
    return state


def validate_primary_profit_forward_shadow_public_state(
    payload: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> None:
    expected_keys = {
        "schema_version",
        "artifact_kind",
        "status",
        "signal_date",
        "exec_date",
        "exit_date",
        "as_of_date",
        "candidate_count",
        "selected_slots",
        "top10_members_sha256",
        "source_bindings",
        "latest_selected_rows",
        "cohorts",
        "forward_signal_date_progress_180",
        "probability_diagnostics",
        "boundaries",
        "snapshot_sha256",
    }
    _expect(set(payload) == expected_keys, "public P1 Shadow state surface drifted")
    signal_date = _normal_date(payload.get("signal_date"))
    exec_date = _normal_date(payload.get("exec_date"))
    exit_date = _normal_date(payload.get("exit_date"))
    as_of_date = _normal_date(payload.get("as_of_date"))
    _expect(
        payload.get("schema_version") == PUBLIC_STATE_SCHEMA
        and payload.get("artifact_kind")
        == "immutable_primary_profit_forward_shadow_public_state"
        and payload.get("status") == STATUS
        and signal_date
        and exec_date
        and exit_date
        and as_of_date >= signal_date
        and payload.get("boundaries") == BOUNDARIES
        and SHA256_RE.fullmatch(str(payload.get("top10_members_sha256") or ""))
        is not None,
        "public P1 Shadow state identity drifted",
    )
    rows = payload.get("latest_selected_rows")
    selected_slots = payload.get("selected_slots")
    candidate_count = payload.get("candidate_count")
    _expect(
        type(candidate_count) is int
        and 0 <= candidate_count <= 10
        and type(selected_slots) is int
        and selected_slots == min(2, candidate_count)
        and isinstance(rows, list)
        and len(rows) == selected_slots,
        "public P1 Shadow state counts drifted",
    )
    for position, row in enumerate(rows, start=1):
        _expect(
            isinstance(row, Mapping)
            and set(row) == set(PUBLIC_STATE_ROW_FIELDS)
            and row.get("shadow_slot") == position
            and _normal_code(row.get("ts_code"))
            and str(row.get("name") or "").strip()
            and type(row.get("promotion_rank")) is int
            and str(row.get("t_status") or "")
            and str(row.get("t1_status") or ""),
            "public P1 Shadow selected row drifted",
        )
    sources = payload.get("source_bindings")
    _expect(
        isinstance(sources, Mapping)
        and set(sources)
        == {
            "mixed_projection",
            "selection",
            "statistics",
            "t_verification",
            "t1_settlement",
        },
        "public P1 Shadow source surface drifted",
    )
    for key in ("mixed_projection", "selection", "statistics"):
        binding = sources[key]
        _expect(
            isinstance(binding, Mapping)
            and str(binding.get("path") or "")
            and SHA256_RE.fullmatch(str(binding.get("sha256") or "")) is not None
            and SHA256_RE.fullmatch(str(binding.get("snapshot_sha256") or ""))
            is not None,
            f"public P1 Shadow {key} binding drifted",
        )
    for key in ("t_verification", "t1_settlement"):
        binding = sources[key]
        _expect(
            binding is None
            or (
                isinstance(binding, Mapping)
                and set(binding) == {"path", "sha256"}
                and str(binding.get("path") or "")
                and SHA256_RE.fullmatch(str(binding.get("sha256") or ""))
                is not None
            ),
            f"public P1 Shadow {key} binding drifted",
        )
    _expect(
        isinstance(payload.get("cohorts"), Mapping)
        and isinstance(payload.get("forward_signal_date_progress_180"), Mapping)
        and isinstance(payload.get("probability_diagnostics"), Mapping)
        and payload.get("snapshot_sha256") == _public_state_snapshot(payload),
        "public P1 Shadow aggregate/snapshot drifted",
    )

    if repo_root is not None:
        repo_root = repo_root.resolve(strict=True)
        _strict_as_of_date(
            repo_root,
            signal_date=signal_date,
            as_of_date=as_of_date,
        )
        loaded: dict[str, Path] = {}
        for key in ("mixed_projection", "selection", "statistics"):
            binding = sources[key]
            path = _safe_file(
                repo_root,
                Path(str(binding["path"])),
                label=f"public P1 Shadow {key}",
            )
            _expect(_sha256(path) == binding["sha256"], f"public P1 Shadow {key} SHA drifted")
            loaded[key] = path
        for key in ("t_verification", "t1_settlement"):
            binding = sources[key]
            if binding is None:
                continue
            path = _safe_file(
                repo_root,
                Path(str(binding["path"])),
                label=f"public P1 Shadow {key}",
            )
            _expect(_sha256(path) == binding["sha256"], f"public P1 Shadow {key} SHA drifted")
        selection = _read_json(loaded["selection"], label="public P1 Shadow selection")
        validate_primary_profit_forward_shadow(
            selection,
            repo_root=repo_root,
            require_downloads=True,
        )
        _expect(
            selection.get("signal_date") == signal_date
            and selection.get("exec_date") == exec_date
            and selection.get("exit_date") == exit_date
            and selection.get("snapshot_sha256")
            == sources["selection"].get("snapshot_sha256"),
            "public P1 Shadow selection/date binding drifted",
        )
        summary = _read_json(loaded["statistics"], label="public P1 Shadow statistics")
        try:
            from top10decision.decision.executable_profit_shadow_settlement import (
                validate_statistics,
            )
            validate_statistics(summary)
        except (ImportError, RuntimeError, ValueError) as exc:
            raise PrimaryProfitForwardShadowError(
                "public P1 Shadow statistics are invalid"
            ) from exc
        _expect(
            summary.get("as_of_date") == as_of_date
            and summary.get("snapshot_sha256")
            == sources["statistics"].get("snapshot_sha256"),
            "public P1 Shadow statistics as-of binding drifted",
        )
        _expect(
            payload.get("cohorts") == summary.get("cohorts")
            and payload.get("forward_signal_date_progress_180")
            == summary.get("forward_signal_date_progress_180")
            and payload.get("probability_diagnostics")
            == summary.get("probability_diagnostics")
            and sources.get("mixed_projection")
            == selection["source_bindings"]["mixed_projection"],
            "public P1 Shadow aggregate/source projection drifted",
        )
        expected_rows, expected_truth = _selected_rows_for_public_state(
            repo_root,
            selection=selection,
            as_of_date=as_of_date,
        )
        _expect(
            payload.get("latest_selected_rows") == expected_rows
            and sources.get("t_verification") == expected_truth["t_verification"]
            and sources.get("t1_settlement") == expected_truth["t1_settlement"],
            "public P1 Shadow truth projection drifted",
        )


def _materialize_public_state(
    repo_root: Path,
    state: Mapping[str, Any],
) -> tuple[Path, Path, dict[str, Any]]:
    repo_root = repo_root.resolve(strict=True)
    validate_primary_profit_forward_shadow_public_state(
        state,
        repo_root=repo_root,
    )
    signal_date = str(state["signal_date"])
    as_of_date = str(state["as_of_date"])
    output = _safe_directory(repo_root, PUBLIC_ROOT, label="public Shadow output")
    state_path = output / f"shadow_state_{signal_date}_asof_{as_of_date}.json"
    state_bytes = _pretty_json_bytes(state)
    index = {
        "schema_version": PUBLIC_INDEX_SCHEMA,
        "index_kind": "primary_profit_forward_shadow_public_pointer",
        "data_alias": False,
        "latest_signal_date": signal_date,
        "latest_exec_date": state["exec_date"],
        "latest_exit_date": state["exit_date"],
        "latest_as_of_date": as_of_date,
        "latest_state_url": state_path.relative_to(repo_root).as_posix(),
        "latest_state_sha256": _sha256_bytes(state_bytes),
        "latest_state_snapshot_sha256": state["snapshot_sha256"],
        "latest_selection_identity_sha256": state["source_bindings"]["selection"][
            "selection_identity_sha256"
        ],
        "latest_mixed_projection_sha256": state["source_bindings"]["mixed_projection"][
            "sha256"
        ],
        "boundaries": copy.deepcopy(BOUNDARIES),
    }
    validate_primary_profit_forward_shadow_public_index(index)
    index_path = repo_root / PUBLIC_INDEX_PATH
    lock_root = _safe_output_directory(repo_root)
    with _lock(lock_root):
        if index_path.exists():
            existing = _read_json(index_path, label="existing public Shadow index")
            validate_primary_profit_forward_shadow_public_index(existing)
            existing_date = _normal_date(existing.get("latest_signal_date"))
            existing_as_of = _normal_date(existing.get("latest_as_of_date"))
            _expect(
                existing_date
                and (existing_date, existing_as_of) <= (signal_date, as_of_date),
                "out-of-order public Shadow pointer rejected",
            )
            if (existing_date, existing_as_of) == (signal_date, as_of_date):
                _expect(existing == index, "same-as-of public Shadow pointer rewrite rejected")
                _install_new(state_path, state_bytes)
                return state_path, index_path, existing
        _install_new(state_path, state_bytes)
        _atomic_pointer(index_path, _pretty_json_bytes(index))
    return state_path, index_path, index


def validate_primary_profit_forward_shadow_public_index(
    payload: Mapping[str, Any],
) -> None:
    expected = {
        "schema_version",
        "index_kind",
        "data_alias",
        "latest_signal_date",
        "latest_exec_date",
        "latest_exit_date",
        "latest_as_of_date",
        "latest_state_url",
        "latest_state_sha256",
        "latest_state_snapshot_sha256",
        "latest_selection_identity_sha256",
        "latest_mixed_projection_sha256",
        "boundaries",
    }
    signal_date = _normal_date(payload.get("latest_signal_date"))
    as_of_date = _normal_date(payload.get("latest_as_of_date"))
    _expect(
        set(payload) == expected
        and payload.get("schema_version") == PUBLIC_INDEX_SCHEMA
        and payload.get("index_kind")
        == "primary_profit_forward_shadow_public_pointer"
        and payload.get("data_alias") is False
        and signal_date
        and _normal_date(payload.get("latest_exec_date"))
        and _normal_date(payload.get("latest_exit_date"))
        and as_of_date >= signal_date
        and payload.get("latest_state_url")
        == (
            f"{PUBLIC_ROOT.as_posix()}/"
            f"shadow_state_{signal_date}_asof_{as_of_date}.json"
        )
        and payload.get("boundaries") == BOUNDARIES,
        "public P1 Shadow index identity/path drifted",
    )
    for key in (
        "latest_state_sha256",
        "latest_state_snapshot_sha256",
        "latest_selection_identity_sha256",
        "latest_mixed_projection_sha256",
    ):
        _expect(
            SHA256_RE.fullmatch(str(payload.get(key) or "")) is not None,
            f"public P1 Shadow index {key} invalid",
        )


def project_primary_profit_forward_shadow_state(
    repo_root: Path,
    signal_date: str,
    as_of_date: str,
) -> dict[str, Any]:
    """Rebuild cumulative statistics and publish one immutable exact-D/as-of sidecar."""

    repo_root = repo_root.resolve(strict=True)
    signal_date = _normal_date(signal_date)
    _expect(signal_date, "P1 Shadow projection signal date must be YYYYMMDD")
    as_of_date = _strict_as_of_date(
        repo_root,
        signal_date=signal_date,
        as_of_date=as_of_date,
    )
    selection_path = _safe_file(
        repo_root,
        OUTPUT_ROOT / f"shadow_{signal_date}.json",
        label="materialized P1 Shadow selection",
    )
    selection = _read_json(selection_path, label="materialized P1 Shadow selection")
    validate_primary_profit_forward_shadow(
        selection,
        repo_root=repo_root,
        require_downloads=True,
    )
    _expect(selection.get("signal_date") == signal_date, "P1 Shadow selection date drifted")
    summary_path, summary = _rebuild_forward_statistics(
        repo_root,
        as_of_date=as_of_date,
    )
    state = build_primary_profit_shadow_public_state(
        repo_root,
        selection_path=selection_path,
        selection=selection,
        summary_path=summary_path,
        summary=summary,
    )
    state_path, public_index_path, public_index = _materialize_public_state(
        repo_root,
        state,
    )
    return {
        "statistics": summary_path,
        "statistics_payload": summary,
        "public_state": state_path,
        "public_state_payload": state,
        "public_index": public_index_path,
        "public_pointer": public_index,
    }


def validate_primary_profit_forward_shadow_repository_chain(
    repo_root: Path,
    signal_date: str,
) -> dict[str, Any]:
    """Validate the complete latest P1→Shadow selection/statistics/public chain."""

    repo_root = repo_root.resolve(strict=True)
    signal_date = _normal_date(signal_date)
    _expect(signal_date, "P1 Shadow repository-chain D must be YYYYMMDD")
    selection_path = _safe_file(
        repo_root,
        OUTPUT_ROOT / f"shadow_{signal_date}.json",
        label="P1 Shadow selection JSON",
    )
    selection_csv = _safe_file(
        repo_root,
        OUTPUT_ROOT / f"shadow_{signal_date}.csv",
        label="P1 Shadow selection CSV",
    )
    selection = _read_json(selection_path, label="P1 Shadow selection JSON")
    validate_primary_profit_forward_shadow(
        selection,
        repo_root=repo_root,
        require_downloads=True,
    )
    _expect(selection.get("signal_date") == signal_date, "P1 Shadow dated selection drifted")
    _expect(
        _sha256(selection_csv) == selection["downloads"]["csv_sha256"],
        "P1 Shadow selection CSV SHA drifted",
    )
    _expect(
        selection_csv.read_bytes() == _csv_bytes(selection),
        "P1 Shadow selection CSV canonical bytes drifted",
    )

    selection_index_path = _safe_file(
        repo_root,
        PRIMARY_INDEX_PATH,
        label="P1 Shadow primary pointer",
    )
    selection_index = _read_json(selection_index_path, label="P1 Shadow primary pointer")
    validate_primary_profit_forward_shadow_index(selection_index)
    _expect(
        selection_index.get("latest_signal_date") == signal_date
        and selection_index.get("latest_json_sha256") == _sha256(selection_path)
        and selection_index.get("latest_csv_sha256") == _sha256(selection_csv)
        and selection_index.get("latest_snapshot_sha256")
        == selection.get("snapshot_sha256")
        and selection_index.get("latest_selection_identity_sha256")
        == selection.get("selection_identity_sha256"),
        "P1 Shadow primary pointer bytes/date drifted",
    )

    summary_path = _safe_file(
        repo_root,
        STATISTICS_PATH,
        label="P1 Shadow statistics",
    )
    summary = _read_json(summary_path, label="P1 Shadow statistics")
    try:
        from top10decision.decision.executable_profit_shadow_settlement import (
            validate_statistics,
        )
        validate_statistics(summary)
    except (ImportError, RuntimeError, ValueError) as exc:
        raise PrimaryProfitForwardShadowError("P1 Shadow statistics are invalid") from exc
    expected_selection_binding = {
        "path": selection_path.relative_to(repo_root).as_posix(),
        "sha256": _sha256(selection_path),
    }
    _expect(
        expected_selection_binding in summary.get("input_files", []),
        "P1 Shadow statistics do not bind exact selection bytes",
    )

    public_index_path = _safe_file(
        repo_root,
        PUBLIC_INDEX_PATH,
        label="public P1 Shadow pointer",
    )
    public_index = _read_json(public_index_path, label="public P1 Shadow pointer")
    validate_primary_profit_forward_shadow_public_index(public_index)
    _expect(
        public_index.get("latest_signal_date") == signal_date,
        "public P1 Shadow pointer is not exact-D",
    )
    state_path = _safe_file(
        repo_root,
        Path(str(public_index["latest_state_url"])),
        label="public P1 Shadow state",
    )
    state = _read_json(state_path, label="public P1 Shadow state")
    validate_primary_profit_forward_shadow_public_state(
        state,
        repo_root=repo_root,
    )
    _expect(
        public_index.get("latest_state_sha256") == _sha256(state_path)
        and public_index.get("latest_state_snapshot_sha256")
        == state.get("snapshot_sha256")
        and state["source_bindings"]["statistics"]["sha256"]
        == _sha256(summary_path)
        and state["source_bindings"]["statistics"]["snapshot_sha256"]
        == summary.get("snapshot_sha256"),
        "public P1 Shadow pointer/statistics bytes drifted",
    )
    return {
        "signal_date": signal_date,
        "as_of_date": state["as_of_date"],
        "selection_json": selection_path,
        "selection_csv": selection_csv,
        "selection_index": selection_index_path,
        "statistics": summary_path,
        "public_state": state_path,
        "public_index": public_index_path,
        "selected_slots": selection["shadow_top2"]["actual_slots"],
        "official_trade_action_created": False,
    }


def freeze_primary_profit_forward_shadow(
    repo_root: Path,
    signal_date: str,
    *,
    selected_at: datetime | None = None,
) -> dict[str, Any]:
    selected_at = selected_at or datetime.now(SHANGHAI)
    payload = build_primary_profit_forward_shadow(
        repo_root,
        signal_date,
        selected_at=selected_at,
    )
    json_path, csv_path, index_path, index = materialize_primary_profit_forward_shadow(
        repo_root,
        payload,
        _now=selected_at,
    )
    projected = project_primary_profit_forward_shadow_state(
        repo_root.resolve(strict=True),
        signal_date,
        signal_date,
    )
    chain = validate_primary_profit_forward_shadow_repository_chain(
        repo_root.resolve(strict=True),
        signal_date,
    )
    return {
        "payload": _read_json(json_path, label="materialized P1 Shadow selection"),
        "selection_json": json_path,
        "selection_csv": csv_path,
        "selection_index": index_path,
        "selection_pointer": index,
        "statistics": projected["statistics"],
        "public_state": projected["public_state"],
        "public_index": projected["public_index"],
        "public_pointer": projected["public_pointer"],
        "chain": chain,
    }


__all__ = [
    "ARTIFACT_KIND",
    "CONTRACT_ID",
    "ENTRY_POLICY_ID",
    "INDEX_SCHEMA_VERSION",
    "PrimaryProfitForwardShadowError",
    "SCHEMA_VERSION",
    "build_primary_profit_forward_shadow",
    "build_primary_profit_shadow_public_state",
    "freeze_primary_profit_forward_shadow",
    "materialize_primary_profit_forward_shadow",
    "project_primary_profit_forward_shadow_state",
    "validate_primary_profit_forward_shadow",
    "validate_primary_profit_forward_shadow_index",
    "validate_primary_profit_forward_shadow_public_index",
    "validate_primary_profit_forward_shadow_public_state",
    "validate_primary_profit_forward_shadow_repository_chain",
]
