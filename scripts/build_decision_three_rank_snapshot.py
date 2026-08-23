#!/usr/bin/env python3
"""Build the immutable DC20 three-rank recovery snapshot for D=20260821.

The command has two deliberately separate inputs:

* the exact, reviewed DC20 candidate archive and its source metadata; and
* DC20-owned recovery bars normalized from the reviewed public Tencent cache.

Once the recovery directory has been materialized, the public cache is no
longer a runtime dependency.  Every bar is cut at D before persistence and the
production D-close feature function, production promotion-context function,
hash-bound model loader/scorer, and public three-rank contract are reused.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from top10decision.auction_v3.config import AuctionV3Config  # noqa: E402
from top10decision.auction_v3.engine import (  # noqa: E402
    AuctionV3Engine,
    _round_price,
)
from top10decision.auction_v3.promotion_model import (  # noqa: E402
    PROMOTION_CONTEXT_FEATURES,
)
from top10decision.decision.d_close_features import (  # noqa: E402
    D_CLOSE_FEATURE_COLUMNS,
    D_CLOSE_FEATURE_CONTRACT_VERSION,
    compute_d_close_features,
)
from top10decision.decision.three_engine_models import (  # noqa: E402
    load_research_only_legacy_three_engine_snapshot,
    score_three_engine_snapshot,
)
from top10decision.decision.three_rank import (  # noqa: E402
    build_three_rank_contract,
    materialize_three_rank_artifacts,
    validate_three_rank_contract,
)


SIGNAL_DATE = "20260821"
EXEC_DATE = "20260824"
EXIT_DATE = "20260825"
SOURCE_PATH = "data/pred/archive/pred_source_20260821.csv"
SOURCE_META_PATH = "data/pred/_pred_source_meta.json"
RECOVERY_ROOT = "data/decision_three_engines/recovery/20260821"
RECOVERY_MANIFEST_PATH = f"{RECOVERY_ROOT}/manifest.json"
RECOVERY_SOURCE_PATH = f"{RECOVERY_ROOT}/source_candidates.csv"
RECOVERY_SOURCE_META_PATH = f"{RECOVERY_ROOT}/source_meta.json"
RECOVERY_STOCK_PRIORS_PATH = f"{RECOVERY_ROOT}/stock_priors.csv"
VALIDATION_PATH = "models/decision_three_engines/validation_latest.json"
DATA_VALIDATION_PATH = "models/decision_three_engine_data_validation.json"
LEDGER_PATH = "data/decision_three_engines/five_year_supervised_ledger.csv.gz"
LEDGER_MANIFEST_PATH = "data/decision_three_engines/five_year_ledger_manifest.json"
OOF_TOP10_PATH = "outputs/auction_v3/metrics/three_engine_oof_top10_latest.csv.gz"
RECOVERY_MODEL_ROOT = f"{RECOVERY_ROOT}/model_snapshot"
RECOVERY_VALIDATION_PATH = f"{RECOVERY_MODEL_ROOT}/validation.json"
RECOVERY_DATA_VALIDATION_PATH = f"{RECOVERY_MODEL_ROOT}/data_validation.json"
RECOVERY_LEDGER_PATH = f"{RECOVERY_MODEL_ROOT}/five_year_supervised_ledger.csv.gz"
RECOVERY_LEDGER_MANIFEST_PATH = f"{RECOVERY_MODEL_ROOT}/five_year_ledger_manifest.json"
RECOVERY_OOF_TOP10_PATH = f"{RECOVERY_MODEL_ROOT}/three_engine_oof_top10.csv.gz"
RECOVERY_ARTIFACT_PATHS = {
    head: f"{RECOVERY_MODEL_ROOT}/{head}.joblib"
    for head in ("promotion", "big_loss", "profit", "p_fill_shadow")
}
EVIDENCE_PATH = "outputs/decision/three_rank_top10_20260821.evidence.json"
PROMOTION_EVENT_SOURCE_PATH = (
    "data/auction_v3/promotion_prior/five_year_event_features.csv.gz"
)
EXPECTED_PROMOTION_EVENT_SOURCE_SHA256 = (
    "57683c2ee65de2b9debd6c7ca253c5ef18e393c121b943884bc577054cd7fe3e"
)

EXPECTED_BINDINGS = {
    SOURCE_PATH: "7934e1fd038801b3c95d646c38c3dfe1d3ade28e44bf735749f10557d220fada",
    SOURCE_META_PATH: "59cc25c876178d98b2c74638706b549c2ceb8b9a28de62a91dcf6a09cf370695",
    VALIDATION_PATH: "ea29b6da156162c910d774ee255756690feb9591f4deed6154eca83b1aeb4ed8",
    LEDGER_PATH: (
        "76f7403ec909d387b222e643be26ba5c918fe8da779788876baf6f8f8dea5026"
    ),
    LEDGER_MANIFEST_PATH: (
        "587092feca1b612d9cdec327ffc83f4cd82b0cc38b778922b8225a8d3dbdcbfc"
    ),
    "models/decision_three_engines/promotion.joblib": (
        "72dcbc139c3260a99b9dd6846403a2acd9ebeef8a518cb7b3ddfc75e52b81e5b"
    ),
    "models/decision_three_engines/big_loss.joblib": (
        "9a3ba655f2026fba80cdf73b904278d0bc730c559f8d38eb14d8d547fd8409c6"
    ),
    "models/decision_three_engines/profit.joblib": (
        "0e5e251dc0632ed120baf7e758a4cbfcebd940857fab62e71c57f3c1979891f3"
    ),
    "models/decision_three_engines/p_fill_shadow.joblib": (
        "1b7c52b6e7270e98c25c19d2ccd8131cb97e902aaff62abb059232c086b54f06"
    ),
    OOF_TOP10_PATH: (
        "df5b57877fd29c6a4c64a45061a42357144151994cc83bb5ee8a2b2d71f5244b"
    ),
}
EXPECTED_PREPARATION_DATA_VALIDATION_SHA256 = (
    "352bdcd2ed2280348532117ea2d3c363f051d52707e5f68c35347e1c1a8d632f"
)
# Rewritten validation/manifest hashes are filled from the deterministic
# one-time sealer and then treated as immutable historical inputs.
EXPECTED_SEALED_RUNTIME_BINDINGS = {
    RECOVERY_VALIDATION_PATH: (
        "99f89e8bbc40d0f6cc39c3312039156a79c4f45e24114fc4affb900f23a46fe4"
    ),
    RECOVERY_DATA_VALIDATION_PATH: (
        "34ab2950fa2e6226173392fa4a17cab5f9f102fe2d10a01c3b2175346b2323da"
    ),
    RECOVERY_LEDGER_PATH: EXPECTED_BINDINGS[LEDGER_PATH],
    RECOVERY_LEDGER_MANIFEST_PATH: (
        "18042d3d6f81d2b86fe6e50b97cb5849512db98de959e493049e3c7c9226524d"
    ),
    RECOVERY_OOF_TOP10_PATH: EXPECTED_BINDINGS[OOF_TOP10_PATH],
    **{
        path: EXPECTED_BINDINGS[f"models/decision_three_engines/{head}.joblib"]
        for head, path in RECOVERY_ARTIFACT_PATHS.items()
    },
}
RUNTIME_BINDING_PATHS = tuple(EXPECTED_SEALED_RUNTIME_BINDINGS)

EXPECTED_HARD_POOL = (
    (4, "002491.SZ", "通鼎互联", "通信设备", "2→3"),
    (6, "000017.SZ", "深中华A", "饰品", "2→3"),
    (11, "603958.SH", "哈森股份", "服装家纺", "2→3"),
    (18, "002412.SZ", "汉森制药", "中药Ⅱ", "3→4"),
    (19, "603626.SH", "科森科技", "消费电子", "2→3"),
    (23, "002903.SZ", "宇环数控", "通用设备", "2→3"),
    (25, "000710.SZ", "贝瑞基因", "医疗服务", "2→3"),
    (26, "002038.SZ", "双鹭药业", "化学制药", "2→3"),
    (28, "000931.SZ", "中关村", "化学制药", "2→3"),
)

EXPECTED_TENCENT_SHA256 = {
    "002491.SZ": "ffc50749cb2c8d6de09294591b77e35a888e776e1e87073529a7b9996b6f3f96",
    "000017.SZ": "458d13bfeb2919233a15e431974d2b80f9ebccc351dd57cd316846f7012780c7",
    "603958.SH": "06c44148981bad968871a7b6fdec2a1b627151dd0a7d63b86cbc9f90daa254e3",
    "002412.SZ": "90bbc565eccfcc11b673d3614fc640df8458c02ecbbe782ba4cfc12c313f9ac6",
    "603626.SH": "9ac930ed4f134f4be9c74397142580533cd5b3df914c06ccfd048152c46f14bd",
    "002903.SZ": "9c9e1a49609b0c4c8dfc24b52687dc9d9aba5a2b02e512575b3c1baf60c37830",
    "000710.SZ": "e245cdf4e32a6db0fac393f1dda015ea43fa9d448a053879fc001d6363fbc01c",
    "002038.SZ": "f670775613077237540c38942461054ac7e548b0c753efee2e2b3f4019390151",
    "000931.SZ": "b24db75925b48d82ded2ab75a5170b3cb193d33099e52ab202d59cac9f5d050c",
}

DATE_RE = re.compile(r"^20\d{6}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class ThreeRankSnapshotError(RuntimeError):
    """Raised before an unbound or future-leaking input can be published."""


def _fail(message: str) -> None:
    raise ThreeRankSnapshotError(message)


def _sha256(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        _fail(f"required regular file is missing or unsafe: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    kwargs: dict[str, Any] = {
        "ensure_ascii": False,
        "allow_nan": False,
        "sort_keys": True,
    }
    if pretty:
        kwargs["indent"] = 2
    else:
        kwargs["separators"] = (",", ":")
    return (json.dumps(value, **kwargs) + ("\n" if pretty else "")).encode(
        "utf-8"
    )


def _mapping_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ThreeRankSnapshotError(f"cannot read {label}: {path}") from exc
    if not isinstance(payload, dict):
        _fail(f"{label} must be a JSON object")
    return payload


def _write_immutable(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.is_symlink():
            _fail(f"immutable path is not a safe regular file: {path}")
        if path.read_bytes() != payload:
            _fail(f"immutable dated input/artifact drifted: {path}")
        return
    handle = tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _csv_bytes(
    frame: pd.DataFrame,
    *,
    bom: bool = False,
    float_format: str = "%.10g",
) -> bytes:
    payload = frame.to_csv(
        index=False,
        lineterminator="\n",
        float_format=float_format,
    ).encode("utf-8")
    return (b"\xef\xbb\xbf" if bom else b"") + payload


def _gzip_bytes(payload: bytes) -> bytes:
    stream = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", fileobj=stream, compresslevel=9, mtime=0
    ) as handle:
        handle.write(payload)
    return stream.getvalue()


def _normal_date(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _normal_text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _compact_name(value: Any) -> str:
    return re.sub(r"\s+", "", _normal_text(value))


def validate_static_bindings(root: Path | str) -> dict[str, str]:
    root_path = Path(root).resolve()
    actual: dict[str, str] = {}
    for relative in RUNTIME_BINDING_PATHS:
        expected = EXPECTED_SEALED_RUNTIME_BINDINGS[relative]
        digest = _sha256(root_path / relative)
        if digest != expected:
            _fail(
                f"sealed historical model/ledger SHA drifted for {relative}: "
                f"expected {expected}, got {digest}"
            )
        actual[relative] = digest

    validation = _mapping_json(
        root_path / RECOVERY_VALIDATION_PATH, "sealed model validation"
    )
    source = validation.get("source")
    artifacts = validation.get("artifacts")
    if not isinstance(source, Mapping) or not isinstance(artifacts, Mapping):
        _fail("model validation lacks source/artifact bindings")
    expected_ledger = EXPECTED_SEALED_RUNTIME_BINDINGS[RECOVERY_LEDGER_PATH]
    if (
        source.get("ledger_path") != RECOVERY_LEDGER_PATH
        or source.get("ledger_sha256") != expected_ledger
        or source.get("ledger_manifest_path") != RECOVERY_LEDGER_MANIFEST_PATH
        or source.get("ledger_manifest_sha256")
        != EXPECTED_SEALED_RUNTIME_BINDINGS[RECOVERY_LEDGER_MANIFEST_PATH]
    ):
        _fail("model validation ledger SHA is not the reviewed ledger")
    if source.get("end") != "20260814":
        _fail("model validation as-of source date drifted")
    for head in ("promotion", "big_loss", "profit", "p_fill_shadow"):
        record = artifacts.get(head)
        path = RECOVERY_ARTIFACT_PATHS[head]
        if not isinstance(record, Mapping) or record.get("path") != path:
            _fail(f"model validation {head} path drifted")
        if record.get("sha256") != EXPECTED_SEALED_RUNTIME_BINDINGS[path]:
            _fail(f"model validation {head} SHA drifted")
    oof_record = artifacts.get("oof_top10")
    if (
        not isinstance(oof_record, Mapping)
        or oof_record.get("path") != RECOVERY_OOF_TOP10_PATH
        or oof_record.get("sha256")
        != EXPECTED_SEALED_RUNTIME_BINDINGS[RECOVERY_OOF_TOP10_PATH]
    ):
        _fail("sealed model validation OOF Top10 binding drifted")
    data_validation = _mapping_json(
        root_path / RECOVERY_DATA_VALIDATION_PATH, "sealed data validation"
    )
    inputs = data_validation.get("inputs")
    if not isinstance(inputs, Mapping) or inputs.get("ledger") != {
        "path": RECOVERY_LEDGER_PATH,
        "sha256": expected_ledger,
    } or inputs.get("manifest") != {
        "path": RECOVERY_LEDGER_MANIFEST_PATH,
        "sha256": EXPECTED_SEALED_RUNTIME_BINDINGS[
            RECOVERY_LEDGER_MANIFEST_PATH
        ],
    }:
        _fail("sealed data validation input bindings are not self-contained")
    return actual


def load_bound_candidate_pool(
    root: Path | str,
    *,
    recovery_only: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    root_path = Path(root).resolve()
    source_relative = RECOVERY_SOURCE_PATH if recovery_only else SOURCE_PATH
    meta_relative = RECOVERY_SOURCE_META_PATH if recovery_only else SOURCE_META_PATH
    source_path = root_path / source_relative
    meta_path = root_path / meta_relative
    for relative, path, expected_key in (
        (source_relative, source_path, SOURCE_PATH),
        (meta_relative, meta_path, SOURCE_META_PATH),
    ):
        digest = _sha256(path)
        expected = EXPECTED_BINDINGS[expected_key]
        if digest != expected:
            _fail(
                f"reviewed candidate source drifted for {relative}: "
                f"expected {expected}, got {digest}"
            )
    meta = _mapping_json(meta_path, "candidate source metadata")
    if (
        meta.get("sha256") != EXPECTED_BINDINGS[SOURCE_PATH]
        or meta.get("body_sha256") != EXPECTED_BINDINGS[SOURCE_PATH]
        or _normal_date(meta.get("resolved_trade_date")) != SIGNAL_DATE
        or _normal_date((meta.get("csv_profile") or {}).get("target_trade_date"))
        != EXEC_DATE
    ):
        _fail("candidate source metadata disagrees with reviewed D/T binding")

    try:
        source = pd.read_csv(source_path, encoding="utf-8-sig")
    except (OSError, UnicodeError, pd.errors.ParserError) as exc:
        raise ThreeRankSnapshotError("cannot parse reviewed candidate source") from exc
    required = {"trade_date", "verify_date", "rank", "ts_code", "name", "board"}
    if not required.issubset(source.columns):
        _fail(f"candidate source lacks columns: {sorted(required - set(source.columns))}")
    stage = pd.Series("", index=source.index, dtype=object)
    for column in ("advance_stage", "晋阶"):
        if column in source.columns:
            candidate = source[column].fillna("").astype(str).str.replace(
                "->", "→", regex=False
            )
            stage = stage.where(stage.ne(""), candidate)
    source["stage_transition"] = stage
    source["trade_date"] = source["trade_date"].map(_normal_date)
    source["verify_date"] = source["verify_date"].map(_normal_date)
    source["source_rank"] = pd.to_numeric(source["rank"], errors="coerce")
    source["ts_code"] = source["ts_code"].fillna("").astype(str).str.strip().str.upper()
    source["name"] = source["name"].map(_compact_name)
    source["industry"] = source["board"].map(_normal_text)
    pool = source[source["stage_transition"].isin(("2→3", "3→4"))].copy()
    if (
        not pool["trade_date"].eq(SIGNAL_DATE).all()
        or not pool["verify_date"].eq(EXEC_DATE).all()
        or pool["source_rank"].isna().any()
    ):
        _fail("hard pool escaped reviewed D/T/rank binding")
    pool["source_rank"] = pool["source_rank"].astype(int)
    pool["stage"] = pool["stage_transition"].map({"2→3": 2, "3→4": 3})
    pool["market_board"] = np.where(
        pool["ts_code"].str.endswith(".SH"), "SH_MAIN", "SZ_MAIN"
    )
    pool["mechanism_limit_pct"] = 10.0
    pool = pool.sort_values(["source_rank", "ts_code"], kind="stable")
    actual = tuple(
        (
            int(row.source_rank),
            str(row.ts_code),
            str(row.name),
            str(row.industry),
            str(row.stage_transition),
        )
        for row in pool.itertuples(index=False)
    )
    if actual != EXPECTED_HARD_POOL:
        _fail(f"reviewed hard pool drifted: {actual!r}")
    normalized = pool[
        [
            "trade_date",
            "verify_date",
            "source_rank",
            "ts_code",
            "name",
            "industry",
            "stage",
            "stage_transition",
            "market_board",
            "mechanism_limit_pct",
        ]
    ].rename(columns={"trade_date": "signal_date", "verify_date": "exec_date"})
    normalized.insert(2, "exit_date", EXIT_DATE)
    normalized = normalized.reset_index(drop=True)
    binding = {
        "source_path": source_relative,
        "source_sha256": EXPECTED_BINDINGS[SOURCE_PATH],
        "source_meta_path": meta_relative,
        "source_meta_sha256": EXPECTED_BINDINGS[SOURCE_META_PATH],
        "source_repository": _normal_text(meta.get("source_repository")),
        "resolved_commit": _normal_text(meta.get("resolved_commit")),
        "source_ref": _normal_text(meta.get("source_ref")),
        "generated_at_utc": _normal_text(meta.get("created_at_utc")),
    }
    return normalized, binding


def _tencent_cache_path(cache_root: Path, code: str) -> Path:
    return cache_root / f"{code.replace('.', '_')}_20210709_20260825.json.gz"


def normalize_tencent_payload(
    payload: Mapping[str, Any],
    *,
    code: str,
    cutoff_date: str = SIGNAL_DATE,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not DATE_RE.fullmatch(cutoff_date):
        _fail("Tencent recovery cutoff is invalid")
    data = payload.get("data")
    if not isinstance(data, Mapping) or len(data) != 1:
        _fail(f"Tencent payload for {code} has invalid data envelope")
    item = next(iter(data.values()))
    raw_days = item.get("day") if isinstance(item, Mapping) else None
    if not isinstance(raw_days, list) or not raw_days:
        _fail(f"Tencent payload for {code} has no daily bars")
    records: list[dict[str, Any]] = []
    annotated_rows = 0
    later_rows = 0
    raw_dates: list[str] = []
    for raw in raw_days:
        if not isinstance(raw, list) or len(raw) < 6:
            _fail(f"Tencent payload for {code} contains a malformed bar")
        date = _normal_date(raw[0])
        if not DATE_RE.fullmatch(date):
            _fail(f"Tencent payload for {code} contains an invalid date")
        raw_dates.append(date)
        annotated_rows += int(len(raw) > 6)
        if date > cutoff_date:
            later_rows += 1
            continue
        records.append(
            {
                "trade_date": date,
                "ts_code": code,
                "open": raw[1],
                "close": raw[2],
                "high": raw[3],
                "low": raw[4],
                "volume": raw[5],
            }
        )
    bars = pd.DataFrame(records)
    for column in ("open", "close", "high", "low", "volume"):
        bars[column] = pd.to_numeric(bars[column], errors="coerce")
    if (
        bars.empty
        or bars.duplicated("trade_date").any()
        or bars[["open", "close", "high", "low", "volume"]].isna().any().any()
        or not bars["trade_date"].is_monotonic_increasing
        or bars["trade_date"].max() > cutoff_date
        or not bars["trade_date"].eq(cutoff_date).any()
    ):
        _fail(f"normalized Tencent bars for {code} violate D-close contract")
    bars["pre_close"] = bars["close"].shift(1)
    audit = {
        "raw_rows": len(raw_days),
        "normalized_rows": len(bars),
        "raw_start_date": min(raw_dates),
        "raw_max_date": max(raw_dates),
        "normalized_start_date": str(bars["trade_date"].min()),
        "normalized_max_date": str(bars["trade_date"].max()),
        "discarded_after_cutoff_rows": later_rows,
        "corporate_action_annotation_rows_stripped": annotated_rows,
    }
    return bars, audit


def load_bound_tencent_bars(
    cache_root: Path | str,
    code: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    cache_path = _tencent_cache_path(Path(cache_root).resolve(), code)
    digest = _sha256(cache_path)
    expected = EXPECTED_TENCENT_SHA256.get(code)
    if digest != expected:
        _fail(
            f"reviewed Tencent cache SHA drifted for {code}: "
            f"expected {expected}, got {digest}"
        )
    try:
        with gzip.open(cache_path, mode="rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ThreeRankSnapshotError(
            f"cannot read reviewed Tencent cache for {code}"
        ) from exc
    if not isinstance(payload, Mapping) or payload.get("code") != 0:
        _fail(f"Tencent cache for {code} has unsuccessful status")
    bars, audit = normalize_tencent_payload(payload, code=code)
    return bars, {**audit, "raw_sha256": digest}


def _prepare_stock_priors(root: Path, pool: pd.DataFrame) -> tuple[bytes, dict[str, Any]]:
    source_path = root / PROMOTION_EVENT_SOURCE_PATH
    digest = _sha256(source_path)
    if digest != EXPECTED_PROMOTION_EVENT_SOURCE_SHA256:
        _fail(
            "reviewed promotion-event source SHA drifted: "
            f"expected {EXPECTED_PROMOTION_EVENT_SOURCE_SHA256}, got {digest}"
        )
    try:
        events = pd.read_csv(
            source_path,
            compression="gzip",
            usecols=[
                "signal_date",
                "ts_code",
                "five_year_stock_prior_rate",
                "five_year_stock_prior_samples_log",
            ],
        )
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        raise ThreeRankSnapshotError("cannot parse reviewed stock-prior source") from exc
    events["signal_date"] = events["signal_date"].map(_normal_date)
    events["ts_code"] = events["ts_code"].fillna("").astype(str).str.upper()
    events = events[
        events["signal_date"].lt(SIGNAL_DATE)
        & events["ts_code"].isin(pool["ts_code"])
    ].copy()
    events = (
        events.sort_values(["ts_code", "signal_date"], kind="stable")
        .groupby("ts_code", as_index=False, sort=False)
        .tail(1)
        .rename(columns={"signal_date": "prior_as_of_date"})
        .sort_values("ts_code", kind="stable")
        .reset_index(drop=True)
    )
    if (
        len(events) != len(pool)
        or set(events["ts_code"]) != set(pool["ts_code"])
        or not events["prior_as_of_date"].lt(SIGNAL_DATE).all()
        or events[
            ["five_year_stock_prior_rate", "five_year_stock_prior_samples_log"]
        ]
        .isna()
        .any()
        .any()
    ):
        _fail("reviewed stock-prior source does not cover the exact hard pool")
    payload = _csv_bytes(events, float_format="%.17g")
    return payload, {
        "path": RECOVERY_STOCK_PRIORS_PATH,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "rows": len(events),
        "truth_cutoff_rule": "latest source row strictly before signal_date",
        "preparation_source_path": PROMOTION_EVENT_SOURCE_PATH,
        "preparation_source_sha256": digest,
    }


def _prepare_model_snapshot(root: Path) -> dict[str, Any]:
    preparation_paths = (
        VALIDATION_PATH,
        LEDGER_PATH,
        LEDGER_MANIFEST_PATH,
        OOF_TOP10_PATH,
        *(f"models/decision_three_engines/{head}.joblib" for head in RECOVERY_ARTIFACT_PATHS),
    )
    for relative in preparation_paths:
        digest = _sha256(root / relative)
        if digest != EXPECTED_BINDINGS[relative]:
            _fail(
                f"one-time model snapshot source drifted for {relative}: "
                f"expected {EXPECTED_BINDINGS[relative]}, got {digest}"
            )
    if _sha256(root / DATA_VALIDATION_PATH) != (
        EXPECTED_PREPARATION_DATA_VALIDATION_SHA256
    ):
        _fail("one-time data-validation source drifted")

    ledger_payload = (root / LEDGER_PATH).read_bytes()
    _write_immutable(root / RECOVERY_LEDGER_PATH, ledger_payload)
    oof_payload = (root / OOF_TOP10_PATH).read_bytes()
    _write_immutable(root / RECOVERY_OOF_TOP10_PATH, oof_payload)

    source_manifest = _mapping_json(root / LEDGER_MANIFEST_PATH, "ledger manifest")
    sealed_manifest = json.loads(json.dumps(source_manifest))
    sealed_manifest["ledger_path"] = RECOVERY_LEDGER_PATH
    sealed_manifest_payload = _canonical_json_bytes(sealed_manifest, pretty=True)
    _write_immutable(root / RECOVERY_LEDGER_MANIFEST_PATH, sealed_manifest_payload)
    sealed_manifest_sha = hashlib.sha256(sealed_manifest_payload).hexdigest()

    source_data_validation = _mapping_json(
        root / DATA_VALIDATION_PATH, "data validation"
    )
    sealed_data_validation = json.loads(json.dumps(source_data_validation))
    sealed_inputs = sealed_data_validation.get("inputs")
    if not isinstance(sealed_inputs, dict):
        _fail("one-time data validation lacks input bindings")
    sealed_inputs["ledger"] = {
        "path": RECOVERY_LEDGER_PATH,
        "sha256": hashlib.sha256(ledger_payload).hexdigest(),
    }
    sealed_inputs["manifest"] = {
        "path": RECOVERY_LEDGER_MANIFEST_PATH,
        "sha256": sealed_manifest_sha,
    }
    data_validation_payload = _canonical_json_bytes(
        sealed_data_validation, pretty=True
    )
    _write_immutable(root / RECOVERY_DATA_VALIDATION_PATH, data_validation_payload)

    artifact_records: dict[str, dict[str, Any]] = {}
    for head, sealed_path in RECOVERY_ARTIFACT_PATHS.items():
        source_path = f"models/decision_three_engines/{head}.joblib"
        payload = (root / source_path).read_bytes()
        _write_immutable(root / sealed_path, payload)
        artifact_records[head] = {
            "path": sealed_path,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "one_time_source_path": source_path,
        }

    source_validation = _mapping_json(root / VALIDATION_PATH, "model validation")
    sealed_validation = json.loads(json.dumps(source_validation))
    sealed_source = sealed_validation.get("source")
    sealed_artifacts = sealed_validation.get("artifacts")
    if not isinstance(sealed_source, dict) or not isinstance(sealed_artifacts, dict):
        _fail("one-time model validation lacks source/artifact records")
    sealed_source["ledger_path"] = RECOVERY_LEDGER_PATH
    sealed_source["ledger_manifest_path"] = RECOVERY_LEDGER_MANIFEST_PATH
    sealed_source["ledger_manifest_sha256"] = sealed_manifest_sha
    for head, record in artifact_records.items():
        if not isinstance(sealed_artifacts.get(head), dict):
            _fail(f"one-time model validation lacks {head} artifact")
        sealed_artifacts[head]["path"] = record["path"]
    oof_record = sealed_artifacts.get("oof_top10")
    if not isinstance(oof_record, dict):
        _fail("one-time model validation lacks OOF Top10 artifact")
    oof_record["path"] = RECOVERY_OOF_TOP10_PATH
    sealed_validation_payload = _canonical_json_bytes(sealed_validation, pretty=True)
    _write_immutable(root / RECOVERY_VALIDATION_PATH, sealed_validation_payload)

    return {
        "schema_version": "dc20_three_rank_sealed_model_snapshot_v1",
        "normal_build_reads_dynamic_assets": False,
        "validation": {
            "path": RECOVERY_VALIDATION_PATH,
            "sha256": hashlib.sha256(sealed_validation_payload).hexdigest(),
            "one_time_source_path": VALIDATION_PATH,
            "one_time_source_sha256": EXPECTED_BINDINGS[VALIDATION_PATH],
        },
        "data_validation": {
            "path": RECOVERY_DATA_VALIDATION_PATH,
            "sha256": hashlib.sha256(data_validation_payload).hexdigest(),
            "one_time_source_path": DATA_VALIDATION_PATH,
        },
        "oof_top10": {
            "path": RECOVERY_OOF_TOP10_PATH,
            "sha256": hashlib.sha256(oof_payload).hexdigest(),
            "one_time_source_path": OOF_TOP10_PATH,
        },
        "ledger": {
            "path": RECOVERY_LEDGER_PATH,
            "sha256": hashlib.sha256(ledger_payload).hexdigest(),
            "one_time_source_path": LEDGER_PATH,
        },
        "ledger_manifest": {
            "path": RECOVERY_LEDGER_MANIFEST_PATH,
            "sha256": sealed_manifest_sha,
            "one_time_source_path": LEDGER_MANIFEST_PATH,
        },
        "artifacts": artifact_records,
    }


def prepare_recovery_inputs(
    root: Path | str,
    cache_root: Path | str,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    recovery = root_path / RECOVERY_ROOT
    prepared_pool, preparation_binding = load_bound_candidate_pool(root_path)
    _write_immutable(
        root_path / RECOVERY_SOURCE_PATH,
        (root_path / SOURCE_PATH).read_bytes(),
    )
    _write_immutable(
        root_path / RECOVERY_SOURCE_META_PATH,
        (root_path / SOURCE_META_PATH).read_bytes(),
    )
    pool, source_binding = load_bound_candidate_pool(root_path, recovery_only=True)
    if _csv_bytes(pool) != _csv_bytes(prepared_pool):
        _fail("repository recovery source copy changed the hard-pool projection")
    candidate_path = recovery / "candidate_pool.csv"
    candidate_payload = _csv_bytes(pool)
    _write_immutable(candidate_path, candidate_payload)
    stock_prior_payload, stock_prior_record = _prepare_stock_priors(root_path, pool)
    _write_immutable(root_path / RECOVERY_STOCK_PRIORS_PATH, stock_prior_payload)
    model_snapshot_record = _prepare_model_snapshot(root_path)

    market_inputs: list[dict[str, Any]] = []
    for code in pool["ts_code"]:
        bars, audit = load_bound_tencent_bars(cache_root, str(code))
        relative = f"{RECOVERY_ROOT}/daily_bars/{str(code).replace('.', '_')}.csv.gz"
        path = root_path / relative
        payload = _gzip_bytes(_csv_bytes(bars))
        _write_immutable(path, payload)
        market_inputs.append(
            {
                "ts_code": str(code),
                "public_source": "Tencent public daily bars",
                "raw_sha256": audit.pop("raw_sha256"),
                "normalized_path": relative,
                "normalized_sha256": hashlib.sha256(payload).hexdigest(),
                **audit,
            }
        )
    manifest: dict[str, Any] = {
        "schema_version": "dc20_three_rank_recovery_inputs_v1",
        "owner": "njedu2023-prog/DC20",
        "runtime_dependency_on_external_repository": False,
        "runtime_dependency_on_public_cache_after_materialization": False,
        "signal_date": SIGNAL_DATE,
        "exec_date": EXEC_DATE,
        "exit_date": EXIT_DATE,
        "point_in_time_contract": {
            "cutoff_date": SIGNAL_DATE,
            "bars_later_than_cutoff_persisted": 0,
            "feature_time": "D close",
            "public_market_price_semantics": "Tencent public exchange daily bars",
        },
        "candidate_source": source_binding,
        "one_time_preparation_provenance": preparation_binding,
        "candidate_pool": {
            "path": f"{RECOVERY_ROOT}/candidate_pool.csv",
            "sha256": hashlib.sha256(candidate_payload).hexdigest(),
            "rows": len(pool),
            "hard_stage_counts": {
                "2→3": int(pool["stage_transition"].eq("2→3").sum()),
                "3→4": int(pool["stage_transition"].eq("3→4").sum()),
            },
        },
        "source_candidates": {
            "path": RECOVERY_SOURCE_PATH,
            "sha256": EXPECTED_BINDINGS[SOURCE_PATH],
        },
        "source_meta": {
            "path": RECOVERY_SOURCE_META_PATH,
            "sha256": EXPECTED_BINDINGS[SOURCE_META_PATH],
        },
        "stock_priors": stock_prior_record,
        "model_snapshot": model_snapshot_record,
        "market_inputs": market_inputs,
    }
    payload = _canonical_json_bytes(manifest, pretty=True)
    _write_immutable(root_path / RECOVERY_MANIFEST_PATH, payload)
    return manifest


def load_recovery_inputs(
    root: Path | str,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], dict[str, Any]]:
    root_path = Path(root).resolve()
    reviewed_pool, source_binding = load_bound_candidate_pool(
        root_path, recovery_only=True
    )
    manifest_path = root_path / RECOVERY_MANIFEST_PATH
    manifest = _mapping_json(manifest_path, "three-rank recovery manifest")
    if (
        manifest.get("schema_version") != "dc20_three_rank_recovery_inputs_v1"
        or manifest.get("owner") != "njedu2023-prog/DC20"
        or manifest.get("runtime_dependency_on_external_repository") is not False
        or manifest.get("runtime_dependency_on_public_cache_after_materialization")
        is not False
        or manifest.get("signal_date") != SIGNAL_DATE
        or manifest.get("exec_date") != EXEC_DATE
        or manifest.get("exit_date") != EXIT_DATE
        or manifest.get("candidate_source") != source_binding
    ):
        _fail("recovery manifest identity/date/source binding drifted")
    point_in_time = manifest.get("point_in_time_contract")
    if not isinstance(point_in_time, Mapping) or (
        point_in_time.get("cutoff_date") != SIGNAL_DATE
        or point_in_time.get("bars_later_than_cutoff_persisted") != 0
    ):
        _fail("recovery manifest point-in-time contract drifted")

    candidate_record = manifest.get("candidate_pool")
    if not isinstance(candidate_record, Mapping):
        _fail("recovery manifest candidate pool binding is missing")
    candidate_path = root_path / str(candidate_record.get("path") or "")
    if (
        candidate_path.resolve() != (root_path / RECOVERY_ROOT / "candidate_pool.csv").resolve()
        or _sha256(candidate_path) != candidate_record.get("sha256")
    ):
        _fail("recovery candidate pool hash/path drifted")
    try:
        pool = pd.read_csv(candidate_path, encoding="utf-8")
    except (OSError, UnicodeError, pd.errors.ParserError) as exc:
        raise ThreeRankSnapshotError("cannot parse recovery candidate pool") from exc
    if _csv_bytes(pool) != _csv_bytes(reviewed_pool):
        _fail("recovery candidate pool differs from reviewed source projection")
    for key, expected_path, expected_sha in (
        ("source_candidates", RECOVERY_SOURCE_PATH, EXPECTED_BINDINGS[SOURCE_PATH]),
        ("source_meta", RECOVERY_SOURCE_META_PATH, EXPECTED_BINDINGS[SOURCE_META_PATH]),
    ):
        record = manifest.get(key)
        if (
            not isinstance(record, Mapping)
            or record.get("path") != expected_path
            or record.get("sha256") != expected_sha
            or _sha256(root_path / expected_path) != expected_sha
        ):
            _fail(f"recovery {key} binding drifted")

    stock_record = manifest.get("stock_priors")
    if (
        not isinstance(stock_record, Mapping)
        or stock_record.get("path") != RECOVERY_STOCK_PRIORS_PATH
        or stock_record.get("preparation_source_sha256")
        != EXPECTED_PROMOTION_EVENT_SOURCE_SHA256
    ):
        _fail("recovery stock-prior binding is missing or invalid")
    stock_path = root_path / RECOVERY_STOCK_PRIORS_PATH
    if _sha256(stock_path) != stock_record.get("sha256"):
        _fail("recovery stock-prior file hash drifted")
    try:
        stock_priors = pd.read_csv(stock_path, float_precision="round_trip")
    except (OSError, UnicodeError, pd.errors.ParserError) as exc:
        raise ThreeRankSnapshotError("cannot parse recovery stock priors") from exc
    stock_priors["prior_as_of_date"] = stock_priors["prior_as_of_date"].map(
        _normal_date
    )
    if (
        len(stock_priors) != len(reviewed_pool)
        or set(stock_priors["ts_code"]) != set(reviewed_pool["ts_code"])
        or not stock_priors["prior_as_of_date"].lt(SIGNAL_DATE).all()
        or stock_priors[
            ["five_year_stock_prior_rate", "five_year_stock_prior_samples_log"]
        ]
        .isna()
        .any()
        .any()
    ):
        _fail("recovery stock priors escaped exact-pool/strict-cutoff contract")

    model_snapshot = manifest.get("model_snapshot")
    if (
        not isinstance(model_snapshot, Mapping)
        or model_snapshot.get("schema_version")
        != "dc20_three_rank_sealed_model_snapshot_v1"
        or model_snapshot.get("normal_build_reads_dynamic_assets") is not False
    ):
        _fail("recovery sealed model snapshot binding is missing")
    sealed_records = {
        RECOVERY_VALIDATION_PATH: model_snapshot.get("validation"),
        RECOVERY_DATA_VALIDATION_PATH: model_snapshot.get("data_validation"),
        RECOVERY_LEDGER_PATH: model_snapshot.get("ledger"),
        RECOVERY_LEDGER_MANIFEST_PATH: model_snapshot.get("ledger_manifest"),
        RECOVERY_OOF_TOP10_PATH: model_snapshot.get("oof_top10"),
        **{
            path: (model_snapshot.get("artifacts") or {}).get(head)
            for head, path in RECOVERY_ARTIFACT_PATHS.items()
        },
    }
    for path, record in sealed_records.items():
        if (
            not isinstance(record, Mapping)
            or record.get("path") != path
            or record.get("sha256")
            != EXPECTED_SEALED_RUNTIME_BINDINGS[path]
        ):
            _fail(f"recovery sealed model record drifted for {path}")

    records = manifest.get("market_inputs")
    if not isinstance(records, list) or len(records) != len(EXPECTED_HARD_POOL):
        _fail("recovery market input inventory is incomplete")
    bars_by_code: dict[str, pd.DataFrame] = {}
    for record in records:
        if not isinstance(record, Mapping):
            _fail("recovery market input record is invalid")
        code = _normal_text(record.get("ts_code"))
        expected_path = f"{RECOVERY_ROOT}/daily_bars/{code.replace('.', '_')}.csv.gz"
        if (
            code not in EXPECTED_TENCENT_SHA256
            or record.get("raw_sha256") != EXPECTED_TENCENT_SHA256[code]
            or record.get("normalized_path") != expected_path
            or record.get("normalized_max_date") != SIGNAL_DATE
        ):
            _fail(f"recovery market input identity drifted for {code!r}")
        path = root_path / expected_path
        if _sha256(path) != record.get("normalized_sha256"):
            _fail(f"recovery normalized bar hash drifted for {code}")
        try:
            bars = pd.read_csv(path, compression="gzip")
        except (OSError, UnicodeError, pd.errors.ParserError) as exc:
            raise ThreeRankSnapshotError(
                f"cannot parse recovery bars for {code}"
            ) from exc
        bars["trade_date"] = bars["trade_date"].map(_normal_date)
        for column in ("open", "close", "high", "low", "volume", "pre_close"):
            bars[column] = pd.to_numeric(bars[column], errors="coerce")
        if (
            bars.empty
            or bars["ts_code"].astype(str).ne(code).any()
            or bars["trade_date"].max() > SIGNAL_DATE
            or not bars["trade_date"].eq(SIGNAL_DATE).any()
            or len(bars) != record.get("normalized_rows")
        ):
            _fail(f"recovery bars for {code} violate cutoff/row contract")
        bars_by_code[code] = bars
    if set(bars_by_code) != set(reviewed_pool["ts_code"]):
        _fail("recovery market inventory differs from hard pool")
    reviewed_pool = reviewed_pool.merge(
        stock_priors,
        on="ts_code",
        how="left",
        validate="one_to_one",
    )
    return reviewed_pool, bars_by_code, manifest


def _seed_context_engine(
    root: Path,
    bars_by_code: Mapping[str, pd.DataFrame],
) -> tuple[AuctionV3Engine, list[str]]:
    engine = AuctionV3Engine(AuctionV3Config(root=root))
    trading_dates = sorted(
        set().union(
            *(set(frame["trade_date"].astype(str)) for frame in bars_by_code.values())
        )
    )
    engine._market_dates_cache = trading_dates
    for trade_date in trading_dates:
        daily_rows: list[dict[str, Any]] = []
        limit_rows: list[dict[str, Any]] = []
        for code, bars in bars_by_code.items():
            selected = bars[bars["trade_date"].astype(str).eq(trade_date)]
            if selected.empty:
                continue
            row = selected.iloc[-1]
            daily_rows.append(
                {
                    "trade_date": trade_date,
                    "ts_code": code,
                    "open": float(row["open"]),
                    "close": float(row["close"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "vol": float(row["volume"]),
                    "volume": float(row["volume"]),
                    "pre_close": float(row["pre_close"])
                    if pd.notna(row["pre_close"])
                    else math.nan,
                }
            )
            pre_close = pd.to_numeric(pd.Series([row["pre_close"]]), errors="coerce").iloc[0]
            if pd.notna(pre_close) and float(pre_close) > 0.0:
                limit_rows.append(
                    {
                        "trade_date": trade_date,
                        "ts_code": code,
                        "up_limit": _round_price(float(pre_close) * 1.10),
                    }
                )
        daily = pd.DataFrame(daily_rows)
        limits = pd.DataFrame(limit_rows)
        if not daily.empty:
            daily = daily.drop_duplicates("ts_code", keep="last").set_index(
                "ts_code", drop=False
            )
        if not limits.empty:
            limits = limits.drop_duplicates("ts_code", keep="last").set_index(
                "ts_code", drop=False
            )
        engine._market_cache[(trade_date, "daily")] = daily
        engine._market_cache[(trade_date, "stk_limit")] = limits
    return engine, trading_dates


def build_runtime_candidate_frame(
    root: Path | str,
    pool: pd.DataFrame,
    bars_by_code: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    root_path = Path(root).resolve()
    engine, trading_dates = _seed_context_engine(root_path, bars_by_code)
    stage2_count = int(pool["stage_transition"].eq("2→3").sum())
    stage3_count = int(pool["stage_transition"].eq("3→4").sum())
    pool_size = len(pool)
    rows: list[dict[str, Any]] = []
    for candidate in pool.itertuples(index=False):
        code = str(candidate.ts_code)
        stage = int(candidate.stage)
        bars = bars_by_code[code]
        d_rows = bars[bars["trade_date"].astype(str).eq(SIGNAL_DATE)]
        if len(d_rows) != 1:
            _fail(f"recovery bars for {code} do not have exactly one D row")
        d_row = d_rows.iloc[0]
        shared_features = compute_d_close_features(
            bars[["trade_date", "open", "close", "high", "low", "volume"]],
            cutoff_date=SIGNAL_DATE,
        )
        shared_d = shared_features[
            shared_features["trade_date"].astype(str).eq(SIGNAL_DATE)
        ]
        if len(shared_d) != 1:
            _fail(f"canonical D-close features are missing for {code}")
        feature_values = shared_d.iloc[0].drop(labels="trade_date").to_dict()
        if any(pd.isna(feature_values[name]) for name in D_CLOSE_FEATURE_COLUMNS):
            _fail(f"canonical D-close feature is empty for {code}")
        context = engine._promotion_source_context_features(
            SIGNAL_DATE, code, trading_dates
        )
        calculated_stage = engine._consecutive_limit_up_count(
            SIGNAL_DATE, code, trading_dates
        )
        if calculated_stage != stage:
            _fail(
                f"public daily-bar streak for {code} is {calculated_stage}, "
                f"not source stage {stage}"
            )
        for feature in PROMOTION_CONTEXT_FEATURES[:8]:
            if pd.isna(context.get(feature)):
                _fail(f"production promotion context {feature} is empty for {code}")
        previous_close = float(d_row["pre_close"])
        rows.append(
            {
                "signal_date": SIGNAL_DATE,
                "ts_code": code,
                "name": str(candidate.name),
                "industry": str(candidate.industry),
                "source_rank": int(candidate.source_rank),
                "stage": stage,
                "stage_transition": str(candidate.stage_transition),
                "board": str(candidate.market_board),
                "d_open": float(d_row["open"]),
                "d_close": float(d_row["close"]),
                "d_high": float(d_row["high"]),
                "d_low": float(d_row["low"]),
                "d_volume": float(d_row["volume"]),
                "d_pct_change": (float(d_row["close"]) / previous_close - 1.0)
                * 100.0,
                "focus_pool_size": pool_size,
                "stage2_pool_size": stage2_count,
                "stage3_pool_size": stage3_count,
                "stage_pool_share": (
                    stage2_count if stage == 2 else stage3_count
                )
                / pool_size,
                "mechanism_limit_pct": float(candidate.mechanism_limit_pct),
                **feature_values,
                **context,
                "five_year_stock_prior_rate": float(
                    candidate.five_year_stock_prior_rate
                ),
                "five_year_stock_prior_samples_log": float(
                    candidate.five_year_stock_prior_samples_log
                ),
            }
        )
    return pd.DataFrame(rows)


def _build_evidence(
    root: Path,
    *,
    recovery_manifest: Mapping[str, Any],
    contract: Mapping[str, Any],
    json_path: Path,
    csv_path: Path,
) -> dict[str, Any]:
    validation = _mapping_json(
        root / RECOVERY_VALIDATION_PATH, "sealed model validation"
    )
    artifacts = validation["artifacts"]
    source = validation["source"]
    return {
        "schema_version": "dc20_three_rank_snapshot_evidence_v1",
        "owner": "njedu2023-prog/DC20",
        "runtime_dependency_on_top10_decision": False,
        "runtime_dependency_on_external_repository": False,
        "signal_date": SIGNAL_DATE,
        "exec_date": EXEC_DATE,
        "exit_date": EXIT_DATE,
        "source_bindings": {
            "candidate": {
                "path": RECOVERY_SOURCE_PATH,
                "sha256": EXPECTED_BINDINGS[SOURCE_PATH],
            },
            "candidate_meta": {
                "path": RECOVERY_SOURCE_META_PATH,
                "sha256": EXPECTED_BINDINGS[SOURCE_META_PATH],
            },
            "stock_priors": {
                "path": RECOVERY_STOCK_PRIORS_PATH,
                "sha256": recovery_manifest["stock_priors"]["sha256"],
            },
            "recovery_manifest": {
                "path": RECOVERY_MANIFEST_PATH,
                "sha256": _sha256(root / RECOVERY_MANIFEST_PATH),
            },
        },
        "model_bindings": {
            "validation": {
                "path": RECOVERY_VALIDATION_PATH,
                "sha256": EXPECTED_SEALED_RUNTIME_BINDINGS[
                    RECOVERY_VALIDATION_PATH
                ],
            },
            "data_validation": {
                "path": RECOVERY_DATA_VALIDATION_PATH,
                "sha256": EXPECTED_SEALED_RUNTIME_BINDINGS[
                    RECOVERY_DATA_VALIDATION_PATH
                ],
            },
            "ledger": {
                "path": source["ledger_path"],
                "sha256": source["ledger_sha256"],
            },
            "ledger_manifest": {
                "path": source["ledger_manifest_path"],
                "sha256": source["ledger_manifest_sha256"],
            },
            "artifacts": {
                head: {
                    "path": artifacts[head]["path"],
                    "sha256": artifacts[head]["sha256"],
                }
                for head in ("promotion", "big_loss", "profit", "p_fill_shadow")
            },
            "oof_top10": {
                "path": artifacts["oof_top10"]["path"],
                "sha256": artifacts["oof_top10"]["sha256"],
            },
        },
        "point_in_time_proof": {
            "cutoff_date": SIGNAL_DATE,
            "normalized_market_max_date": max(
                record["normalized_max_date"]
                for record in recovery_manifest["market_inputs"]
            ),
            "bars_later_than_cutoff_persisted": 0,
            "feature_contract_version": D_CLOSE_FEATURE_CONTRACT_VERSION,
            "feature_columns": list(D_CLOSE_FEATURE_COLUMNS),
            "feature_snapshot_sha256": contract["feature_snapshot_sha256"],
        },
        "ranking_proof": {
            "status": contract["status"],
            "promotion_pool_size": contract["promotion_pool_size"],
            "top10_count": contract["top10_count"],
            "top10_members_sha256": contract["top10_members_sha256"],
            "bundle_sha256": contract["bundle_sha256"],
            "promotion_ranks": [row["promotion_rank"] for row in contract["rows"]],
            "head_statuses": {
                head: contract["models"][head]["status"]
                for head in ("promotion", "big_loss", "profit")
            },
            "unready_head_fields_are_null": all(
                row["big_loss_safety_rank"] is None
                and row["predicted_big_loss_probability"] is None
                and row["profit_rank"] is None
                and row["predicted_profit_probability"] is None
                for row in contract["rows"]
            ),
        },
        "execution": {
            "actual_execution_claimed": False,
            "buy_count": 0,
            "decision": "NO_TRADE",
            "reason": "dated research ranking recovery; B/C validation gates are not READY",
        },
        "outputs": {
            "json": {
                "path": json_path.relative_to(root).as_posix(),
                "sha256": _sha256(json_path),
            },
            "csv": {
                "path": csv_path.relative_to(root).as_posix(),
                "sha256": _sha256(csv_path),
            },
        },
    }


def build_decision_three_rank_snapshot(
    root: Path | str = ROOT,
    *,
    cache_root: Path | str | None = None,
    prepare_from_cache: bool = False,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    recovery_manifest = root_path / RECOVERY_MANIFEST_PATH
    if prepare_from_cache:
        if cache_root is None:
            _fail("--prepare-from-cache requires an explicit --cache-root")
        prepare_recovery_inputs(root_path, cache_root)
    elif cache_root is not None:
        _fail("cache_root is preparation-only; omit it for the permanent rebuild")
    if not recovery_manifest.is_file():
        _fail(
            "repository-owned recovery inputs are absent; run the explicit "
            "one-time --prepare-from-cache command first"
        )
    validate_static_bindings(root_path)
    pool, bars_by_code, recovery = load_recovery_inputs(root_path)
    runtime = build_runtime_candidate_frame(root_path, pool, bars_by_code)
    loaded = load_research_only_legacy_three_engine_snapshot(
        root_path / RECOVERY_VALIDATION_PATH,
        root=root_path,
    )
    if (
        loaded.metadata["promotion"].get(
            "research_only_legacy_calibration_evidence_missing"
        )
        is not True
        or any(
            loaded.metadata[head].get(
                "research_only_legacy_calibration_evidence_missing"
            )
            is True
            for head in ("big_loss", "profit", "p_fill_shadow")
        )
    ):
        _fail("sealed historical model compatibility scope drifted")
    score = score_three_engine_snapshot(
        runtime,
        loaded,
        signal_date=SIGNAL_DATE,
    )
    if (
        score.status != "PARTIAL_MODELS_NOT_READY"
        or score.promotion_pool_size != len(EXPECTED_HARD_POOL)
        or score.diagnostics.get("runtime_feature_gate_passed") is not True
        or score.diagnostics.get("runtime_promotion_priors_attached") is not True
    ):
        _fail(f"production three-engine scorer did not pass runtime gates: {score.diagnostics}")
    rows = score.rows
    if (
        rows["top10_selected"].sum() != len(EXPECTED_HARD_POOL)
        or sorted(pd.to_numeric(rows["promotion_rank"]).astype(int))
        != list(range(1, len(EXPECTED_HARD_POOL) + 1))
        or set(rows["promotion_model_status"]) != {"READY"}
        or set(rows["big_loss_model_status"]) != {"NOT_READY_VALIDATION_GATE"}
        or set(rows["profit_model_status"]) != {"NOT_READY_VALIDATION_GATE"}
        or rows["big_loss_safety_rank"].notna().any()
        or rows["predicted_big_loss_probability"].notna().any()
        or rows["profit_rank"].notna().any()
        or rows["predicted_profit_probability"].notna().any()
    ):
        _fail("three-engine output violated ready/null/rank contract")

    plan = {
        "signal_date": SIGNAL_DATE,
        "exec_date": EXEC_DATE,
        "exit_date": EXIT_DATE,
        "generated_at_utc": recovery["candidate_source"]["generated_at_utc"],
        "feature_snapshot_sha256": score.feature_snapshot_sha256,
        "top10_members_sha256": score.top10_members_sha256,
        "candidates": rows.to_dict(orient="records"),
    }
    contract = build_three_rank_contract(plan)
    contract["execution_summary"] = {
        "actual_execution_claimed": False,
        "buy_count": 0,
        "decision": "NO_TRADE",
        "reason": "dated research ranking recovery; B/C validation gates are not READY",
    }
    validate_three_rank_contract(contract)
    json_path, csv_path, materialized = materialize_three_rank_artifacts(
        root_path, contract
    )
    if materialized.get("execution_summary") != contract["execution_summary"]:
        _fail("existing immutable contract lacks the exact BUY=0 execution binding")
    evidence = _build_evidence(
        root_path,
        recovery_manifest=recovery,
        contract=materialized,
        json_path=json_path,
        csv_path=csv_path,
    )
    evidence_path = root_path / EVIDENCE_PATH
    _write_immutable(evidence_path, _canonical_json_bytes(evidence, pretty=True))
    return {
        "contract": materialized,
        "evidence": evidence,
        "paths": {
            "json": json_path,
            "csv": csv_path,
            "evidence": evidence_path,
            "recovery_manifest": recovery_manifest,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=None,
        help="explicit one-time public-cache directory; never used by a normal rebuild",
    )
    parser.add_argument(
        "--prepare-from-cache",
        action="store_true",
        help="one-time materialization of DC20-owned <=D recovery inputs",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = build_decision_three_rank_snapshot(
            args.root,
            cache_root=args.cache_root,
            prepare_from_cache=args.prepare_from_cache,
        )
    except ThreeRankSnapshotError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    contract = result["contract"]
    print(
        json.dumps(
            {
                "status": contract["status"],
                "signal_date": contract["signal_date"],
                "top10_count": contract["top10_count"],
                "promotion_status": contract["models"]["promotion"]["status"],
                "big_loss_status": contract["models"]["big_loss"]["status"],
                "profit_status": contract["models"]["profit"]["status"],
                "buy_count": contract["execution_summary"]["buy_count"],
                "bundle_sha256": contract["bundle_sha256"],
                "paths": {
                    name: path.relative_to(Path(args.root).resolve()).as_posix()
                    for name, path in result["paths"].items()
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
