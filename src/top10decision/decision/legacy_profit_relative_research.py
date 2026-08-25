from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import math
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from top10decision.decision.three_engine_models import (
    LoadedThreeEngineArtifacts,
    ProbabilityHeadBundle,
    _feature_snapshot_sha256,
    _normalize_inference_pool,
    attach_runtime_promotion_priors,
)
from top10decision.decision.three_rank import (
    top10_members_sha256,
    validate_three_rank_contract,
)


PROJECTION_SCHEMA = "dc20_legacy_profit_relative_research_projection_v1"
PROJECTION_KIND = "immutable_d_frozen_legacy_profit_relative_research"
INDEX_SCHEMA = "dc20_legacy_profit_relative_research_index_v1"
INDEX_KIND = "dated_legacy_profit_relative_research_pointer_only"
PUBLIC_STATUS = "PUBLIC_RESEARCH_ONLY_NOT_FORMAL"
OUTPUT_ROOT = Path("outputs/decision/legacy_profit_relative_research")

SEALED_VALIDATION_PATH = Path(
    "data/decision_three_engines/recovery/20260821/model_snapshot/validation.json"
)
SEALED_VALIDATION_SHA256 = (
    "99f89e8bbc40d0f6cc39c3312039156a79c4f45e24114fc4affb900f23a46fe4"
)
SEALED_PROFIT_ARTIFACT_PATH = Path(
    "data/decision_three_engines/recovery/20260821/model_snapshot/profit.joblib"
)
SEALED_PROFIT_ARTIFACT_SHA256 = (
    "0e5e251dc0632ed120baf7e758a4cbfcebd940857fab62e71c57f3c1979891f3"
)
SEALED_PROFIT_MODEL_VERSION = (
    "decision_three_engine_models_v2:profit:20260814:extra_trees:beta"
)
SEALED_PROFIT_MODEL_AS_OF_DATE = "20260814"
SEALED_PROFIT_OFFICIAL_STATUS = "NOT_READY_VALIDATION_GATE"
SEALED_PROFIT_GATE_PASS_COUNT = 20
SEALED_PROFIT_GATE_TOTAL_COUNT = 26
SEALED_PROFIT_GATE_SCORE_PCT = 76.9
SCORE_SEMANTICS = "raw_model_relative_score_not_probability"

DATE_RE = re.compile(r"20\d{6}")
CODE_RE = re.compile(r"\d{6}\.(?:SH|SZ)")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
ROW_FIELDS = (
    "ts_code",
    "name",
    "industry",
    "stage_transition",
    "promotion_rank",
    "legacy_profit_relative_rank",
    "legacy_profit_raw_score",
    "legacy_profit_relative_percentile",
    "rank_tied",
    "rank_group_size",
)


class LegacyProfitRelativeResearchError(RuntimeError):
    """Raised when the sealed research ordering cannot prove its lineage."""


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise LegacyProfitRelativeResearchError(message)


def _normal_date(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _normal_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text if CODE_RE.fullmatch(text) else ""


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
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
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


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _payload_snapshot(payload: Mapping[str, Any]) -> str:
    copied = dict(payload)
    copied.pop("snapshot_sha256", None)
    copied.pop("downloads", None)
    return _sha256_bytes(_canonical_json_bytes(copied))


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
    _expect(current.resolve(strict=True).is_relative_to(root), f"{label} escaped repository")
    return current


def _ensure_directory(root: Path, relative: Path) -> Path:
    root = root.resolve(strict=True)
    _expect(
        not relative.is_absolute() and ".." not in relative.parts,
        "unsafe research output path",
    )
    current = root
    for part in relative.parts:
        current = current / part
        if current.exists():
            _expect(
                current.is_dir() and not current.is_symlink(),
                "research output has a symlink ancestor",
            )
        else:
            current.mkdir()
    return current


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_immutable(path: Path, content: bytes) -> None:
    if path.exists():
        _expect(
            path.is_file() and not path.is_symlink(),
            "immutable research artifact path is unsafe",
        )
        _expect(path.read_bytes() == content, "immutable research artifact rewrite rejected")
        return
    _atomic_write(path, content)


def _load_three_rank(
    repo_root: Path,
    signal_date: str,
) -> tuple[Path, Path, dict[str, Any]]:
    relative = Path(f"outputs/decision/three_rank_top10_{signal_date}.json")
    path = _safe_existing_file(repo_root, relative, label="dated three-rank JSON")
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LegacyProfitRelativeResearchError("dated three-rank JSON is invalid") from exc
    _expect(isinstance(contract, dict), "dated three-rank must be an object")
    try:
        validate_three_rank_contract(contract)
    except Exception as exc:
        raise LegacyProfitRelativeResearchError("dated three-rank contract is invalid") from exc
    _expect(contract.get("signal_date") == signal_date, "three-rank date binding drifted")
    rows = contract.get("rows")
    _expect(isinstance(rows, list) and 0 <= len(rows) <= 10, "three-rank row count invalid")
    model = (contract.get("models") or {}).get("profit")
    _expect(isinstance(model, Mapping), "three-rank profit model metadata missing")
    _expect(
        model.get("status") == SEALED_PROFIT_OFFICIAL_STATUS
        and model.get("ranking_ready") is False
        and model.get("probability_ready") is False,
        "official profit head is not the expected NOT_READY source",
    )
    _expect(
        all(
            row.get("profit_rank") is None
            and row.get("predicted_profit_probability") is None
            for row in rows
        ),
        "official NOT_READY profit fields were populated",
    )
    codes = [_normal_code(row.get("ts_code")) for row in rows]
    expected_members = top10_members_sha256(signal_date, codes)
    _expect(
        all(codes)
        and len(codes) == len(set(codes))
        and contract.get("top10_members_sha256") == expected_members,
        "three-rank frozen membership is invalid",
    )
    downloads = contract.get("downloads")
    csv_relative = Path(f"outputs/decision/three_rank_top10_{signal_date}.csv")
    _expect(
        isinstance(downloads, Mapping)
        and downloads.get("json_url") == relative.as_posix()
        and downloads.get("csv_url") == csv_relative.as_posix()
        and downloads.get("row_count") == len(rows),
        "three-rank download binding is invalid",
    )
    csv_path = _safe_existing_file(repo_root, csv_relative, label="dated three-rank CSV")
    _expect(downloads.get("csv_sha256") == _sha256(csv_path), "three-rank CSV SHA drifted")
    return path, csv_path, contract


def _sealed_recovery_runtime_frame(repo_root: Path) -> pd.DataFrame:
    """Load the deterministic D21 recovery builder without sys.path assumptions."""

    script_path = _safe_existing_file(
        repo_root,
        Path("scripts/build_decision_three_rank_snapshot.py"),
        label="sealed recovery builder",
    )
    spec = importlib.util.spec_from_file_location(
        "dc20_legacy_profit_recovery_builder",
        script_path,
    )
    _expect(spec is not None and spec.loader is not None, "recovery builder import failed")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        pool, bars_by_code, _recovery = module.load_recovery_inputs(repo_root)
        return module.build_runtime_candidate_frame(repo_root, pool, bars_by_code)
    except Exception as exc:
        raise LegacyProfitRelativeResearchError(
            "sealed recovery deterministic rebuild failed"
        ) from exc


def validate_sealed_profit_model(
    repo_root: Path,
    loaded: LoadedThreeEngineArtifacts,
) -> ProbabilityHeadBundle:
    """Accept exactly the archived 20260821 profit head, never a current head."""

    repo_root = repo_root.resolve(strict=True)
    validation_path = _safe_existing_file(
        repo_root,
        SEALED_VALIDATION_PATH,
        label="sealed legacy validation",
    )
    artifact_path = _safe_existing_file(
        repo_root,
        SEALED_PROFIT_ARTIFACT_PATH,
        label="sealed legacy profit artifact",
    )
    _expect(
        loaded.root.resolve() == repo_root
        and loaded.validation_path.resolve() == validation_path.resolve(),
        "loaded legacy snapshot root/path drifted",
    )
    _expect(_sha256(validation_path) == SEALED_VALIDATION_SHA256, "sealed validation SHA drifted")
    _expect(_sha256(artifact_path) == SEALED_PROFIT_ARTIFACT_SHA256, "sealed profit SHA drifted")
    metadata = loaded.metadata.get("profit") or {}
    _expect(
        metadata.get("status") == SEALED_PROFIT_OFFICIAL_STATUS
        and metadata.get("version") == SEALED_PROFIT_MODEL_VERSION
        and metadata.get("as_of_date") == SEALED_PROFIT_MODEL_AS_OF_DATE
        and metadata.get("artifact_sha256") == SEALED_PROFIT_ARTIFACT_SHA256
        and metadata.get("validation_gate_pass_count") == SEALED_PROFIT_GATE_PASS_COUNT
        and metadata.get("validation_gate_total_count") == SEALED_PROFIT_GATE_TOTAL_COUNT,
        "sealed legacy profit metadata drifted",
    )
    payload = loaded.payloads.get("profit") or {}
    bundle = payload.get("bundle")
    _expect(isinstance(bundle, ProbabilityHeadBundle), "sealed legacy profit bundle missing")
    _expect(
        payload.get("status") == SEALED_PROFIT_OFFICIAL_STATUS
        and payload.get("promoted") is False
        and payload.get("model_version") == SEALED_PROFIT_MODEL_VERSION
        and payload.get("model_as_of_date") == SEALED_PROFIT_MODEL_AS_OF_DATE
        and bundle.head == "profit"
        and bundle.trained_signal_end == SEALED_PROFIT_MODEL_AS_OF_DATE,
        "sealed legacy profit bundle identity drifted",
    )
    return bundle


def score_legacy_profit_relative_rows(
    repo_root: Path,
    *,
    signal_date: str,
    runtime_candidates: pd.DataFrame,
    three_rank: Mapping[str, Any],
    loaded: LoadedThreeEngineArtifacts,
) -> tuple[list[dict[str, Any]], str]:
    """Score only the frozen TopN with a hash-pinned, NOT_READY legacy head.

    The returned rank is a research annotation. Equal raw model scores receive
    the same dense rank; no symbol/code tie-break is allowed to impersonate
    model separation.
    """

    repo_root = repo_root.resolve(strict=True)
    date = _normal_date(signal_date)
    _expect(DATE_RE.fullmatch(date) is not None, "research signal date is invalid")
    _expect(three_rank.get("signal_date") == date, "runtime/three-rank date drifted")
    frozen_rows = three_rank.get("rows")
    _expect(isinstance(frozen_rows, list) and len(frozen_rows) <= 10, "frozen rows invalid")
    bundle = validate_sealed_profit_model(repo_root, loaded)

    try:
        base = _normalize_inference_pool(runtime_candidates, date)
        base = attach_runtime_promotion_priors(
            base,
            loaded.runtime_prior_ledger,
            signal_date=date,
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise LegacyProfitRelativeResearchError("runtime legacy feature preparation failed") from exc
    _expect(
        len(base) == int(three_rank.get("promotion_pool_size") or 0),
        "runtime pool size differs from frozen promotion pool",
    )
    empty_contract = not len(base) and not frozen_rows
    if not empty_contract:
        missing_features = sorted(
            set(bundle.feature_builder.numeric_columns) - set(base.columns)
        )
        all_missing_features = sorted(
            name
            for name in bundle.feature_builder.numeric_columns
            if name in base.columns
            and not pd.to_numeric(base[name], errors="coerce")
            .replace([np.inf, -np.inf], np.nan)
            .notna()
            .any()
        )
        _expect(
            not missing_features,
            f"runtime legacy features missing: {missing_features}",
        )
        _expect(
            not all_missing_features,
            f"runtime legacy features entirely missing: {all_missing_features}",
        )
    feature_snapshot = _feature_snapshot_sha256(base, bundle.feature_builder)
    _expect(
        feature_snapshot == three_rank.get("feature_snapshot_sha256"),
        "runtime feature snapshot differs from frozen three-rank",
    )

    frozen_codes = [_normal_code(row.get("ts_code")) for row in frozen_rows]
    base_codes = set(base["ts_code"].astype(str))
    _expect(
        all(frozen_codes)
        and len(frozen_codes) == len(set(frozen_codes))
        and set(frozen_codes).issubset(base_codes),
        "frozen TopN is not an exact runtime subset",
    )
    expected_members = top10_members_sha256(date, frozen_codes)
    _expect(
        three_rank.get("top10_members_sha256") == expected_members,
        "frozen TopN member hash drifted",
    )
    if not frozen_rows:
        return [], feature_snapshot

    selected = base.set_index("ts_code", drop=False).loc[frozen_codes].copy()
    try:
        _calibrated, raw = bundle.predict_components(selected)
    except (TypeError, ValueError, KeyError) as exc:
        raise LegacyProfitRelativeResearchError("sealed legacy profit scoring failed") from exc
    raw = np.asarray(raw, dtype=float)
    _expect(
        raw.shape == (len(selected),)
        and np.isfinite(raw).all()
        and ((0.0 <= raw) & (raw <= 1.0)).all(),
        "sealed legacy profit raw scores are invalid",
    )
    scored = pd.DataFrame(
        {
            "ts_code": frozen_codes,
            "legacy_profit_raw_score": raw,
        }
    )
    scored["legacy_profit_relative_rank"] = (
        scored["legacy_profit_raw_score"]
        .rank(method="dense", ascending=False)
        .astype(int)
    )
    if len(scored) == 1:
        # A single candidate has a model score and rank, but no meaningful
        # within-set percentile comparison.
        scored["legacy_profit_relative_percentile"] = np.nan
    else:
        scored["legacy_profit_relative_percentile"] = scored[
            "legacy_profit_raw_score"
        ].rank(method="average", ascending=True, pct=True)
    group_sizes = scored.groupby("legacy_profit_raw_score", sort=False)[
        "ts_code"
    ].transform("size")
    scored["rank_group_size"] = group_sizes.astype(int)
    scored["rank_tied"] = scored["rank_group_size"].gt(1)
    scored_by_code = scored.set_index("ts_code", drop=False)

    output: list[dict[str, Any]] = []
    for frozen in frozen_rows:
        code = _normal_code(frozen.get("ts_code"))
        scored_row = scored_by_code.loc[code]
        output.append(
            {
                "ts_code": code,
                "name": str(frozen.get("name") or ""),
                "industry": str(frozen.get("industry") or ""),
                "stage_transition": str(frozen.get("stage_transition") or ""),
                "promotion_rank": int(frozen["promotion_rank"]),
                "legacy_profit_relative_rank": int(
                    scored_row["legacy_profit_relative_rank"]
                ),
                "legacy_profit_raw_score": float(
                    scored_row["legacy_profit_raw_score"]
                ),
                "legacy_profit_relative_percentile": (
                    None
                    if pd.isna(scored_row["legacy_profit_relative_percentile"])
                    else float(scored_row["legacy_profit_relative_percentile"])
                ),
                "rank_tied": bool(scored_row["rank_tied"]),
                "rank_group_size": int(scored_row["rank_group_size"]),
            }
        )
    output.sort(
        key=lambda row: (
            row["legacy_profit_relative_rank"],
            row["promotion_rank"],
        )
    )
    return output, feature_snapshot


def build_projection(
    repo_root: Path,
    *,
    signal_date: str,
    runtime_candidates: pd.DataFrame,
    loaded: LoadedThreeEngineArtifacts,
    runtime_source: Mapping[str, Any],
) -> dict[str, Any]:
    repo_root = repo_root.resolve(strict=True)
    date = _normal_date(signal_date)
    three_json, three_csv, three_rank = _load_three_rank(repo_root, date)
    rows, feature_snapshot = score_legacy_profit_relative_rows(
        repo_root,
        signal_date=date,
        runtime_candidates=runtime_candidates,
        three_rank=three_rank,
        loaded=loaded,
    )
    _expect(
        isinstance(runtime_source, Mapping)
        and runtime_source.get("source_kind")
        in {"sealed_20260821_recovery", "canonical_runtime_feature_csv"}
        and isinstance(runtime_source.get("path"), str)
        and SHA256_RE.fullmatch(str(runtime_source.get("sha256") or "")) is not None,
        "runtime source binding is invalid",
    )
    runtime_relative = Path(str(runtime_source["path"]))
    runtime_path = _safe_existing_file(
        repo_root,
        runtime_relative,
        label="runtime feature source",
    )
    _expect(
        _sha256(runtime_path) == runtime_source["sha256"],
        "runtime feature source SHA drifted",
    )
    if runtime_source["source_kind"] == "sealed_20260821_recovery":
        _expect(
            date == "20260821"
            and runtime_relative
            == Path("data/decision_three_engines/recovery/20260821/manifest.json"),
            "sealed recovery source identity drifted",
        )
    else:
        _expect(
            runtime_relative
            == Path(
                f"data/decision/legacy_profit_relative/runtime_features_{date}.csv"
            ),
            "canonical runtime feature source path/date drifted",
        )
    payload: dict[str, Any] = {
        "schema_version": PROJECTION_SCHEMA,
        "artifact_kind": PROJECTION_KIND,
        "status": PUBLIC_STATUS,
        "research_only": True,
        "actual_execution_claimed": False,
        "signal_date": date,
        "exec_date": three_rank["exec_date"],
        "exit_date": three_rank["exit_date"],
        "candidate_count": len(rows),
        "top10_members_sha256": three_rank["top10_members_sha256"],
        "feature_snapshot_sha256": feature_snapshot,
        "source_three_rank": {
            "json_path": three_json.relative_to(repo_root).as_posix(),
            "json_sha256": _sha256(three_json),
            "csv_path": three_csv.relative_to(repo_root).as_posix(),
            "csv_sha256": _sha256(three_csv),
            "bundle_sha256": three_rank["bundle_sha256"],
            "official_profit_fields_remain_null": True,
        },
        "source_runtime_features": {
            "source_kind": runtime_source["source_kind"],
            "path": str(runtime_source["path"]),
            "sha256": str(runtime_source["sha256"]),
            "feature_snapshot_sha256": feature_snapshot,
        },
        "model": {
            "head": "profit",
            "display_name": "原盈利模型·相对实验排序",
            "official_status": SEALED_PROFIT_OFFICIAL_STATUS,
            "formal_ranking_ready": False,
            "formal_probability_ready": False,
            "version": SEALED_PROFIT_MODEL_VERSION,
            "model_as_of_date": SEALED_PROFIT_MODEL_AS_OF_DATE,
            "validation_gate_pass_count": SEALED_PROFIT_GATE_PASS_COUNT,
            "validation_gate_total_count": SEALED_PROFIT_GATE_TOTAL_COUNT,
            "validation_gate_score_pct": SEALED_PROFIT_GATE_SCORE_PCT,
            "score_semantics": SCORE_SEMANTICS,
            "target_semantics": (
                "T_open_proxy_to_T1_open_proxy_net_return_gt_0_"
                "conditional_on_market_fill_proxy_eq_1"
            ),
            "p_fill_integrated": False,
            "probability_claimed": False,
            "sealed_validation_path": SEALED_VALIDATION_PATH.as_posix(),
            "sealed_validation_sha256": SEALED_VALIDATION_SHA256,
            "sealed_artifact_path": SEALED_PROFIT_ARTIFACT_PATH.as_posix(),
            "sealed_artifact_sha256": SEALED_PROFIT_ARTIFACT_SHA256,
        },
        "ranking_contract": {
            "candidate_scope": "exact_frozen_promotion_topn_only",
            "membership_or_promotion_rank_may_change": False,
            "formal_profit_fields_may_change": False,
            "trade_or_action_may_change": False,
            "score_direction": "higher_raw_score_is_better_relative_order",
            "tie_policy": "equal_raw_score_equal_dense_rank_no_code_tiebreak",
            "percentile_semantics": "within_frozen_topn_empirical_rank_percentile",
            "candidate_count_rule": "show_exact_n_zero_to_ten_never_pad",
            "relative_comparison_sufficient": len(rows) >= 2,
        },
        "rows": rows,
        "execution": {
            "decision": "NO_TRADE",
            "buy_count": 0,
            "order_count": 0,
            "broker_connected": False,
            "human_decision_support_only": True,
        },
    }
    payload["snapshot_sha256"] = _payload_snapshot(payload)
    validate_projection(payload)
    return payload


def validate_projection(
    payload: Mapping[str, Any],
    *,
    require_downloads: bool = False,
) -> None:
    _expect(isinstance(payload, Mapping), "research projection must be an object")
    _expect(
        payload.get("schema_version") == PROJECTION_SCHEMA
        and payload.get("artifact_kind") == PROJECTION_KIND
        and payload.get("status") == PUBLIC_STATUS
        and payload.get("research_only") is True
        and payload.get("actual_execution_claimed") is False,
        "research projection identity drifted",
    )
    date = str(payload.get("signal_date") or "")
    _expect(DATE_RE.fullmatch(date) is not None, "projection signal date invalid")
    exec_date = str(payload.get("exec_date") or "")
    exit_date = str(payload.get("exit_date") or "")
    _expect(
        DATE_RE.fullmatch(exec_date) is not None
        and DATE_RE.fullmatch(exit_date) is not None
        and date < exec_date < exit_date,
        "projection D/T/T+1 chronology invalid",
    )
    rows = payload.get("rows")
    _expect(
        isinstance(rows, list)
        and payload.get("candidate_count") == len(rows)
        and 0 <= len(rows) <= 10,
        "projection row count invalid",
    )
    model = payload.get("model")
    _expect(
        isinstance(model, Mapping)
        and model.get("official_status") == SEALED_PROFIT_OFFICIAL_STATUS
        and model.get("formal_ranking_ready") is False
        and model.get("formal_probability_ready") is False
        and model.get("version") == SEALED_PROFIT_MODEL_VERSION
        and model.get("model_as_of_date") == SEALED_PROFIT_MODEL_AS_OF_DATE
        and model.get("validation_gate_pass_count") == SEALED_PROFIT_GATE_PASS_COUNT
        and model.get("validation_gate_total_count") == SEALED_PROFIT_GATE_TOTAL_COUNT
        and model.get("validation_gate_score_pct") == SEALED_PROFIT_GATE_SCORE_PCT
        and model.get("score_semantics") == SCORE_SEMANTICS
        and model.get("target_semantics")
        == (
            "T_open_proxy_to_T1_open_proxy_net_return_gt_0_"
            "conditional_on_market_fill_proxy_eq_1"
        )
        and model.get("p_fill_integrated") is False
        and model.get("probability_claimed") is False
        and model.get("sealed_validation_path") == SEALED_VALIDATION_PATH.as_posix()
        and model.get("sealed_validation_sha256") == SEALED_VALIDATION_SHA256
        and model.get("sealed_artifact_path") == SEALED_PROFIT_ARTIFACT_PATH.as_posix()
        and model.get("sealed_artifact_sha256") == SEALED_PROFIT_ARTIFACT_SHA256,
        "projection model binding drifted",
    )
    _expect(
        str(model.get("model_as_of_date")) < date,
        "legacy model as-of date is not strictly before D",
    )
    ranking = payload.get("ranking_contract")
    _expect(
        ranking
        == {
            "candidate_scope": "exact_frozen_promotion_topn_only",
            "membership_or_promotion_rank_may_change": False,
            "formal_profit_fields_may_change": False,
            "trade_or_action_may_change": False,
            "score_direction": "higher_raw_score_is_better_relative_order",
            "tie_policy": "equal_raw_score_equal_dense_rank_no_code_tiebreak",
            "percentile_semantics": "within_frozen_topn_empirical_rank_percentile",
            "candidate_count_rule": "show_exact_n_zero_to_ten_never_pad",
            "relative_comparison_sufficient": len(rows) >= 2,
        },
        "projection ranking safety contract drifted",
    )
    execution = payload.get("execution")
    _expect(
        isinstance(execution, Mapping)
        and execution.get("decision") == "NO_TRADE"
        and execution.get("buy_count") == 0
        and execution.get("order_count") == 0
        and execution.get("broker_connected") is False
        and execution.get("human_decision_support_only") is True,
        "research projection claimed execution",
    )
    forbidden = {
        "profit_rank",
        "predicted_profit_probability",
        "action",
        "selected",
        "trade_rank",
        "order",
    }
    codes: list[str] = []
    scores: list[float] = []
    for row in rows:
        _expect(isinstance(row, Mapping), "projection row invalid")
        _expect(set(row) == set(ROW_FIELDS), "projection row fields drifted")
        _expect(not forbidden.intersection(row), "formal/action fields leaked into research row")
        code = _normal_code(row.get("ts_code"))
        score = _finite(row.get("legacy_profit_raw_score"))
        percentile = _finite(row.get("legacy_profit_relative_percentile"))
        _expect(
            bool(code)
            and isinstance(row.get("promotion_rank"), int)
            and isinstance(row.get("legacy_profit_relative_rank"), int)
            and score is not None
            and 0.0 <= score <= 1.0
            and (
                (len(rows) == 1 and row.get("legacy_profit_relative_percentile") is None)
                or (
                    len(rows) >= 2
                    and percentile is not None
                    and 0.0 < percentile <= 1.0
                )
            )
            and isinstance(row.get("rank_tied"), bool)
            and isinstance(row.get("rank_group_size"), int)
            and row.get("rank_group_size") >= 1,
            "projection row score/rank invalid",
        )
        codes.append(code)
        scores.append(score)
    _expect(len(codes) == len(set(codes)), "projection contains duplicate members")
    if rows:
        frame = pd.DataFrame({"score": scores})
        expected_rank = frame["score"].rank(method="dense", ascending=False).astype(int)
        expected_pct = (
            None
            if len(rows) == 1
            else frame["score"].rank(method="average", ascending=True, pct=True)
        )
        sizes = frame.groupby("score", sort=False)["score"].transform("size").astype(int)
        for position, row in enumerate(rows):
            _expect(
                row["legacy_profit_relative_rank"] == int(expected_rank.iloc[position])
                and (
                    (expected_pct is None and row["legacy_profit_relative_percentile"] is None)
                    or (
                        expected_pct is not None
                        and math.isclose(
                            row["legacy_profit_relative_percentile"],
                            float(expected_pct.iloc[position]),
                            rel_tol=0.0,
                            abs_tol=1e-15,
                        )
                    )
                )
                and row["rank_group_size"] == int(sizes.iloc[position])
                and row["rank_tied"] is (int(sizes.iloc[position]) > 1),
                "projection tie/rank calculation drifted",
            )
        expected_order = sorted(
            rows,
            key=lambda row: (
                row["legacy_profit_relative_rank"],
                row["promotion_rank"],
            ),
        )
        _expect(rows == expected_order, "projection rows are not in research rank order")
    _expect(
        payload.get("top10_members_sha256") == top10_members_sha256(date, codes),
        "projection membership hash drifted",
    )
    source_three = payload.get("source_three_rank")
    source_runtime = payload.get("source_runtime_features")
    three_prefix = f"outputs/decision/three_rank_top10_{date}"
    _expect(
        isinstance(source_three, Mapping)
        and source_three.get("json_path") == f"{three_prefix}.json"
        and source_three.get("csv_path") == f"{three_prefix}.csv"
        and source_three.get("official_profit_fields_remain_null") is True
        and all(
            SHA256_RE.fullmatch(str(source_three.get(field) or "")) is not None
            for field in ("json_sha256", "csv_sha256", "bundle_sha256")
        )
        and isinstance(source_runtime, Mapping)
        and source_runtime.get("source_kind")
        in {"sealed_20260821_recovery", "canonical_runtime_feature_csv"}
        and isinstance(source_runtime.get("path"), str)
        and bool(source_runtime.get("path"))
        and SHA256_RE.fullmatch(str(source_runtime.get("sha256") or "")) is not None
        and source_runtime.get("feature_snapshot_sha256")
        == payload.get("feature_snapshot_sha256"),
        "projection source binding invalid",
    )
    _expect(
        SHA256_RE.fullmatch(str(payload.get("feature_snapshot_sha256") or ""))
        is not None
        and SHA256_RE.fullmatch(str(payload.get("top10_members_sha256") or ""))
        is not None,
        "projection hashes invalid",
    )
    if source_runtime.get("source_kind") == "sealed_20260821_recovery":
        _expect(
            date == "20260821"
            and source_runtime.get("path")
            == "data/decision_three_engines/recovery/20260821/manifest.json",
            "sealed runtime source path/date drifted",
        )
    else:
        _expect(
            source_runtime.get("path")
            == f"data/decision/legacy_profit_relative/runtime_features_{date}.csv",
            "canonical runtime source path/date drifted",
        )
    snapshot = payload.get("snapshot_sha256")
    _expect(
        SHA256_RE.fullmatch(str(snapshot or "")) is not None
        and snapshot == _payload_snapshot(payload),
        "projection snapshot SHA drifted",
    )
    downloads = payload.get("downloads")
    if require_downloads:
        prefix = OUTPUT_ROOT / f"projection_{date}"
        _expect(
            isinstance(downloads, Mapping)
            and downloads
            == {
                "json_url": f"{prefix.as_posix()}.json",
                "csv_url": f"{prefix.as_posix()}.csv",
                "csv_sha256": downloads.get("csv_sha256"),
                "row_count": len(rows),
            }
            and SHA256_RE.fullmatch(str(downloads.get("csv_sha256") or ""))
            is not None,
            "projection dated downloads invalid",
        )
    else:
        _expect(downloads is None, "unmaterialized projection unexpectedly has downloads")


def _projection_csv_bytes(payload: Mapping[str, Any]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(ROW_FIELDS), lineterminator="\n")
    writer.writeheader()
    writer.writerows(payload.get("rows") or [])
    return buffer.getvalue().encode("utf-8")


def _index_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    date = str(payload["signal_date"])
    projection_json = OUTPUT_ROOT / f"projection_{date}.json"
    projection_csv = OUTPUT_ROOT / f"projection_{date}.csv"
    downloads = payload.get("downloads") or {}
    return {
        "schema_version": INDEX_SCHEMA,
        "index_kind": INDEX_KIND,
        "status": PUBLIC_STATUS,
        "research_only": True,
        "data_alias": False,
        "latest_fallback_allowed": False,
        "latest_signal_date": date,
        "latest_projection_json_url": projection_json.as_posix(),
        "latest_projection_json_sha256": None,
        "latest_projection_csv_url": projection_csv.as_posix(),
        "latest_projection_csv_sha256": downloads.get("csv_sha256"),
        "latest_projection_snapshot_sha256": payload.get("snapshot_sha256"),
        "candidate_count": payload.get("candidate_count"),
        "top10_members_sha256": payload.get("top10_members_sha256"),
        "model": {
            "official_status": SEALED_PROFIT_OFFICIAL_STATUS,
            "version": SEALED_PROFIT_MODEL_VERSION,
            "model_as_of_date": SEALED_PROFIT_MODEL_AS_OF_DATE,
            "sealed_artifact_sha256": SEALED_PROFIT_ARTIFACT_SHA256,
            "score_semantics": SCORE_SEMANTICS,
        },
    }


def validate_index(payload: Mapping[str, Any]) -> None:
    _expect(
        isinstance(payload, Mapping)
        and payload.get("schema_version") == INDEX_SCHEMA
        and payload.get("index_kind") == INDEX_KIND
        and payload.get("status") == PUBLIC_STATUS
        and payload.get("research_only") is True
        and payload.get("data_alias") is False
        and payload.get("latest_fallback_allowed") is False,
        "legacy profit research index identity drifted",
    )
    date = str(payload.get("latest_signal_date") or "")
    _expect(DATE_RE.fullmatch(date) is not None, "legacy profit research index date invalid")
    prefix = OUTPUT_ROOT / f"projection_{date}"
    _expect(
        payload.get("latest_projection_json_url") == f"{prefix.as_posix()}.json"
        and payload.get("latest_projection_csv_url") == f"{prefix.as_posix()}.csv"
        and all(
            SHA256_RE.fullmatch(str(payload.get(field) or "")) is not None
            for field in (
                "latest_projection_json_sha256",
                "latest_projection_csv_sha256",
                "latest_projection_snapshot_sha256",
                "top10_members_sha256",
            )
        )
        and isinstance(payload.get("candidate_count"), int)
        and 0 <= payload["candidate_count"] <= 10,
        "legacy profit research index pointer invalid",
    )
    model = payload.get("model")
    _expect(
        isinstance(model, Mapping)
        and model.get("official_status") == SEALED_PROFIT_OFFICIAL_STATUS
        and model.get("version") == SEALED_PROFIT_MODEL_VERSION
        and model.get("model_as_of_date") == SEALED_PROFIT_MODEL_AS_OF_DATE
        and model.get("sealed_artifact_sha256") == SEALED_PROFIT_ARTIFACT_SHA256
        and model.get("score_semantics") == SCORE_SEMANTICS,
        "legacy profit research index model binding drifted",
    )


def materialize_projection(
    repo_root: Path,
    payload: Mapping[str, Any],
) -> tuple[Path, Path, Path, dict[str, Any]]:
    repo_root = repo_root.resolve(strict=True)
    payload = dict(payload)
    validate_projection(payload)
    output = _ensure_directory(repo_root, OUTPUT_ROOT)
    date = str(payload["signal_date"])
    json_path = output / f"projection_{date}.json"
    csv_path = output / f"projection_{date}.csv"
    index_path = output / "index.json"

    csv_bytes = _projection_csv_bytes(payload)
    payload["downloads"] = {
        "json_url": json_path.relative_to(repo_root).as_posix(),
        "csv_url": csv_path.relative_to(repo_root).as_posix(),
        "csv_sha256": _sha256_bytes(csv_bytes),
        "row_count": len(payload.get("rows") or []),
    }
    # Downloads are excluded from snapshot_sha256, so this remains the same
    # immutable model/data snapshot created by build_projection().
    json_bytes = _pretty_json_bytes(payload)
    validate_projection(payload, require_downloads=True)
    _write_immutable(json_path, json_bytes)
    _write_immutable(csv_path, csv_bytes)

    index = _index_payload(payload)
    index["latest_projection_json_sha256"] = _sha256(json_path)
    validate_index(index)
    if index_path.exists():
        _expect(index_path.is_file() and not index_path.is_symlink(), "research index path unsafe")
        try:
            existing = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise LegacyProfitRelativeResearchError("existing research index invalid") from exc
        validate_index(existing)
        existing_date = str(existing["latest_signal_date"])
        _expect(date >= existing_date, "out-of-order research pointer update rejected")
        if date == existing_date:
            _expect(existing == index, "same-date research pointer rewrite rejected")
            return json_path, csv_path, index_path, payload
    _atomic_write(index_path, _pretty_json_bytes(index))
    return json_path, csv_path, index_path, payload


def validate_repository_chain(
    repo_root: Path,
    *,
    deterministic_rebuild: bool = True,
) -> dict[str, Any]:
    """Verify the public chain and, by default, rebuild its private source.

    Daily first calls the default strict mode in its isolated research root,
    then copies an immutable repository-owned replay CSV with the sidecar and
    calls strict mode again in the target repository.  Static mode is only for
    a Pages tree that intentionally omits that non-public replay evidence.
    """

    repo_root = repo_root.resolve(strict=True)
    index_path = _safe_existing_file(
        repo_root,
        OUTPUT_ROOT / "index.json",
        label="legacy profit research index",
    )
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LegacyProfitRelativeResearchError("research index JSON invalid") from exc
    validate_index(index)
    date = str(index["latest_signal_date"])
    projection_relative = Path(str(index["latest_projection_json_url"]))
    csv_relative = Path(str(index["latest_projection_csv_url"]))
    projection_path = _safe_existing_file(
        repo_root,
        projection_relative,
        label="dated legacy profit projection JSON",
    )
    csv_path = _safe_existing_file(
        repo_root,
        csv_relative,
        label="dated legacy profit projection CSV",
    )
    _expect(
        _sha256(projection_path) == index["latest_projection_json_sha256"]
        and _sha256(csv_path) == index["latest_projection_csv_sha256"],
        "research index dated artifact SHA drifted",
    )
    try:
        projection = json.loads(projection_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LegacyProfitRelativeResearchError("dated projection JSON invalid") from exc
    validate_projection(projection, require_downloads=True)
    _expect(
        projection.get("signal_date") == date
        and projection.get("snapshot_sha256")
        == index.get("latest_projection_snapshot_sha256")
        and projection.get("candidate_count") == index.get("candidate_count")
        and projection.get("top10_members_sha256")
        == index.get("top10_members_sha256"),
        "research index/projection identity drifted",
    )
    downloads = projection["downloads"]
    _expect(
        downloads["json_url"] == projection_relative.as_posix()
        and downloads["csv_url"] == csv_relative.as_posix()
        and downloads["csv_sha256"] == _sha256(csv_path)
        and csv_path.read_bytes() == _projection_csv_bytes(projection),
        "dated projection CSV does not match projection rows",
    )

    three_json, three_csv, three_rank = _load_three_rank(repo_root, date)
    source_three = projection["source_three_rank"]
    _expect(
        source_three["json_sha256"] == _sha256(three_json)
        and source_three["csv_sha256"] == _sha256(three_csv)
        and source_three["bundle_sha256"] == three_rank["bundle_sha256"]
        and projection["top10_members_sha256"]
        == three_rank["top10_members_sha256"]
        and projection["feature_snapshot_sha256"]
        == three_rank["feature_snapshot_sha256"],
        "projection/three-rank exact source binding drifted",
    )

    if not deterministic_rebuild:
        return {
            "signal_date": date,
            "candidate_count": projection["candidate_count"],
            "projection_sha256": _sha256(projection_path),
            "csv_sha256": _sha256(csv_path),
            "deterministic_rebuild_match": False,
            "public_static_chain_match": True,
        }

    runtime_source = projection["source_runtime_features"]
    runtime_path = _safe_existing_file(
        repo_root,
        Path(str(runtime_source["path"])),
        label="projection runtime feature source",
    )
    _expect(
        _sha256(runtime_path) == runtime_source["sha256"],
        "projection runtime source SHA drifted",
    )
    if runtime_source["source_kind"] == "sealed_20260821_recovery":
        runtime_candidates = _sealed_recovery_runtime_frame(repo_root)
    else:
        try:
            runtime_candidates = pd.read_csv(runtime_path, low_memory=False)
        except (OSError, ValueError, pd.errors.ParserError) as exc:
            raise LegacyProfitRelativeResearchError(
                "canonical runtime feature source is invalid"
            ) from exc
    from top10decision.decision.three_engine_models import (
        load_research_only_legacy_three_engine_snapshot,
    )

    loaded = load_research_only_legacy_three_engine_snapshot(
        repo_root / SEALED_VALIDATION_PATH,
        root=repo_root,
    )
    rebuilt = build_projection(
        repo_root,
        signal_date=date,
        runtime_candidates=runtime_candidates,
        loaded=loaded,
        runtime_source=runtime_source,
    )
    materialized_without_downloads = dict(projection)
    materialized_without_downloads.pop("downloads", None)
    _expect(
        _canonical_json_bytes(rebuilt)
        == _canonical_json_bytes(materialized_without_downloads),
        "published research scores differ from deterministic sealed rebuild",
    )
    return {
        "signal_date": date,
        "candidate_count": projection["candidate_count"],
        "projection_sha256": _sha256(projection_path),
        "csv_sha256": _sha256(csv_path),
        "deterministic_rebuild_match": True,
        "public_static_chain_match": True,
    }


__all__ = [
    "INDEX_KIND",
    "INDEX_SCHEMA",
    "LegacyProfitRelativeResearchError",
    "OUTPUT_ROOT",
    "PROJECTION_KIND",
    "PROJECTION_SCHEMA",
    "PUBLIC_STATUS",
    "ROW_FIELDS",
    "SCORE_SEMANTICS",
    "SEALED_PROFIT_ARTIFACT_PATH",
    "SEALED_PROFIT_ARTIFACT_SHA256",
    "SEALED_PROFIT_MODEL_AS_OF_DATE",
    "SEALED_PROFIT_MODEL_VERSION",
    "SEALED_VALIDATION_PATH",
    "SEALED_VALIDATION_SHA256",
    "build_projection",
    "materialize_projection",
    "score_legacy_profit_relative_rows",
    "validate_index",
    "validate_projection",
    "validate_repository_chain",
    "validate_sealed_profit_model",
]
