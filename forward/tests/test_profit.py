"""Independent P1 inference tests; legacy artifacts are comparison-only fixtures."""
from __future__ import annotations

import builtins
import copy
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from forward import profit


ROOT = Path(__file__).resolve().parents[2]


def rebind(bundle):
    """Rehash a synthetic fixture so deeper, independent bindings get tested."""
    day = bundle["day"]
    bundle["runtime_sha256"] = profit._digest({
        "schema_version": "dc20_forward_promotion_runtime_v1", "signal_date": day["signal_date"],
        "columns": bundle["runtime_columns"], "rows": bundle["runtime_rows"]})
    source = day["source"]
    source["computation_performed"] = True
    source["inference_performed"] = bool(bundle["runtime_rows"])
    source["runtime_sha256"] = bundle["runtime_sha256"]
    source["feature_snapshot_sha256"] = bundle["feature_snapshot_sha256"]
    source["members_sha256"] = profit._digest({"schema": "dc20_three_rank_member_set_v1",
        "signal_date": day["signal_date"], "members": sorted(r["ts_code"] for r in day["rows"])})
    bundle["receipt"] = dict(signal_date=day["signal_date"], selected_count=len(day["rows"]),
        promotion_pool_size=len(bundle["runtime_rows"]), runtime_sha256=bundle["runtime_sha256"],
        day_sha256=profit._digest(day), computation_performed=True,
        inference_performed=bool(bundle["runtime_rows"]), training_performed=False)
    return bundle


@pytest.fixture(scope="module")
def historical_bundle():
    # Only the TEST adapter reads an old P0 runtime. infer_profit never does.
    import pandas as pd
    date = "20260908"
    p0 = json.loads((ROOT / f"outputs/decision/three_rank_top10_{date}.json").read_text())
    frame = pd.read_csv(ROOT / f"outputs/decision/primary_d_runtime_features_{date}.csv", low_memory=False)
    frame["signal_date"] = frame["signal_date"].astype(str)
    frame = frame.astype(object).where(pd.notna(frame), None)
    rows = [{"ts_code": r["ts_code"], "name": r["name"], "industry": r["industry"],
             "stage_transition": r["stage_transition"], "promotion_rank": r["promotion_rank"],
             "promotion_probability": r["predicted_promotion_probability"],
             "path_label": None, "path_change_pct": None} for r in p0["rows"]]
    day = {key: p0[key] for key in ("signal_date", "exec_date", "exit_date", "generated_at_utc")}
    day.update(generation_mode="REPLAY", rows=rows, source={
        "schema_version": "dc20_forward_promotion_source_v1",
        "model_sha256": "b7837d7001917a9c7bcc8814a09b45c6460f36a1adf6a7b5dcc024b4adc5f79c",
        "source_commit": subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
        "inference_performed": True, "training_performed": False, "legacy_ranking_read": False,
        "production_enabled": False})
    return rebind(dict(day=day, runtime_rows=frame.to_dict("records"), runtime_columns=list(frame.columns),
                       feature_snapshot_sha256=p0["feature_snapshot_sha256"]))


def test_real_retained_model_reproduces_historical_profit_ranks_and_scores(historical_bundle, monkeypatch):
    expected = json.loads((ROOT / "outputs/decision/executable_profit_research/projection_20260908.json").read_text())
    before = copy.deepcopy(historical_bundle)
    original_open, original_path_open = builtins.open, Path.open
    reads = []
    def guard(path):
        if not isinstance(path, (str, bytes, Path)):
            return
        text = str(path)
        assert "/outputs/decision/" not in text
        assert "/outputs/auction" not in text
        assert "/data/decision_executable_profit/forward/" not in text
        assert not ("action_plan" in text and not text.endswith(".py"))
        reads.append(text)
    def checked_open(file, *args, **kwargs):
        guard(file)
        return original_open(file, *args, **kwargs)
    def checked_path_open(path, *args, **kwargs):
        guard(path)
        return original_path_open(path, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", checked_open)
    monkeypatch.setattr(Path, "open", checked_path_open)
    out = profit.infer_profit(ROOT, historical_bundle)
    wanted = {row["ts_code"]: row for row in expected["rows"]}
    assert [r["ts_code"] for r in out["rows"]] == [r["ts_code"] for r in historical_bundle["day"]["rows"]]
    for row in out["rows"]:
        old = wanted[row["ts_code"]]
        assert row["profit_rank"] == old["executable_profit_research_rank"]
        assert row["profit_score"] == pytest.approx(old["research_joint_proxy_score"], abs=1e-15)
        assert row["promotion_rank"] == old["promotion_rank"]
    assert out["generation_mode"] == "REPLAY"
    assert out["source"]["model_sha256"] == profit.MODEL_SHA256
    assert out["source"]["inference_performed"] is True
    assert out["source"]["lagged_prior_max_history_exit_date"] < out["signal_date"]
    assert out["source"]["calibrated_probability_output"] is False
    assert datetime.fromisoformat(out["generated_at_utc"]) >= datetime.fromisoformat(historical_bundle["day"]["generated_at_utc"])
    assert any(str(profit.MODEL_PATH) in path for path in reads)
    assert historical_bundle == before


@pytest.mark.parametrize("change", ["runtime_value", "row_order", "duplicate_code", "wrong_member",
    "promotion_rank", "probability", "name", "runtime_date", "runtime_timestamp", "feature_binding",
    "missing_column", "nan", "natural", "source_commit", "missing_receipt", "receipt_hash"])
def test_corrupt_promotion_or_feature_binding_fails_closed(historical_bundle, change):
    b = copy.deepcopy(historical_bundle)
    if change == "runtime_value": b["runtime_rows"][0]["d_close"] += 1
    elif change == "row_order": b["day"]["rows"].reverse()
    elif change == "duplicate_code": b["day"]["rows"][1]["ts_code"] = b["day"]["rows"][0]["ts_code"]
    elif change == "wrong_member": b["day"]["rows"][0]["ts_code"] = "600999.SH"
    elif change == "promotion_rank": b["day"]["rows"][0]["promotion_rank"] = 2
    elif change == "probability": b["day"]["rows"][0]["promotion_probability"] = .1
    elif change == "name": b["day"]["rows"][0]["name"] = "Changed"
    elif change == "runtime_date": b["runtime_rows"][0]["signal_date"] = "20260909"
    elif change == "runtime_timestamp": b["runtime_rows"][0]["generated_at_utc"] = "2026-09-09T00:00:00Z"
    elif change == "feature_binding": b["feature_snapshot_sha256"] = "f" * 64
    elif change == "missing_column": b["runtime_columns"].remove("d_close")
    elif change == "nan": b["runtime_rows"][0]["d_close"] = float("nan")
    elif change == "natural": b["day"]["generation_mode"] = "NATURAL"
    elif change == "source_commit":
        b["day"]["source"]["source_commit"] = "0" * 40
        rebind(b)
    elif change == "missing_receipt": del b["receipt"]
    elif change == "receipt_hash": b["receipt"]["day_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        profit.infer_profit(ROOT, b)


def test_rehashed_runtime_tamper_still_fails_frozen_feature_snapshot(historical_bundle):
    b = copy.deepcopy(historical_bundle)
    b["runtime_rows"][0]["d_close"] += 1
    rebind(b)
    with pytest.raises(ValueError, match="feature SHA"):
        profit.infer_profit(ROOT, b)


def test_future_or_predated_profit_timestamp_rejected(historical_bundle):
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="future"):
        profit.infer_profit(ROOT, historical_bundle, generated_at_utc=future)
    with pytest.raises(ValueError, match="predate"):
        profit.infer_profit(ROOT, historical_bundle, generated_at_utc="2026-09-01T00:00:00Z")


def test_empty_real_pool_does_not_call_model_or_pad_members(historical_bundle, monkeypatch):
    b = copy.deepcopy(historical_bundle)
    b["day"]["rows"], b["runtime_rows"] = [], []
    rebind(b)
    helper, _, _ = profit._dependencies(ROOT)
    monkeypatch.setattr(helper, "load_internal_challenger", lambda *_: pytest.fail("N0 must not run model"))
    out = profit.infer_profit(ROOT, b)
    assert out["rows"] == []
    assert out["source"]["computation_completed"] is True
    assert out["source"]["inference_performed"] is out["source"]["model_loaded"] is False


def test_empty_promotion_compute_contract_connects_to_profit_without_padding(monkeypatch):
    from forward import promotion
    helper = promotion._import_p0(ROOT)
    original = helper.build_exact_primary_pool
    def empty_pool(*args, **kwargs):
        frame, audit = original(*args, **kwargs)
        return frame.iloc[:0].copy(), dict(audit, hard_stage_rows=0)
    monkeypatch.setattr(helper, "build_exact_primary_pool", empty_pool)
    bundle = promotion.compute_promotion_bundle(ROOT, "20260908",
        generated_at_utc=datetime.now(timezone.utc).isoformat())
    assert bundle["day"]["source"]["inference_performed"] is False
    out = profit.infer_profit(ROOT, bundle)
    assert out["rows"] == []
    assert out["source"]["computation_completed"] is True
    assert out["source"]["model_loaded"] is False


def test_untracked_or_changed_retained_math_is_rejected_before_import(historical_bundle, monkeypatch):
    original = Path.read_bytes
    def changed(path):
        raw = original(path)
        return raw + b"\n" if str(path).endswith("src/top10decision/decision/executable_profit_shadow.py") else raw
    monkeypatch.setattr(Path, "read_bytes", changed)
    monkeypatch.setattr(profit, "_dependencies", lambda *_: pytest.fail("must not import modified source"))
    with pytest.raises(ValueError, match="Git HEAD"):
        profit.infer_profit(ROOT, historical_bundle)


def test_changed_model_bytes_rejected_before_deserialization(historical_bundle, monkeypatch):
    original = Path.read_bytes
    def changed(path):
        raw = original(path)
        return raw + b"tampered" if str(path).endswith(profit.MODEL_PATH) else raw
    monkeypatch.setattr(Path, "read_bytes", changed)
    monkeypatch.setattr(profit, "_dependencies", lambda *_: pytest.fail("must not import before model hash check"))
    with pytest.raises(ValueError, match="source SHA mismatch"):
        profit.infer_profit(ROOT, historical_bundle)


def test_future_outcome_prior_is_not_accepted(historical_bundle, monkeypatch):
    helper, _, _ = profit._dependencies(ROOT)
    original = helper.build_strict_lagged_priors
    def future(**kwargs):
        result = original(**kwargs)
        result["lagged_prior_max_history_exit_date"] = historical_bundle["day"]["signal_date"]
        return result
    monkeypatch.setattr(helper, "build_strict_lagged_priors", future)
    with pytest.raises(ValueError, match="lagged truth"):
        profit.infer_profit(ROOT, historical_bundle)


def test_model_nan_output_is_rejected(historical_bundle, monkeypatch):
    helper, np, _ = profit._dependencies(ROOT)
    original = helper.load_internal_challenger
    class InvalidHead:
        def predict_proba(self, rows):
            return np.full((len(rows), 2), np.nan)
    def invalid(root):
        model = original(root)
        model.bundle["fill_model"] = InvalidHead()
        return model
    monkeypatch.setattr(helper, "load_internal_challenger", invalid)
    with pytest.raises(ValueError, match="invalid profit model output"):
        profit.infer_profit(ROOT, historical_bundle)
