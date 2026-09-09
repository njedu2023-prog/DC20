"""Real inference by the retained mixed-profit model, in REPLAY staging only.

The caller supplies a newly computed promotion runtime bundle. This module
never reads a prior profit projection, Action, selection or performance ledger.
The hash-pinned five-year supervised table is used solely for lagged features.
No weights are fitted, no output files are written, and no order is produced.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


MODEL_PATH = "work/executable-profit-lagged-features-20260824/outputs/internal_forward_challenger.pkl"
MODEL_SHA256 = "42dfb497d4457db9fbdff4180c510fee1ea18ab56696253b06220d981f88d209"
FEATURES_SHA256 = "a07c3c2d688e1e0eb5aaaa891ffd3039d5ca3f6bb26f20e80f88611833893048"
CALENDAR_PATH = "data/market/trade_cal_sse.csv"
CALENDAR_SHA256 = "150a3e29ebd6e050d55caee1df218ef5dcfc3542053d8a7478d6be50d09fd748"
HISTORY_PATH = "data/decision_three_engines/five_year_supervised_ledger.csv.gz"
HISTORY_SHA256 = "7cabe48da6375106b22b2c08c17a7b11780861fed319496ee26761d20fa20a46"
SUPPORTING_INPUTS = (
    "models/decision_executable_profit_internal_forward_challenger.json",
    "models/decision_executable_profit_shadow_contract.json",
    "data/decision_executable_profit/historical_oof_top10_ledger_manifest.json",
    "work/executable-profit-lagged-features-20260824/ARTIFACT_INDEX.json",
    "work/executable-profit-lagged-features-20260824/lagged_priors.py",
    "work/executable-profit-lagged-features-20260824/outputs/internal_forward_challenger_audit.json",
    "work/executable-profit-lagged-features-20260824/outputs/lagged_priors_manifest.json",
)
SHA_RE = re.compile(r"[0-9a-f]{64}")
CODE_RE = re.compile(r"(?:(?:600|601|603|605)\d{3}\.SH|(?:000|001|002|003)\d{3}\.SZ)")


class ProfitInferenceError(ValueError):
    """Invalid independent P1 input; the caller must retain its valid P0."""


def _require(condition, message):
    if not condition:
        raise ProfitInferenceError(message)


def _digest(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, OverflowError) as exc:
        raise ProfitInferenceError("input must be finite canonical JSON") from exc
    return hashlib.sha256(raw).hexdigest()


def _number(value):
    _require(not isinstance(value, bool) and isinstance(value, (int, float, str)), "invalid numeric input")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProfitInferenceError("invalid numeric input") from exc
    _require(math.isfinite(number), "nonfinite numeric input")
    return number


def _date(value):
    _require(isinstance(value, str) and re.fullmatch(r"20\d{6}", value), "exact YYYYMMDD required")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise ProfitInferenceError("invalid calendar date") from exc
    return value


def _timestamp(value):
    _require(isinstance(value, str), "timezone-aware timestamp required")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProfitInferenceError("invalid timestamp") from exc
    _require(stamp.utcoffset() is not None, "timestamp must be timezone aware")
    return stamp


def _file_sha(root, relative, expected=None):
    path = root / relative
    _require(root in path.resolve().parents and path.is_file(), f"missing model/feature source: {relative}")
    _require(not any(p.is_symlink() for p in (path, *path.parents) if p != root), "source symlinks forbidden")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    _require(expected is None or actual == expected, f"source SHA mismatch: {relative}")
    return actual


def _dependencies(root):
    # Lazy import keeps P0, basic ledger and HTML code free of ML dependencies.
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    module = importlib.import_module("top10decision.decision.executable_profit_shadow")
    _require(Path(module.__file__).resolve() == src / "top10decision/decision/executable_profit_shadow.py",
             "inference helper was imported from another checkout")
    for name, imported in list(sys.modules.items()):
        if name.startswith("top10decision.") and getattr(imported, "__file__", None):
            _require(src in Path(imported.__file__).resolve().parents, "mixed-checkout math dependency")
    import numpy as np
    import pandas as pd
    return module, np, pd


def _retained_code_bindings(root, source_commit):
    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], check=True,
                              capture_output=True).stdout
    try:
        head = git("rev-parse", "HEAD").decode().strip()
        _require(head == source_commit, "P1 checkout differs from the recomputed promotion revision")
        lines = git("ls-tree", "-r", "HEAD", "--", "src/top10decision").decode().splitlines()
    except (subprocess.CalledProcessError, UnicodeError) as exc:
        raise ProfitInferenceError("inference requires a source-bound Git checkout") from exc
    bindings = {}
    for line in lines:
        prefix, path = line.split("\t", 1)
        if not path.endswith(".py"):
            continue
        mode, kind, expected_blob = prefix.split()
        _require(kind == "blob" and mode in {"100644", "100755"}, "unsafe retained inference source")
        digest = _file_sha(root, path)
        raw = (root / path).read_bytes()
        actual_blob = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
        _require(actual_blob == expected_blob, f"retained inference code differs from Git HEAD: {path}")
        bindings[path] = digest
    _require("src/top10decision/decision/executable_profit_shadow.py" in bindings,
             "missing committed inference helper")
    return head, bindings


def _verify_unchanged(root, dependencies, source_commit):
    for path, expected in dependencies.items():
        _file_sha(root, path, expected)
    current = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                             capture_output=True, text=True).stdout.strip()
    _require(current == source_commit, "source revision changed during profit computation")


def _validate_bundle(bundle):
    """Compatibility wrapper; shared P0 validation cannot import the P1 module."""
    from .promotion_schema import PromotionSchemaError, validate_bundle
    try:
        return validate_bundle(bundle)
    except PromotionSchemaError as exc:
        raise ProfitInferenceError(str(exc)) from exc


def _infer_profit(root, promotion_result, *, generated_at_utc=None):
    """Infer exact-member independent profit scores; never a win-rate claim.

This staging entry point intentionally rejects NATURAL. A historical replay
does not establish a timely freeze, forward accuracy, or production readiness.
"""
    root = Path(root).resolve(strict=True)
    day, codes, runtime_sha = _validate_bundle(promotion_result)
    generated = generated_at_utc or datetime.now(timezone.utc).isoformat()
    _require(_timestamp(generated) >= _timestamp(day["generated_at_utc"]), "P1 cannot predate its P0 computation")
    _require(_timestamp(generated) <= datetime.now(timezone.utc), "P1 computation timestamp is in the future")
    dependencies = {
        MODEL_PATH: _file_sha(root, MODEL_PATH, MODEL_SHA256),
        CALENDAR_PATH: _file_sha(root, CALENDAR_PATH, CALENDAR_SHA256),
        HISTORY_PATH: _file_sha(root, HISTORY_PATH, HISTORY_SHA256),
    }
    head, code_bindings = _retained_code_bindings(root, day["source"].get("source_commit"))
    dependencies.update(code_bindings)
    dependencies.update({path: _file_sha(root, path) for path in SUPPORTING_INPUTS})
    helper, np, pd = _dependencies(root)
    dates = helper._read_pinned_sse_open_dates(root)
    d, t, t1 = [day[key] for key in ("signal_date", "exec_date", "exit_date")]
    _require(d in dates and dates[dates.index(d):dates.index(d) + 3] == [d, t, t1],
             "D/T/T1 must be adjacent pinned SSE sessions")
    source = {"promotion_source_sha256": _digest(day), "runtime_sha256": runtime_sha,
              "feature_snapshot_sha256": promotion_result["feature_snapshot_sha256"],
              "model_sha256": MODEL_SHA256, "feature_columns_sha256": FEATURES_SHA256,
              "dependencies": dependencies, "model_status": "INTERNAL_CHALLENGER_NOT_READY",
              "research_only": True, "calibrated_probability_output": False,
              "inference_performed": False, "computation_completed": True, "model_loaded": False,
              "source_commit": head, "feature_count": 156,
              "historical_feature_use": "strictly_before_D_lagged_features_not_performance_statistics"}
    result = {"schema_version": "dc20_forward_profit_inference_v1", "status": "REPLAY_RESEARCH_ONLY",
              "generation_mode": "REPLAY", "research_only": True, "signal_date": d,
              "exec_date": t, "exit_date": t1, "generated_at_utc": generated,
              "source": source, "rows": []}
    if not codes:
        source["empty_event_reason"] = "P0_REAL_TOPN_EMPTY"
        _verify_unchanged(root, dependencies, head)
        return result
    loaded = helper.load_internal_challenger(root)
    _require(loaded.source_hashes["model_pickle_sha256"] == MODEL_SHA256
             and len(loaded.feature_columns) == 156, "frozen profit model identity drifted")
    full = pd.DataFrame(promotion_result["runtime_rows"], columns=promotion_result["runtime_columns"])
    snapshot = helper._promotion_feature_snapshot_sha256(full, loaded, signal_date=d)
    _require(snapshot == promotion_result["feature_snapshot_sha256"], "recomputed promotion feature SHA mismatch")
    targets = pd.DataFrame([{
        "signal_date": d, "exec_date": t, "exit_date": t1,
        "ts_code": row["ts_code"], "name": row["name"], "industry": row["industry"],
        "stage": 2 if row["stage_transition"] == "2→3" else 3,
        "stage_transition": row["stage_transition"],
        "board": "SH_MAIN" if row["ts_code"].endswith(".SH") else "SZ_MAIN",
        "promotion_rank": row["promotion_rank"],
    } for row in day["rows"]])
    base = full[full["ts_code"].isin(codes)].copy()
    base["stage"] = helper._strict_frozen_stage_numbers(base["stage"])
    base["board"] = base["board"].fillna("").astype(str).str.upper()
    base = helper._restore_hash_bound_runtime_promotion_priors(base, loaded, signal_date=d)
    prepared = targets.merge(base, on=["signal_date", "ts_code"], how="left",
                             validate="one_to_one", suffixes=("", "_input"))
    _require(prepared["stage"].eq(prepared["stage_input"]).all()
             and prepared["board"].eq(prepared["board_input"]).all(), "stage/board join mismatch")
    for column in loaded.raw_base_features:
        original = prepared[column]
        numeric = pd.to_numeric(original, errors="coerce").replace([np.inf, -np.inf], np.nan)
        invalid = original.notna() & original.astype(str).str.strip().ne("") & numeric.isna()
        _require(not invalid.any() and numeric.notna().any(), f"invalid or all-missing trained feature: {column}")
        prepared[column] = numeric
    prepared["stage_2"] = prepared["stage"].eq(2).astype(float)
    prepared["stage_3"] = prepared["stage"].eq(3).astype(float)
    prepared["board_sh_main"] = prepared["board"].eq("SH_MAIN").astype(float)
    prepared["board_sz_main"] = prepared["board"].eq("SZ_MAIN").astype(float)
    history = pd.read_csv(root / HISTORY_PATH, low_memory=False)
    priors = helper.build_strict_lagged_priors(history=history, targets=targets,
                                            open_dates=dates, lagged_module=loaded.lagged_priors)
    prior_columns = ["signal_date", "ts_code", "lagged_prior_max_history_exit_date",
                     "lagged_prior_snapshot_sha256", *loaded.lagged_features]
    frame = prepared.merge(priors[prior_columns], on=["signal_date", "ts_code"],
                           how="left", validate="one_to_one").sort_values("promotion_rank", kind="stable").reset_index(drop=True)
    _require(len(frame) == len(codes) and frame[list(loaded.lagged_features)].notna().all().all(),
             "lagged prior feature join incomplete")
    availability = frame["lagged_prior_max_history_exit_date"].fillna("").astype(str)
    _require(availability.isin(dates).all() and availability.lt(d).all(), "same-day/future/unbound lagged truth")
    features = frame[list(loaded.feature_columns)]
    fill = np.asarray(loaded.bundle["fill_model"].predict_proba(features)[:, 1], dtype=float)
    conditional = np.asarray(loaded.bundle["conditional_profit_model"].predict_proba(features)[:, 1], dtype=float)
    _require(fill.shape == conditional.shape == (len(codes),)
             and np.isfinite(fill).all() and np.isfinite(conditional).all(), "invalid profit model output")
    fill, conditional = np.clip(fill, 0, 1), np.clip(conditional, 0, 1)
    joint = fill * conditional
    order = sorted(range(len(frame)), key=lambda i: (-float(joint[i]), -float(conditional[i]),
                                                     -float(fill[i]), str(frame.iloc[i]["ts_code"])))
    ranks = {index: rank for rank, index in enumerate(order, 1)}
    result["rows"] = [{"ts_code": row["ts_code"], "name": row["name"], "promotion_rank": row["promotion_rank"],
                       "profit_rank": ranks[index], "profit_score": float(joint[index])}
                      for index, row in enumerate(day["rows"])]
    source.update(inference_performed=True, model_loaded=True, model_source_hashes=dict(loaded.source_hashes),
                  lagged_prior_max_history_exit_date=max(availability),
                  mixed_feature_snapshot_sha256=helper._feature_snapshot_sha256(
                      frame, loaded.feature_columns, source_file_sha256=runtime_sha, generated_at_utc=day["generated_at_utc"]),
                  row_proxy_scores=[{"ts_code": code, "fill_proxy_score": float(fill[i]),
                                     "conditional_profit_score": float(conditional[i])} for i, code in enumerate(codes)])
    source["computation_source_sha256"] = _file_sha(root, "src/top10decision/decision/executable_profit_shadow.py")
    _verify_unchanged(root, dependencies, head)
    _digest(result)
    return result


def infer_profit(root, promotion_result, *, generated_at_utc=None):
    """Read-only, real retained-model inference; REPLAY only until activation."""
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        return _infer_profit(root, promotion_result, generated_at_utc=generated_at_utc)
    finally:
        sys.dont_write_bytecode = previous
