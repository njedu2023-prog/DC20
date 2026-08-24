from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import warnings
from pathlib import Path
from typing import Any, Mapping, Sequence

import joblib
import numpy as np
import pandas as pd


DEFAULT_FIXTURE_PATH = Path(
    "tests/fixtures/decision_promotion_active_golden_vector_v1.json"
)
DEFAULT_CONTRACT_PATH = Path(
    "models/decision_executable_profit_shadow_contract.json"
)
FIXTURE_SCHEMA = "dc20_promotion_active_golden_vector_v1"
HARD_POOL_SCHEMA = "dc20_promotion_hard_pool_v1"
OUTPUT_SCHEMA = "dc20_promotion_golden_output_v1"
FEATURE_SNAPSHOT_SCHEMA = "dc20_three_engine_d_feature_snapshot_v2_quantized12"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^20\d{6}$")
CODE_RE = re.compile(r"^\d{6}\.(?:SH|SZ)$")
HARD_STAGES = {"2→3": 2, "3→4": 3}
EXPECTED_ROW_KEYS = {
    "signal_date",
    "ts_code",
    "stage",
    "stage_transition",
    "board",
    "features",
    "expected",
}
EXPECTED_OUTPUT_KEYS = {
    "raw_score",
    "calibrated_probability",
    "rank",
    "top10_selected",
}
FORBIDDEN_FIXTURE_KEYS = {
    "promotion_hit",
    "market_fill",
    "public_market_buyable_proxy",
    "big_loss_hit",
    "profit_hit",
    "net_return",
    "gross_return",
    "target",
    "label",
    "buy_date",
    "exec_date",
    "target_exit_date",
    "exit_date",
    "predicted_big_loss_probability",
    "predicted_profit_probability",
    "p_fill_shadow_probability",
}


class FrozenPromotionGoldenVectorError(ValueError):
    """Raised when the active promotion fixture or score drifts."""


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise FrozenPromotionGoldenVectorError(message)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    _expect(isinstance(value, Mapping), f"{label} must be an object")
    return value


def _sequence(value: Any, label: str) -> Sequence[Any]:
    _expect(
        isinstance(value, Sequence) and not isinstance(value, (str, bytes)),
        f"{label} must be an array",
    )
    return value


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FrozenPromotionGoldenVectorError(
            f"cannot load promotion golden-vector dependency {path}: {exc}"
        ) from exc
    _expect(isinstance(value, dict), f"{path} must contain an object")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise FrozenPromotionGoldenVectorError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _decimal(value: Any, digits: int) -> str | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    if number == 0.0:
        return "0"
    return format(number, f".{digits}g")


def _resolve_repo_file(root: Path, relative: Any, label: str) -> Path:
    _expect(isinstance(relative, str) and relative, f"{label} path is missing")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise FrozenPromotionGoldenVectorError(
            f"{label} path escaped the repository root"
        ) from exc
    _expect(path.is_file(), f"{label} file is missing: {relative}")
    return path


def _normal_signal_date(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.replace("-", "")


def _normal_ts_code(value: Any) -> str:
    return str(value or "").strip().upper()


def _verify_generation_sources(
    root: Path,
    *,
    provenance: Mapping[str, Any],
    signal_date: str,
    fixture_keys: Sequence[tuple[str, str, str]],
) -> dict[str, str]:
    candidate_path = _resolve_repo_file(
        root,
        provenance.get("candidate_source_path"),
        "golden candidate source",
    )
    candidate_sha256 = _file_sha256(candidate_path)
    _expect(
        candidate_sha256 == provenance.get("candidate_source_sha256"),
        "golden candidate source hash drifted",
    )
    try:
        candidates = pd.read_csv(
            candidate_path,
            dtype=str,
            keep_default_na=False,
            encoding="utf-8-sig",
        )
    except Exception as exc:
        raise FrozenPromotionGoldenVectorError(
            f"cannot read the golden candidate source: {exc}"
        ) from exc
    required = {
        "signal_date",
        "ts_code",
        "stage_transition",
        "mechanism_limit_pct",
    }
    _expect(
        required.issubset(candidates.columns),
        "golden candidate source is missing hard-pool identity columns",
    )
    limit_pct = pd.to_numeric(candidates["mechanism_limit_pct"], errors="coerce")
    projected = candidates.loc[
        candidates["stage_transition"].astype(str).isin(HARD_STAGES)
        & limit_pct.sub(10.0).abs().le(1e-9),
        ["signal_date", "ts_code", "stage_transition"],
    ]
    source_keys = [
        (
            _normal_signal_date(item.signal_date),
            _normal_ts_code(item.ts_code),
            str(item.stage_transition).strip(),
        )
        for item in projected.itertuples(index=False)
    ]
    _expect(
        all(key[0] == signal_date for key in source_keys),
        "golden candidate hard pool mixes signal dates",
    )
    _expect(
        len(source_keys) == len(set(source_keys)),
        "golden candidate source contains duplicate hard-pool identities",
    )
    _expect(
        sorted(source_keys) == sorted(fixture_keys),
        "golden fixture hard pool differs from the pinned candidate source",
    )

    feature_source_path = _resolve_repo_file(
        root,
        provenance.get("feature_source_path"),
        "golden feature-generation source",
    )
    feature_source_sha256 = _file_sha256(feature_source_path)
    _expect(
        feature_source_sha256 == provenance.get("feature_source_sha256"),
        "golden feature-generation source hash drifted",
    )
    return {
        "candidate_source_sha256": candidate_sha256,
        "feature_generation_source_sha256": feature_source_sha256,
    }


def _walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            keys.add(str(key))
            keys.update(_walk_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_walk_keys(item))
    return keys


def _feature_snapshot_sha256(
    frame: pd.DataFrame,
    feature_builder: Any,
) -> str:
    transformed = feature_builder.transform(frame)
    records: list[dict[str, Any]] = []
    for index, row in frame.iterrows():
        records.append(
            {
                "ts_code": str(row["ts_code"]),
                "values": {
                    name: _decimal(transformed.at[index, name], 12)
                    for name in feature_builder.feature_names
                },
            }
        )
    return _canonical_sha256(
        {
            "schema": FEATURE_SNAPSHOT_SCHEMA,
            "signal_date": str(frame["signal_date"].iloc[0]),
            "features": records,
        }
    )


def _load_model_payload(root: Path, model: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    model_path = _resolve_repo_file(root, model.get("artifact_path"), "promotion model")
    actual_sha256 = _file_sha256(model_path)
    _expect(
        actual_sha256 == model.get("artifact_sha256"),
        "promotion artifact hash drifted from the golden fixture",
    )
    src = root / "src"
    _expect(src.is_dir(), "DC20-owned source package is missing")
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Setting the shape on a NumPy array has been deprecated.*",
                category=DeprecationWarning,
                module=r"joblib\.numpy_pickle",
            )
            payload = joblib.load(model_path)
    except Exception as exc:  # joblib surfaces several persistence exceptions
        raise FrozenPromotionGoldenVectorError(
            f"cannot load the frozen promotion artifact: {exc}"
        ) from exc
    _expect(isinstance(payload, dict), "promotion artifact payload must be an object")
    return payload, actual_sha256


def validate_frozen_promotion_golden_vector(
    repo_root: Path,
    *,
    fixture_path: Path = DEFAULT_FIXTURE_PATH,
    binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate one self-contained D score snapshot against its pinned sources.

    Scoring inputs are carried by the fixture itself.  Provenance validation
    additionally hashes the exact DC20-owned candidate and feature-generation
    sources.  It never reads a recovery snapshot or another repository.
    """

    root = repo_root.resolve()
    fixture_file = _resolve_repo_file(root, fixture_path.as_posix(), "golden fixture")
    fixture_sha256 = _file_sha256(fixture_file)
    fixture = _load_json(fixture_file)

    _expect(fixture.get("schema_version") == FIXTURE_SCHEMA, "golden fixture schema drifted")
    _expect(fixture.get("status") == "ACTIVE_VERIFIED", "golden fixture is not active")
    _expect(
        not (_walk_keys(fixture) & FORBIDDEN_FIXTURE_KEYS),
        "golden fixture contains future truth, a label, or a cross-head output",
    )
    authority = _mapping(fixture.get("authority"), "fixture authority")
    _expect(
        authority.get("repository") == "njedu2023-prog/DC20"
        and authority.get("branch") == "main"
        and authority.get("owner") == "DC20",
        "golden fixture owner drifted",
    )
    _expect(
        authority.get("runtime_dependency_on_top10_decision") is False
        and authority.get("runtime_dependency_on_recovery_snapshot") is False,
        "golden fixture must remain independent from top10-decision and recovery",
    )
    serialized = fixture_file.read_text(encoding="utf-8").lower()
    _expect(
        "njedu2023-prog/top10-decision" not in serialized
        and "/recovery/" not in serialized
        and "\\recovery\\" not in serialized,
        "golden fixture contains a forbidden external or recovery dependency",
    )

    signal_date = str(fixture.get("signal_date") or "")
    _expect(DATE_RE.fullmatch(signal_date) is not None, "golden signal_date is invalid")
    provenance = _mapping(fixture.get("generation_provenance"), "generation provenance")
    _expect(
        provenance.get("contains_outcomes_or_future_labels") is False
        and provenance.get("contains_cross_head_outputs") is False,
        "golden generation provenance violates the D-close information boundary",
    )
    for field in ("candidate_source_sha256", "feature_source_sha256"):
        _expect(
            SHA256_RE.fullmatch(str(provenance.get(field) or "")) is not None,
            f"{field} is not a SHA-256",
        )
    _expect(
        provenance.get("promotion_prior_recomputation")
        == "eight promotion priors recomputed from atomic promotion_hit truth with signal_date strictly before 20260812",
        "strict lagged promotion-prior provenance drifted",
    )
    _expect(
        provenance.get("hard_pool_projection")
        == "stage_transition in {2→3,3→4} and mechanism_limit_pct == 10"
        and provenance.get("feature_row_binding")
        == "exact (signal_date, ts_code) identity match to the authoritative candidate hard pool",
        "golden generation-source projection contract drifted",
    )

    model = _mapping(fixture.get("model"), "fixture model")
    _expect(
        model.get("head") == "promotion"
        and model.get("status") == "READY"
        and model.get("promoted") is True,
        "fixture is not bound to the active READY promotion head",
    )
    _expect(
        SHA256_RE.fullmatch(str(model.get("artifact_sha256") or "")) is not None,
        "fixture promotion artifact SHA-256 is invalid",
    )
    payload, artifact_sha256 = _load_model_payload(root, model)
    for field in (
        "head",
        "status",
        "promoted",
        "model_version",
        "model_as_of_date",
        "feature_contract",
        "runtime_feature_contract_version",
    ):
        _expect(
            payload.get(field) == model.get(field),
            f"promotion artifact {field} drifted from the golden fixture",
        )
    bundle = payload.get("bundle")
    _expect(bundle is not None and hasattr(bundle, "predict_components"), "promotion bundle is missing")
    feature_builder = getattr(bundle, "feature_builder", None)
    _expect(feature_builder is not None, "promotion feature builder is missing")

    contract = _mapping(fixture.get("contract"), "fixture contract")
    _expect(
        contract.get("hard_stage_scope") == ["2→3", "3→4"]
        and contract.get("top_n") == 10,
        "hard-stage or Top10 fixture contract drifted",
    )
    raw_names = list(_sequence(contract.get("raw_numeric_feature_names"), "raw feature names"))
    _expect(len(raw_names) == len(set(raw_names)) == 44, "golden raw feature inventory drifted")
    _expect(
        tuple(raw_names) == tuple(feature_builder.numeric_columns),
        "golden raw features drifted from the active promotion artifact",
    )
    _expect(
        contract.get("raw_numeric_feature_count") == len(raw_names)
        and contract.get("model_feature_count") == len(feature_builder.feature_names),
        "golden feature counts drifted",
    )

    row_values = list(_sequence(fixture.get("rows"), "golden rows"))
    _expect(len(row_values) == contract.get("pool_size") == 13, "golden hard-pool size drifted")
    rows: list[dict[str, Any]] = []
    keys: list[tuple[str, str, str]] = []
    stage_counts = {"2→3": 0, "3→4": 0}
    for position, value in enumerate(row_values):
        row = _mapping(value, f"golden row {position}")
        _expect(set(row) == EXPECTED_ROW_KEYS, f"golden row {position} schema drifted")
        date = str(row.get("signal_date") or "")
        code = str(row.get("ts_code") or "")
        transition = str(row.get("stage_transition") or "")
        stage = row.get("stage")
        board = str(row.get("board") or "")
        _expect(date == signal_date, "golden rows mix signal dates")
        _expect(CODE_RE.fullmatch(code) is not None, f"invalid golden ts_code: {code}")
        _expect(transition in HARD_STAGES, "golden row escaped the hard-stage scope")
        _expect(type(stage) is int and stage == HARD_STAGES[transition], "stage transition drifted")
        expected_board = "SH_MAIN" if code.endswith(".SH") else "SZ_MAIN"
        _expect(board == expected_board, "golden board disagrees with ts_code")
        features = _mapping(row.get("features"), f"golden features {code}")
        _expect(set(features) == set(raw_names), f"golden feature inventory drifted for {code}")
        numeric: dict[str, float] = {}
        for name in raw_names:
            decimal = _decimal(features.get(name), 17)
            _expect(decimal is not None, f"golden feature {name} is not finite for {code}")
            numeric[name] = float(features[name])
        expected = _mapping(row.get("expected"), f"golden expected output {code}")
        _expect(set(expected) == EXPECTED_OUTPUT_KEYS, f"golden expected schema drifted for {code}")
        _expect(
            _decimal(expected.get("raw_score"), 12) == expected.get("raw_score")
            and _decimal(expected.get("calibrated_probability"), 12)
            == expected.get("calibrated_probability"),
            f"golden expected decimals are not canonical for {code}",
        )
        _expect(
            type(expected.get("rank")) is int
            and type(expected.get("top10_selected")) is int,
            f"golden expected rank fields are invalid for {code}",
        )
        keys.append((date, code, transition))
        stage_counts[transition] += 1
        rows.append(
            {
                "signal_date": date,
                "ts_code": code,
                "stage": stage,
                "stage_transition": transition,
                "board": board,
                **numeric,
                "expected": dict(expected),
            }
        )
    _expect(len(keys) == len(set(keys)), "golden hard pool contains duplicate identities")
    _expect(stage_counts == contract.get("stage_counts"), "golden stage counts drifted")
    ordered = sorted(zip(keys, rows), key=lambda item: item[0])
    keys = [key for key, _ in ordered]
    rows = [row for _, row in ordered]
    generation_sources = _verify_generation_sources(
        root,
        provenance=provenance,
        signal_date=signal_date,
        fixture_keys=keys,
    )

    bindings = _mapping(fixture.get("bindings"), "fixture bindings")
    hard_pool_sha256 = _canonical_sha256(
        {
            "schema": HARD_POOL_SCHEMA,
            "identities": [list(key) for key in keys],
        }
    )
    _expect(
        bindings.get("authoritative_hard_pool_sha256") == hard_pool_sha256,
        "authoritative hard-pool hash drifted",
    )
    frame = pd.DataFrame([{k: v for k, v in row.items() if k != "expected"} for row in rows])
    feature_snapshot_sha256 = _feature_snapshot_sha256(frame, feature_builder)
    _expect(
        bindings.get("feature_snapshot_sha256") == feature_snapshot_sha256,
        "golden feature snapshot hash drifted",
    )
    try:
        probability, raw = bundle.predict_components(frame)
        probability_second, raw_second = bundle.predict_components(frame)
    except Exception as exc:
        raise FrozenPromotionGoldenVectorError(f"active promotion scoring failed: {exc}") from exc
    _expect(
        np.array_equal(probability, probability_second)
        and np.array_equal(raw, raw_second),
        "active promotion scoring is not deterministic",
    )
    scored = pd.DataFrame(
        {
            "ts_code": frame["ts_code"].astype(str),
            "stage_transition": frame["stage_transition"].astype(str),
            "raw_score": raw,
            "calibrated_probability": probability,
        }
    ).sort_values(
        ["calibrated_probability", "raw_score", "ts_code"],
        ascending=[False, False, True],
        kind="stable",
    ).reset_index(drop=True)
    scored["rank"] = np.arange(1, len(scored) + 1)
    scored["top10_selected"] = scored["rank"].le(10).astype(int)
    expected_by_code = {row["ts_code"]: row["expected"] for row in rows}
    output_rows: list[dict[str, Any]] = []
    for item in scored.itertuples(index=False):
        actual = {
            "ts_code": str(item.ts_code),
            "stage_transition": str(item.stage_transition),
            "raw_score": _decimal(item.raw_score, 12),
            "calibrated_probability": _decimal(item.calibrated_probability, 12),
            "rank": int(item.rank),
            "top10_selected": int(item.top10_selected),
        }
        expected = expected_by_code[actual["ts_code"]]
        _expect(
            {key: actual[key] for key in EXPECTED_OUTPUT_KEYS} == expected,
            f"active promotion output drifted for {actual['ts_code']}",
        )
        output_rows.append(actual)
    golden_output_sha256 = _canonical_sha256(
        {
            "schema": OUTPUT_SCHEMA,
            "signal_date": signal_date,
            "rows": output_rows,
        }
    )
    _expect(
        bindings.get("golden_output_sha256") == golden_output_sha256,
        "golden output hash drifted",
    )

    if binding is not None:
        expected_binding = _mapping(binding, "contract golden-vector binding")
        comparisons = {
            "status": "ACTIVE_VERIFIED",
            "fixture_path": fixture_path.as_posix(),
            "fixture_sha256": fixture_sha256,
            "signal_date": signal_date,
            "candidate_source_path": provenance.get("candidate_source_path"),
            "candidate_source_sha256": generation_sources[
                "candidate_source_sha256"
            ],
            "feature_generation_source_path": provenance.get(
                "feature_source_path"
            ),
            "feature_generation_source_sha256": generation_sources[
                "feature_generation_source_sha256"
            ],
            "strict_prior_rule": provenance.get("promotion_prior_recomputation"),
            "model_artifact_path": model.get("artifact_path"),
            "model_artifact_sha256": artifact_sha256,
            "model_version": model.get("model_version"),
            "authoritative_hard_pool_sha256": hard_pool_sha256,
            "feature_snapshot_sha256": feature_snapshot_sha256,
            "golden_output_sha256": golden_output_sha256,
            "pool_size": len(rows),
            "top_n": 10,
            "runtime_dependency_on_top10_decision": False,
            "runtime_dependency_on_recovery_snapshot": False,
            "contains_outcomes_or_future_labels": False,
            "contains_cross_head_outputs": False,
        }
        for field, expected_value in comparisons.items():
            _expect(
                expected_binding.get(field) == expected_value,
                f"contract golden-vector {field} drifted",
            )
        _expect(
            expected_binding.get("stage_counts") == stage_counts,
            "contract golden-vector stage_counts drifted",
        )

    return {
        "valid": True,
        "fixture_id": fixture.get("fixture_id"),
        "fixture_sha256": fixture_sha256,
        "signal_date": signal_date,
        "candidate_source_sha256": generation_sources["candidate_source_sha256"],
        "feature_generation_source_sha256": generation_sources[
            "feature_generation_source_sha256"
        ],
        "promotion_artifact_sha256": artifact_sha256,
        "authoritative_hard_pool_sha256": hard_pool_sha256,
        "feature_snapshot_sha256": feature_snapshot_sha256,
        "golden_output_sha256": golden_output_sha256,
        "pool_size": len(rows),
        "stage_counts": stage_counts,
        "top_n": 10,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the active frozen DC20 promotion golden vector."
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE_PATH)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT_PATH)
    args = parser.parse_args()
    try:
        contract = _load_json(args.repo_root.resolve() / args.contract)
        identity = _mapping(contract.get("promotion_identity"), "promotion identity")
        binding = _mapping(identity.get("golden_vector"), "promotion golden vector")
        result = validate_frozen_promotion_golden_vector(
            args.repo_root,
            fixture_path=args.fixture,
            binding=binding,
        )
    except FrozenPromotionGoldenVectorError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
