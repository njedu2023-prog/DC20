"""Synthetic projection only: no real sources, source admission, fit or scoring."""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
from pathlib import Path
import socket
from types import MappingProxyType

import pytest

from work.profit_1000_upgrade import candidate_d_feature_projection as p
from work.profit_1000_upgrade import candidate_forward_predict as forward


def fixture(size=2, observed=21):
    day, trained = "20260914", "20260709"
    snapshot, model, members, bundle = (letter * 64 for letter in "abcd")
    count = min(10, size)
    stage2 = min(10, size)
    rows = []
    for i in range(size):
        code, stage = f"{600100+i:06d}.SH", 2 if i < stage2 else 3
        row = {"signal_date": day, "ts_code": code, "stage": float(stage),
            "stage_transition": f"{stage}→{stage+1}", "identity": f"{day}|{code}|{stage}→{stage+1}",
            "promotion_rank": i+1, "top10_selected": int(i < count),
            "promotion_model_as_of_date": trained, "promotion_model_artifact_sha256": model,
            "feature_snapshot_sha256": snapshot, "focus_pool_size": float(size),
            "stage2_pool_size": float(stage2), "stage3_pool_size": float(size-stage2),
            "stage_pool_share": (stage2 if stage == 2 else size-stage2) / size,
            **{key: .25 + j/100 for j, key in enumerate(p.FIVE_YEAR_RETAINED)}}
        rows.append(row)
    contract = {"schema_version": "decision_three_rank_top10_v1",
        "artifact_kind": "d_close_independent_three_rank_top10",
        "membership_authority": "promotion_probability_engine_only", "downstream_scope": "exact_frozen_promotion_top10",
        "signal_date": day, "exec_date": "20260915", "exit_date": "20260916", "feature_as_of_date": day,
        "feature_snapshot_sha256": snapshot, "top10_members_sha256": members, "bundle_sha256": bundle,
        "promotion_pool_size": size, "top10_count": count,
        "models": {"promotion": {"status": "READY", "model_as_of_date": trained, "artifact_sha256": model}},
        "rows": [{key: row[key] for key in ("ts_code", "stage_transition", "promotion_rank", "top10_selected")}
            for row in rows[:count]]}
    index = {"schema_version": p.INDEX_SCHEMA, "index_kind": "dated_primary_d_runtime_pointer_only",
        "data_alias": False, "latest_signal_date": day, "latest_exec_date": "20260915", "latest_exit_date": "20260916",
        "runtime_feature_row_count": size, "runtime_selected_count": count, "runtime_identity_sha256": "e"*64,
        "latest_feature_snapshot_sha256": snapshot, "latest_top10_members_sha256": members, "latest_bundle_sha256": bundle}
    for suffix, name in (("receipt", f"primary_d_receipt_{day}.json"),
            ("runtime_features", f"primary_d_runtime_features_{day}.csv"),
            ("three_rank_json", f"three_rank_top10_{day}.json"), ("three_rank_csv", f"three_rank_top10_{day}.csv")):
        index[f"latest_{suffix}_url"] = "outputs/decision/" + name
        index[f"latest_{suffix}_sha256"] = "f"*64
    bars = {row["ts_code"]: [{"trade_date": (datetime.strptime(day, "%Y%m%d") - timedelta(days=observed-j-1)).strftime("%Y%m%d"),
        "ts_code": row["ts_code"], "close": 10.+j/10, "volume": 100.+j} for j in range(observed)] for row in rows[:count]}
    history = [{"signal_date": "20260814", "ts_code": row["ts_code"], "promotion_hit": 1} for row in rows[:count]]
    return [rows, contract, index, history, bars], {"signal_date": day, "promotion_train_end": trained,
        "promotion_train_end_kind": p.TRAIN_END_KIND}


def run(case):
    inputs, options = case
    return p.project_candidate_d_features(*inputs, **options)


def test_complete_pool_not_top10_is_used_and_original_rank_preserved():
    case = fixture(13)
    before = deepcopy(case)
    result = run(case)
    assert result["status"] == "PROJECTED_UNVERIFIED_D_FEATURES"
    assert result["full_pool_size"] == 13 and result["candidate_count"] == 10
    assert result["stage2_pool_size"] == 10 and result["stage3_pool_size"] == 3
    assert [r["promotion_rank"] for r in result["rows"]] == list(range(1,11))
    assert all(r["focus_pool_size"] == 13. and r["stage_pool_share"] == 10/13 for r in result["rows"])
    assert all(r["stage3_pool_size"] == 3. for r in result["rows"])
    assert case == before
    assert result["legacy_promotion_oof_train_end_field_meaning"] == "LIVE_PERSISTED_TRAIN_END"
    assert result["historical_OOF_rank_claimed"] is False


@pytest.mark.parametrize("size", [0, 1, 2, 10, 11, 13])
def test_zero_one_and_large_pool_keep_exact_membership_no_replacement(size):
    result = run(fixture(size))
    assert len(result["rows"]) == min(10,size)
    assert result["candidate_count"] == min(10,size)
    assert all("candidate_rank" not in row and "candidate_score" not in row for row in result["rows"])
    assert result["source_completeness_verified"] is False
    assert result["status"] == ("EMPTY_P0_CANDIDATE_INPUT" if size == 0 else "PROJECTED_UNVERIFIED_D_FEATURES")


def test_mixed_stage_selected_full_pool_values_and_all29_feature_shape():
    case = fixture(3)
    inputs, _ = case
    row = inputs[0][-1]
    row["stage"] = 3
    row["stage_transition"] = "3→4"
    row["identity"] = f"20260914|{row['ts_code']}|3→4"
    inputs[1]["rows"][-1]["stage_transition"] = "3→4"
    for r in inputs[0]:
        r.update(stage2_pool_size=2., stage3_pool_size=1., stage_pool_share=(1. if r is row else 2.)/3)
    result = run(case)
    for row in result["rows"]:
        assert len(forward._feature_row(row)) == 29
        assert all(row[key] is None for key in p.MISSING_SIGNALS)
        assert row["board_stage"] in (2,3) and type(row["promotion_rank"]) is int
        assert row["five_year_stock_prior_rate"] == .5
    assert result["rows"][-1]["stage_pool_share"] == 1/3


@pytest.mark.parametrize("name", p.FIVE_YEAR_RETAINED)
def test_other_five_year_values_preserved_with_explicit_null(name):
    case = fixture(1)
    case[0][0][0][name] = None
    out = run(case)["rows"][0]
    assert out[name] is None
    for other in p.FIVE_YEAR_RETAINED:
        if other != name:
            assert out[other] == case[0][0][0][other]


def test_prior_recomputed_not_old_cached_runtime_prior_and_preclose_unused():
    case = fixture(1)
    case[0][0][0]["five_year_stock_prior_rate"] = .123
    case[0][3] = []
    code = case[0][0][0]["ts_code"]
    bars = case[0][4][code]
    bars[-1]["pre_close"] = 999.
    bars[-1]["pct_chg"] = -99.
    result = run(case)
    assert result["rows"][0]["five_year_stock_prior_rate"] == .4
    assert result["rows"][0]["d_pct_change"] == 100.*(bars[-1]["close"]-bars[-2]["close"])/bars[-2]["close"]
    assert result["diagnostics"][0]["cold_prior_applied"] is True
    assert result["diagnostics"][0]["supplied_pre_close_used"] is False


def test_same_frozen_math_for_all_seven_daily_values_and_stale_history_disclosed():
    case = fixture(1)
    code = case[0][0][0]["ts_code"]
    expected = p.feature_math.compute_daily_features(case[0][4][code], signal_date="20260914", ts_code=code)
    result = run(case)
    assert {k:result["rows"][0][k] for k in p.DAILY_FEATURES} == expected["features"]
    assert result["observed_history_end"] == result["fixed_history_ceiling"] == "20260814"
    assert result["source_age_calendar_days"] == 31 and result["fixed_history_predates_D"] is True


@pytest.mark.parametrize("observed", [0, 1, 2, 10, 20])
def test_missing_or_short_observed_bars_block_entire_projection_not_zero(observed):
    result = run(fixture(2, observed))
    assert result["status"] == "BLOCKED_DAILY_FEATURE_HISTORY"
    assert result["projection_window_complete"] is False and len(result["rows"]) == 2
    assert len(result["blocked_candidates"]) == 2
    assert all(r[k] is None for r in result["rows"] for k in p.DAILY_FEATURES)
    assert result["unavailable_returns_imputed_zero"] is False and result["scoring_authorized"] is False


def test_suspension_missing_observation_not_silently_21_market_days():
    case = fixture(1)
    code = case[0][0][0]["ts_code"]
    case[0][4][code].pop(5)
    result = run(case)
    assert result["diagnostics"][0]["observed_bar_count"] == 20
    assert result["blocked_candidates"][0]["reason"] == "INSUFFICIENT_OBSERVED_HISTORY"


class Trap:
    def __float__(self): raise AssertionError("OUTCOME_READ")
    def __repr__(self): raise AssertionError("OUTCOME_REPR")
    def __eq__(self, other): raise AssertionError("OUTCOME_COMPARED")
    def __iter__(self): raise AssertionError("OUTCOME_ITERATED")
    def __deepcopy__(self, memo): raise AssertionError("OUTCOME_COPIED")


def test_all_unknown_columns_including_live_path_profit_and_future_outcomes_ignored():
    case = fixture(2)
    for row in case[0][0]:
        for field in (*p.MISSING_SIGNALS, "predicted_promotion_probability", "T_price", "net_return", "future_outcome"):
            row[field] = Trap()
    for row in case[0][1]["rows"]:
        row["future_outcome"] = Trap()
    for row in case[0][3]:
        row["T_plus_1_return"] = Trap()
    for bars in case[0][4].values():
        for row in bars:
            row["pre_close"] = row["future_outcome"] = Trap()
    case[0][1]["future_outcome"] = case[0][2]["future_outcome"] = Trap()
    result = run(case)
    assert result["future_outcomes_read"] is False
    assert all(row[field] is None for row in result["rows"] for field in p.MISSING_SIGNALS)


@pytest.mark.parametrize("where", ["runtime", "history", "history_after_ceiling", "bars", "model", "contract", "index"])
def test_all_dates_checked_before_any_numeric_or_outcome_value(where):
    case = fixture(2)
    inputs, _ = case
    inputs[0][0][p.FIVE_YEAR_RETAINED[0]] = Trap()
    inputs[3][0]["promotion_hit"] = Trap()
    if where == "runtime": inputs[0][-1]["signal_date"] = "20260915"
    if where == "history": inputs[3][-1]["signal_date"] = "20260914"
    if where == "history_after_ceiling": inputs[3][-1]["signal_date"] = "20260817"
    if where == "bars": inputs[4][inputs[0][-1]["ts_code"]][-1]["trade_date"] = "20260915"
    if where == "model": inputs[0][-1]["promotion_model_as_of_date"] = "20260914"
    if where == "contract": inputs[1]["feature_as_of_date"] = "20260915"
    if where == "index": inputs[2]["latest_signal_date"] = "20260915"
    with pytest.raises(ValueError): run(case)


@pytest.mark.parametrize("value", [True, "2", "2.0", 2.5, 4, None, float("nan")])
def test_bad_stage_cannot_be_coerced(value):
    case = fixture(1); case[0][0][0]["stage"] = value
    with pytest.raises(ValueError, match="STAGE_2_OR_3"): run(case)


@pytest.mark.parametrize("value", [True, 1.0, "1", 0, 2, None])
def test_runtime_rank_stays_exact_integer_and_complete(value):
    case = fixture(1); case[0][0][0]["promotion_rank"] = value
    with pytest.raises(ValueError, match="FULL_POOL_RANK"): run(case)


@pytest.mark.parametrize("change", ["truncate", "duplicate", "rank_duplicate", "selection", "top_order", "top_stage",
    "top_rank_float", "runtime_identity", "snapshot", "model_sha", "pool", "pool_share"])
def test_frozen_membership_and_full_pool_tamper_rejected(change):
    case = fixture(13); inputs, _ = case
    if change == "truncate": inputs[0].pop()
    if change == "duplicate": inputs[0][-1] = deepcopy(inputs[0][0])
    if change == "rank_duplicate": inputs[0][-1]["promotion_rank"] = 12
    if change == "selection": inputs[0][-1]["top10_selected"] = 1
    if change == "top_order": inputs[1]["rows"].reverse()
    if change == "top_stage": inputs[1]["rows"][0]["stage_transition"] = "3→4"
    if change == "top_rank_float": inputs[1]["rows"][0]["promotion_rank"] = 1.
    if change == "runtime_identity": inputs[0][0]["identity"] = "changed"
    if change == "snapshot": inputs[0][0]["feature_snapshot_sha256"] = "0"*64
    if change == "model_sha": inputs[0][0]["promotion_model_artifact_sha256"] = "0"*64
    if change == "pool": inputs[0][-1]["focus_pool_size"] = 10.
    if change == "pool_share": inputs[0][0]["stage_pool_share"] = 1.
    with pytest.raises(ValueError): run(case)


@pytest.mark.parametrize("field", p.FIVE_YEAR_RETAINED)
def test_missing_retained_numeric_is_not_implicitly_null(field):
    case = fixture(1); del case[0][0][0][field]
    with pytest.raises(ValueError, match="EXPLICIT_RETAINED"): run(case)


@pytest.mark.parametrize("value", [True, "1", [], {}, float("inf"), float("nan")])
def test_nonfinite_or_opaque_retained_feature_rejected(value):
    case = fixture(1); case[0][0][0][p.FIVE_YEAR_RETAINED[0]] = value
    with pytest.raises(ValueError): run(case)


@pytest.mark.parametrize("change", ["bar_duplicate", "bar_wrong_code", "bar_zero", "bar_missing_close",
    "bar_missing_volume", "bar_negative_volume", "extra_code", "missing_code", "history_duplicate", "history_unknown", "truth_missing"])
def test_daily_and_truth_source_shape_strict(change):
    case = fixture(1); inputs, _ = case; code = inputs[0][0]["ts_code"]
    bars = inputs[4][code]
    if change == "bar_duplicate": bars.append(deepcopy(bars[0]))
    if change == "bar_wrong_code": bars[0]["ts_code"] = "000001.SZ"
    if change == "bar_zero": bars[0]["close"] = 0
    if change == "bar_missing_close": del bars[0]["close"]
    if change == "bar_missing_volume": del bars[0]["volume"]
    if change == "bar_negative_volume": bars[0]["volume"] = -1
    if change == "extra_code": inputs[4]["000001.SZ"] = []
    if change == "missing_code": inputs[4].pop(code)
    if change == "history_duplicate": inputs[3].append(deepcopy(inputs[3][0]))
    if change == "history_unknown": inputs[3][0]["promotion_hit"] = 2
    if change == "truth_missing": del inputs[3][0]["promotion_hit"]
    with pytest.raises(ValueError): run(case)


@pytest.mark.parametrize("change", ["path", "sha", "digest", "count", "alias", "schema", "train_kind", "train_date", "train_mismatch"])
def test_declarations_and_explicit_live_train_end_required_but_not_authority(change):
    case = fixture(1); inputs, options = case
    if change == "path": inputs[2]["latest_receipt_url"] = "outputs/decision/primary_d_receipt_latest.json"
    if change == "sha": inputs[2]["latest_receipt_sha256"] = "not-a-sha"
    if change == "digest": inputs[2]["latest_bundle_sha256"] = "0"*64
    if change == "count": inputs[2]["runtime_feature_row_count"] = True
    if change == "alias": inputs[2]["data_alias"] = True
    if change == "schema": inputs[1]["schema_version"] = "something_else"
    if change == "train_kind": options["promotion_train_end_kind"] = "OOF"
    if change == "train_date": options["promotion_train_end"] = "20260914"
    if change == "train_mismatch": inputs[1]["models"]["promotion"]["model_as_of_date"] = "20260708"
    with pytest.raises(ValueError): run(case)


def test_missing_explicit_train_end_kind_is_not_defaulted():
    case = fixture(1); del case[1]["promotion_train_end_kind"]
    with pytest.raises(TypeError): run(case)


@pytest.mark.parametrize("change", ["feature", "rank", "history", "bar", "declaration", "future_history", "future_bar"])
def test_original_caller_revalidated_after_math_callbacks(monkeypatch, change):
    case = fixture(1); inputs, _ = case
    original = p.feature_math.compute_stock_prior
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if change == "feature": inputs[0][0][p.FIVE_YEAR_RETAINED[0]] += 1.
        if change == "rank": inputs[0][0]["promotion_rank"] = 2
        if change == "history": inputs[3][0]["promotion_hit"] = 0
        if change == "bar": next(iter(inputs[4].values()))[0]["close"] += .1
        if change == "declaration": inputs[2]["latest_receipt_sha256"] = "0"*64
        if change == "future_history":
            inputs[3][0]["signal_date"] = "20260914"; inputs[3][0]["promotion_hit"] = Trap()
        if change == "future_bar":
            bars = next(iter(inputs[4].values()))
            bars[-1]["trade_date"] = "20260915"; bars[-1]["close"] = Trap()
        return result
    monkeypatch.setattr(p.feature_math, "compute_stock_prior", changed)
    with pytest.raises(ValueError): run(case)


def test_deep_immutable_output_does_not_retain_mutable_caller_aliases():
    case = fixture(1)
    result = run(case)
    assert type(result) is MappingProxyType and type(result["rows"]) is tuple
    assert type(result["rows"][0]) is MappingProxyType
    assert type(result["four_file_declarations"]["receipt"]) is MappingProxyType
    with pytest.raises(TypeError): result["rows"][0]["promotion_rank"] = 2
    with pytest.raises(TypeError): result["four_file_declarations"]["receipt"]["sha256"] = "0"*64
    previous = result["rows"][0][p.FIVE_YEAR_RETAINED[0]]
    case[0][0][0][p.FIVE_YEAR_RETAINED[0]] = 999
    assert result["rows"][0][p.FIVE_YEAR_RETAINED[0]] == previous


def test_kernel_never_reads_files_uses_network_or_predicts(monkeypatch):
    case = fixture(1)
    def forbidden(*args, **kwargs): raise AssertionError("NO_IO_FIT_OR_SCORING")
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(forward, "predict_forward", forbidden)
    result = run(case)
    assert all(result[key] == expected and type(result[key]) is type(expected) for key, expected in p.FLAGS.items())
    assert result["dependency_required_sha256"]["candidate_live_feature_math.py"] == p.MATH_SHA


def test_old_math_forward_and_p0_files_not_modified():
    root = Path(__file__).resolve().parents[2]
    expected = {"work/profit_1000_upgrade/candidate_live_feature_math.py": p.MATH_SHA,
        "work/profit_1000_upgrade/candidate_forward_predict.py": "6ada7749e033b5d3e5c39c4d674d835b9113d83b4a37ef9d7e7fbad70ac95719",
        "outputs/decision/primary_d_receipt_20260911.json": "55d46d16c5c0dba2649744926f03a600d646a8fa6e186ee92632d30d1a895a11",
        "outputs/decision/primary_d_runtime_features_20260911.csv": "0e65485eb5cac60a9f43e634db55ce461d78d58e1e61a2b7edba1a5973da156a"}
    for path, sha in expected.items():
        assert hashlib.sha256((root/path).read_bytes()).hexdigest() == sha


def test_math_contract_rechecked_after_last_callback(monkeypatch):
    case = fixture(1)
    original = p.feature_math.compute_stock_prior
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        monkeypatch.setattr(p.feature_math, "REQUIRED_OBSERVED_BARS", 20)
        return result
    monkeypatch.setattr(p.feature_math, "compute_stock_prior", changed)
    with pytest.raises(ValueError, match="HISTORICAL_MATH_CONTRACT_CHANGED"):
        run(case)


def test_bound_real_p0_structure_and_math_only_opt_in(monkeypatch):
    """Actual D=20260911 shape smoke, not source/time/model authorization.

    Opt in with DC20_RUN_BOUND_P0_PROJECTION_SMOKE=1. Reuses the existing
    four-file runtime checker, original complete history and only the 21
    receipt-bound daily files. No extra bars, sources, models or T data loaded.
    All source bytes are checked at both boundaries. Missing observed bars
    remain BLOCKED; they are never synthesized to make this smoke pass.
    """
    import csv
    import gzip
    import json
    import os
    if os.environ.get("DC20_RUN_BOUND_P0_PROJECTION_SMOKE") != "1":
        pytest.skip("opt-in existing real P0 structure/math only, no source authority")
    root = Path(__file__).resolve().parents[2]
    outputs = root / "outputs/decision"
    paths = {"receipt": outputs / "primary_d_receipt_20260911.json",
        "runtime": outputs / "primary_d_runtime_features_20260911.csv",
        "contract": outputs / "three_rank_top10_20260911.json",
        "csv": outputs / "three_rank_top10_20260911.csv"}
    history_path = root / "data/decision_three_engines/five_year_supervised_ledger.csv.gz"
    pins = {paths["receipt"]: "55d46d16c5c0dba2649744926f03a600d646a8fa6e186ee92632d30d1a895a11",
        paths["runtime"]: "0e65485eb5cac60a9f43e634db55ce461d78d58e1e61a2b7edba1a5973da156a",
        paths["contract"]: "7898f87a9e936ad85a83e98bf72331e846904d564a38c2d7c0b241bd89d6bf4e",
        paths["csv"]: "5a3e5dc7f9a5a0a4556315fc8fda1307f2198eca30b9e64b0b60dc189c71f31a",
        history_path: "7cabe48da6375106b22b2c08c17a7b11780861fed319496ee26761d20fa20a46",
        root / "scripts/publish_primary_three_rank.py": "5996a8f9e0b55f34c22e4e10e78924616cf8a8ebfb08be789ed169a9b3bb8d98",
        root / "work/profit_1000_upgrade/candidate_live_feature_math.py": p.MATH_SHA,
        root / "src/top10decision/decision/d_close_features.py": "c46b7cabcab833f4dce4c984ee570a8bc3da861d00c43a95dcc45d6c17b4221e"}
    def file_sha(path):
        assert path.is_file() and path.stat().st_nlink == 1
        assert not any(item.is_symlink() for item in (path, *path.parents))
        return hashlib.sha256(path.read_bytes()).hexdigest()
    for path, sha in pins.items():
        assert file_sha(path) == sha
    own_paths = (Path(p.__file__), Path(__file__))
    own = {path:file_sha(path) for path in own_paths}
    receipt = json.loads(paths["receipt"].read_bytes())
    contract = json.loads(paths["contract"].read_bytes())
    assert receipt["signal_date"] == contract["signal_date"] == "20260911"
    from scripts import publish_primary_three_rank as publisher
    index = publisher.build_primary_d_runtime_index(root, receipt_path=paths["receipt"],
        runtime_path=paths["runtime"], three_rank_json_path=paths["contract"], three_rank_csv_path=paths["csv"])
    assert index["runtime_feature_row_count"] == index["runtime_selected_count"] == 6
    with paths["runtime"].open(encoding="utf-8-sig", newline="") as stream:
        raw_runtime = list(csv.DictReader(stream))
    # Raw decoder is test-local and explicit; projection does not coerce CSV.
    identity_keys = ("signal_date", "ts_code", "stage_transition", "identity", "feature_snapshot_sha256",
        "promotion_model_as_of_date", "promotion_model_artifact_sha256")
    assert all(row["signal_date"] == "20260911" for row in raw_runtime)
    runtime = [{**{key:row[key] for key in identity_keys}, "stage": float(row["stage"]),
        "promotion_rank": int(row["promotion_rank"]), "top10_selected": int(row["top10_selected"]),
        **{key:None if row[key] == "" else float(row[key]) for key in (*p.FIVE_YEAR_RETAINED,
            "focus_pool_size", "stage2_pool_size", "stage3_pool_size", "stage_pool_share")}} for row in raw_runtime]
    with gzip.open(history_path, "rt", encoding="utf-8-sig", newline="") as stream:
        raw_history = list(csv.DictReader(stream))
    assert len(raw_history) == 12322
    assert max(row["signal_date"] for row in raw_history) == "20260814"
    assert all(p._date(row["signal_date"]) < "20260914" and p._code(row["ts_code"]) for row in raw_history)
    history = [{"signal_date":row["signal_date"], "ts_code":row["ts_code"],
        "promotion_hit": None if row["promotion_hit"] == "" else float(row["promotion_hit"])} for row in raw_history]
    # Check the real P0 shape even if later day-price byte qualification fails.
    # No bars are supplied here: this is explicitly BLOCKED, never source PASS.
    structure = p.project_candidate_d_features(runtime, contract, index, history,
        {row["ts_code"]:[] for row in runtime}, signal_date="20260911",
        promotion_train_end=contract["models"]["promotion"]["model_as_of_date"],
        promotion_train_end_kind=p.TRAIN_END_KIND)
    assert structure["status"] == "BLOCKED_DAILY_FEATURE_HISTORY"
    assert [row["five_year_stock_prior_rate"] for row in structure["rows"]] == [1/3,.5,5/11,5/11,3/7,.4]
    assert [row["promotion_rank"] for row in structure["rows"]] == list(range(1,7))
    assert all(row[field] is None for row in structure["rows"] for field in p.DAILY_FEATURES)
    for path, sha in {**pins, **own}.items():
        assert file_sha(path) == sha
    print(json.dumps({"scope":"REAL_P0_IDENTITY_AND_PRIOR_ONLY_DAILY_NOT_SUPPLIED",
        "status":structure["status"], "rows":6, "history_rows":len(history),
        "source_authority_issued":False, "daily_features_or_scores_computed":False}))
    daily_bindings = [row for row in receipt["inputs"]["runtime_consumed_market_files"] if row["table"] == "daily"]
    assert len(daily_bindings) == 21 and len({r["trade_date"] for r in daily_bindings}) == 21
    assert sorted(r["trade_date"] for r in daily_bindings) == receipt["inputs"]["calendar"]["runtime_context_dates"]
    assert all(p._date(row["trade_date"]) <= "20260911" and row["path"] ==
        f"data/market/raw/{row['trade_date'][:4]}/{row['trade_date']}/daily.csv" for row in daily_bindings)
    for binding in daily_bindings:
        path = root/binding["path"]
        pins[path] = p._sha(binding["sha256"])
        assert file_sha(path) == pins[path]
    bars = {row["ts_code"]:[] for row in runtime}
    for binding in daily_bindings:
        with (root/binding["path"]).open(encoding="utf-8-sig", newline="") as stream:
            raw = list(csv.DictReader(stream))
        assert all(p._date(row["trade_date"]) == binding["trade_date"] for row in raw)
        for row in raw:
            if row["ts_code"] in bars:
                bars[row["ts_code"]].append({"trade_date":row["trade_date"], "ts_code":row["ts_code"],
                    "close":float(row["close"]), "volume":None if row["vol"] == "" else float(row["vol"])})
    expected_codes = [row["ts_code"] for row in contract["rows"]]
    expected_priors = [1/3, .5, 5/11, 5/11, 3/7, .4]
    def forbidden(*args, **kwargs): raise AssertionError("NO_FORWARD_FIT_OR_NETWORK")
    monkeypatch.setattr(forward, "predict_forward", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    result = p.project_candidate_d_features(runtime, contract, index, history, bars,
        signal_date="20260911", promotion_train_end=contract["models"]["promotion"]["model_as_of_date"],
        promotion_train_end_kind=p.TRAIN_END_KIND)
    assert [row["ts_code"] for row in result["rows"]] == expected_codes
    assert [row["five_year_stock_prior_rate"] for row in result["rows"]] == expected_priors
    assert [row["promotion_rank"] for row in result["rows"]] == list(range(1,7))
    assert result["candidate_count"] == result["full_pool_size"] == 6
    short = any(len(rows) < 21 or max((r["trade_date"] for r in rows), default=None) != "20260911" for rows in bars.values())
    assert result["status"] == ("BLOCKED_DAILY_FEATURE_HISTORY" if short else "PROJECTED_UNVERIFIED_D_FEATURES")
    assert result["source_independently_verified"] is result["natural_freeze_verified"] is False
    assert result["model_predictions_computed"] is result["production_activation_allowed"] is False
    for path, sha in {**pins, **own}.items():
        assert file_sha(path) == sha
    assert publisher.build_primary_d_runtime_index(root, receipt_path=paths["receipt"], runtime_path=paths["runtime"],
        three_rank_json_path=paths["contract"], three_rank_csv_path=paths["csv"]) == index
    print(json.dumps({"scope":"BOUND_REAL_P0_STRUCTURE_AND_MATH_ONLY_NOT_SOURCE_AUTHORITY", "D":"20260911",
        "status":result["status"], "rows":result["candidate_count"], "full_pool":result["full_pool_size"],
        "observed_bars":{code:len(rows) for code,rows in bars.items()}, "daily_files":len(daily_bindings),
        "history_rows":len(history), "history_end":result["observed_history_end"],
        "stock_priors":[row["five_year_stock_prior_rate"] for row in result["rows"]],
        "original_bytes_unchanged":True, "source_or_freeze_authority_issued":False}, sort_keys=True))
