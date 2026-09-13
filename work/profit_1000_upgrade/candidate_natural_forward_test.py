"""Synthetic P0 sources + the exact serialized 999666 model; no natural claims.

Portable tests explicitly substitute ONLY the synthetic evaluation-envelope hash
and synthetic P0 history/index fixture. The model parameters/hash never change.
The opt-in test uses the real 779baaff evaluation bytes with no model/source
envelope override. All injected clocks are marked TEST; no network or fitting.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket

import pytest

from work.profit_1000_upgrade import candidate_natural_forward as m
from work.profit_1000_upgrade.test_candidate_d_source_adapter import make_case

FIXED_MODEL = json.loads(r'''{
  "coefficients": [
    -3.32598345595214E-17,
    -0.0003952932781683351,
    3.4953148640028993E-17,
    -0.0009700300178255796,
    0.002526389861305913,
    0.0030807060852964872,
    -0.0016418283616077196,
    0.0012075284406619007,
    -0.0040463488008996166,
    0.005238293162410398,
    0.0038330658495195004,
    -0.00023544787140359578,
    0.004420839318036452,
    -0.0009655273149327933,
    -0.0041687883784989384,
    0.002938863993197129,
    -0.0018614859964359873,
    0.0005572591795166472,
    0.0010345137373285352,
    -0.0012368047784865476,
    0.00012219633809910043,
    -0.0001221963380991258,
    2.1156680821240232E-16,
    9.626961798919105E-18,
    -7.057439478336514E-17,
    4.456109815554386E-17,
    -8.028154111812024E-18,
    -2.1126321633583844E-16,
    -8.339896044755589E-18
  ],
  "entry_policy_id": "research_canonical_price_capacity_split_no_cap_v3",
  "features": [
    "promotion_probability",
    "promotion_rank",
    "path_change",
    "d_pct_change",
    "volume_ratio",
    "ret_2d",
    "ret_5d",
    "ret_10d",
    "volatility_5d",
    "volatility_20d",
    "stage_pool_share",
    "five_year_board_stage_delta",
    "five_year_streak_runup",
    "five_year_pre_streak_1d_return",
    "five_year_recent_20d_rate",
    "five_year_recent_60d_rate",
    "five_year_stock_prior_rate",
    "focus_pool_size",
    "stage2_pool_size",
    "stage3_pool_size",
    "stage_2",
    "stage_3",
    "path_持续强势",
    "path_弱转强",
    "path_加速一致",
    "path_强转弱",
    "path_持续弱势",
    "path_分歧回封",
    "path_路径混合"
  ],
  "fit_label_available_date_max": "20251110",
  "fit_signal_date_max": "20251106",
  "fit_signal_date_min": "20221111",
  "frozen_source_sha256": "b3addf99a0f30c784b6a2ae190c3bf6f67f9b1b4a64325193b8d962d6ee2dedd",
  "holdout_start_date": "20260914",
  "imputation_train_medians": [
    0.0,
    4.0,
    0.0,
    10.0,
    1.506063526,
    0.2101105845,
    0.2555366269,
    0.284019975,
    0.05082557029,
    0.04241886753,
    0.7,
    -0.00005326470573,
    0.2104519774,
    0.002141327623,
    0.4053843314,
    0.398422591,
    0.4166666667,
    9.0,
    7.0,
    2.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0
  ],
  "input_bindings": {
    "base_archive_sha256": "f35140230a777b58276e58e861ec63076f375a27b0f11d32bd170174658d44ad",
    "candidate_collection_receipt_sha256": "8523d4f7a8ed83154dbfdec19fc55fc93e6789d445321016e4fdb7046fd910ae",
    "development_frozen_rows_sha256": "34be0f2a3b5d405b0279d71e1570c7b0e6d1348d5924fb1dba7f3a3f537578b7",
    "development_label_rows_sha256": "d49d13d387d6247033de2ae332bce6e126ea06ca408cfdcd338d7cb3ae6c5433",
    "label_report_sha256": "c5850ce7ebbd7f3761dbd741c437b770d48f13bbbc388e47f5efac891180fa5e",
    "market_collection_receipt_sha256": "f4a346a2edb3d62397a396722a350b2b52a983eb61c5027e12ed4252d868b65b",
    "source_sha256": "b3addf99a0f30c784b6a2ae190c3bf6f67f9b1b4a64325193b8d962d6ee2dedd"
  },
  "intercept": -0.008644553806639465,
  "label_policy_id": "dc20_exit_1000_limit_hold_20260912_v1",
  "numpy_version": "2.5.2",
  "production_activation_allowed": false,
  "provider_timestamp_semantics_confirmed": false,
  "research_only": true,
  "round_trip_cost_rate": 0.0045,
  "scaling_train_means": [
    0.0,
    4.609510922095496,
    0.0,
    10.007135387682377,
    2.152239494524777,
    0.2101870045692829,
    0.2655675644698662,
    0.30582673658007054,
    0.05175728243017492,
    0.04473021156679885,
    0.6420989957950699,
    -0.000685346577279702,
    0.24647511587803958,
    -0.000649575734256158,
    0.4109404379094726,
    0.4101181860303317,
    0.4307547082381809,
    11.634834718731877,
    8.488497970230041,
    3.1463367485018363,
    0.6850956891552291,
    0.31490431084477094,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0
  ],
  "scaling_train_scales": [
    1.0,
    2.6862197497786457,
    1.0,
    0.05478547500479695,
    2.2311024065613254,
    0.0009614381932908512,
    0.09626132787889445,
    0.16930755237086567,
    0.012780171509583793,
    0.010171221916815788,
    0.24102997684894412,
    0.012167661917554798,
    0.05548196215596091,
    0.03489789465235815,
    0.08788372238969532,
    0.07366968004193909,
    0.10071362919363774,
    12.887303177110113,
    10.880122546220178,
    3.2940269265571254,
    0.46447775604023517,
    0.46447775604023517,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0
  ],
  "schema_version": "dc20_profit_1000_candidate_v3",
  "sklearn_version": "1.9.0",
  "source_overlay_contract": {
    "auction_qualification_policy_id": "dc20_research_canonical_price_capacity_split_20260912_v3",
    "auction_source_policy_ids": [
      "dc20_research_canonical_price_capacity_split_20260912_v3",
      "dc20_research_candidate_auction_source_20260913_v1"
    ],
    "complete_empty_response_rule": "DAILY_OPEN_PROXY_CAPACITY_UNKNOWN",
    "entry_policy_id": "research_canonical_price_capacity_split_no_cap_v3",
    "exit_policy_id": "dc20_exit_1000_limit_hold_20260912_v1",
    "minute_source_policy_id": "dc20_research_stk_mins_query_0931_v1",
    "minute_time_semantics": "RESEARCH_BAR_END_ASSUMPTION_NOT_PROVIDER_CONFIRMED",
    "missing_rejected_invalid_response_rule": "PENDING_NOT_FALLBACK_NOT_ZERO",
    "overlay_policy_id": "dc20_candidate_auction_label_overlay_20260913_v1",
    "round_trip_cost_rate": 0.0045
  },
  "specification": {
    "alpha": 10.0,
    "estimator": "Ridge",
    "fit_intercept": true,
    "hyperparameter_search": false,
    "solver": "svd",
    "target": "slot_net_return"
  },
  "training_cutoff_date": "20251111"
}''')
ACTUAL_EVALUATION = Path("/Users/moclh/Documents/ChatGPT/DC20/upgrade-candidate-evidence-20260913.VdRAd5/fixed-development-evaluation-pf5wb5u1/candidate_development_evaluation.json")
FIXED_EVAL_SHA = "779baaffb008165e88a88f568497d42b410dcbec03e9f613b099464d245a4872"


def stamp(value="2026-09-14T12:00:00+00:00"):
    return datetime.fromisoformat(value)


def setup_case(tmp_path, monkeypatch, *, size=2, missing_bar=False, actual=False):
    case = make_case(tmp_path, monkeypatch, size=size, missing_bar=missing_bar)
    output = tmp_path.resolve() / "candidate_natural_forward"
    assert m.scorer.canonical_sha(FIXED_MODEL) == m.MODEL_SHA
    if actual:
        assert hashlib.sha256(ACTUAL_EVALUATION.read_bytes()).hexdigest() == FIXED_EVAL_SHA
        evaluation = ACTUAL_EVALUATION
    else:
        evaluation = tmp_path.resolve() / "synthetic_envelope_exact_real_model.json"
        evaluation.write_text(json.dumps({"status": "DEVELOPMENT_ONLY_FITTED", "candidate_model": FIXED_MODEL}))
        synthetic_sha = hashlib.sha256(evaluation.read_bytes()).hexdigest()
        monkeypatch.setattr(m, "EVALUATION_SHA", synthetic_sha)
        plan = json.loads(m.REGISTRATION_PATH.read_bytes())
        plan["evaluation_file_sha256"] = synthetic_sha
        registration = tmp_path.resolve() / "synthetic_registration.json"
        registration.write_text(json.dumps(plan))
        monkeypatch.setattr(m, "REGISTRATION_PATH", registration)
    case.update(output=output, evaluation=evaluation)
    return case


def run(case, **options):
    return m.freeze_natural_day(case["root"], case["output"], case["evaluation"],
        signal_date=options.pop("signal_date", case["day"]),
        expected_p0_sha256=case["expected"], source_path_map=case["map"],
        clock=options.pop("clock", lambda: stamp()), **options)


def record(receipt):
    return json.loads(Path(receipt["snapshot_path"]).read_bytes())


@pytest.mark.parametrize("size", [0, 1, 2, 10, 13])
def test_real_fixed_model_synthetic_source_saves_all_ranks_and_explicit_slots(tmp_path, monkeypatch, size):
    case = setup_case(tmp_path, monkeypatch, size=size)
    before = {p: p.read_bytes() for p in case["root"].rglob("*") if p.is_file()}
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("NETWORK"))
    result = run(case)
    out = record(result)
    assert result["status"] == "LOCAL_RESEARCH_SNAPSHOT_FROZEN"
    assert result["new_snapshot_written"] and result["local_freeze_completed_before_cutoff"]
    assert out["model_canonical_sha256"] == m.MODEL_SHA
    assert len(out["prediction"]["rows"]) == min(10, size)
    for key in ("candidate_slots", "promotion_slots"):
        assert len(out[key]) == 2
        assert [r["slot"] for r in out[key]] == [1, 2]
        for row in out[key][min(2, size):]:
            assert row["status"] == "MISSING_CANDIDATE" and row["ts_code"] is None
        assert all(r["slot_net_return"] is None and r["net_return"] is None for r in out[key])
    if size:
        assert any(r["candidate_score"] < 0 for r in out["prediction"]["rows"])
        assert [r["candidate_rank"] for r in out["prediction"]["rows"]] == list(range(1, min(10, size)+1))
    assert out["D_source_evidence"]["projection"]["full_pool_size"] == size
    assert out["D_source_evidence"]["source_authority_issued"] is False
    assert out["prediction"]["model_source_independently_verified"] is False
    assert out["clock_mode"] == "INJECTED_TEST_CLOCK_RESEARCH_ONLY"
    for key in ("production_activation_allowed", "source_authority_issued", "git_publication_verified",
                "natural_forward_admission_issued", "formal_ledger_written", "model_retrained"):
        assert out[key] is result[key] is False
    assert out["replacement_gate"] == "NOT_CONFIGURED"
    assert m._sealed(out)
    assert hashlib.sha256(Path(result["snapshot_path"]).read_bytes()).hexdigest() == result["snapshot_file_sha256"]
    assert before == {p: p.read_bytes() for p in case["root"].rglob("*") if p.is_file()}


def test_scores_equal_unmodified_pure_scorer_and_model_bytes_not_rewritten(tmp_path, monkeypatch):
    case = setup_case(tmp_path, monkeypatch)
    before = case["evaluation"].read_bytes()
    out = record(run(case))
    rows = [dict(row) for row in m.source.project_bound_p0_d(case["root"], signal_date=case["day"],
        expected_p0_sha256=case["expected"])["projection"]["rows"]]
    pure = m.scorer.predict_forward(FIXED_MODEL, rows, signal_date=case["day"], expected_model_sha256=m.MODEL_SHA)
    assert out["prediction"] == pure
    assert case["evaluation"].read_bytes() == before
    assert out["prediction_generated_at_utc"] == "2026-09-14T12:00:00.000000+00:00"


def test_same_D_is_idempotent_only_with_external_prior_sha_and_preserves_original_time(tmp_path, monkeypatch):
    case = setup_case(tmp_path, monkeypatch)
    first = run(case); original = Path(first["snapshot_path"]).read_bytes()
    with pytest.raises(ValueError, match="EXTERNAL_EXISTING"):
        run(case)
    second = run(case, expected_existing_snapshot_sha256=first["snapshot_file_sha256"],
        clock=lambda: stamp("2026-09-16T08:00:00+00:00"))
    assert second["status"] == "EXISTING_IDENTICAL_SNAPSHOT_REVALIDATED_NO_NEW_ADMISSION"
    assert not second["new_snapshot_written"] and not second["local_freeze_completed_before_cutoff"]
    assert Path(first["snapshot_path"]).read_bytes() == original


@pytest.mark.parametrize("kind", ["tamper", "reseal_time", "wrong_external_sha"])
def test_existing_tamper_cannot_be_retimestamped_or_replaced(tmp_path, monkeypatch, kind):
    case = setup_case(tmp_path, monkeypatch)
    first = run(case); path = Path(first["snapshot_path"]); out = record(first)
    supplied_sha = first["snapshot_file_sha256"]
    if kind == "wrong_external_sha":
        supplied_sha = "0" * 64
    else:
        out["prediction_generated_at_utc"] = "2026-09-14T08:00:00+00:00"
        if kind == "reseal_time":
            del out["snapshot_sha256"]
            out["snapshot_sha256"] = m.scorer.canonical_sha(out)
        path.write_bytes(m.storage.encoded(out))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="EXTERNAL_EXISTING"):
        run(case, expected_existing_snapshot_sha256=supplied_sha)
    assert path.read_bytes() == before


@pytest.mark.parametrize("now", ["2026-09-14T06:59:59+00:00", "2026-09-15T01:25:00+00:00",
                                 "2026-09-15T01:25:00.000001+00:00", "2026-09-16T01:00:00+00:00"])
def test_before_close_or_late_first_freeze_rejected_without_snapshot(tmp_path, monkeypatch, now):
    case = setup_case(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="BEFORE_D_CLOSE|LATE_FIRST_FREEZE"):
        run(case, clock=lambda: stamp(now))
    assert not case["output"].exists()


def test_cas_crossing_cutoff_never_issues_success_receipt(tmp_path, monkeypatch):
    case = setup_case(tmp_path, monkeypatch)
    moments = iter([stamp(), stamp(), stamp("2026-09-15T01:24:59.999999+00:00"),
                    stamp("2026-09-15T01:25:00+00:00")])
    result = run(case, clock=lambda: next(moments))
    assert result["status"] == "BLOCKED_POST_CAS_DEADLINE_SNAPSHOT_NOT_ADMITTED"
    assert result["new_snapshot_written"] and not result["local_freeze_completed_before_cutoff"]
    assert record(result)["natural_forward_admission_issued"] is False


def test_final_persisted_guard_crossing_cutoff_never_claims_completed_before_cutoff(tmp_path, monkeypatch):
    case = setup_case(tmp_path, monkeypatch)
    current = [stamp("2026-09-15T01:24:59.999999+00:00")]
    original = m._read
    target = case["output"] / ("day_" + case["day"] + ".json")
    def read(*a, **k):
        result = original(*a, **k)
        if Path(a[0]) == target:
            current[0] = stamp("2026-09-15T01:25:00+00:00")
        return result
    monkeypatch.setattr(m, "_read", read)
    result = run(case, clock=lambda: current[0])
    assert result["status"] == "BLOCKED_POST_CAS_DEADLINE_SNAPSHOT_NOT_ADMITTED"
    assert result["local_operation_completed_at_utc"] == "2026-09-15T01:25:00.000000+00:00"
    assert result["local_freeze_completed_before_cutoff"] is False


@pytest.mark.parametrize("clock", [lambda: datetime(2026, 9, 14, 12), lambda: "2026-09-14T12:00:00Z"])
def test_invalid_clock_type_or_timezone_rejected(tmp_path, monkeypatch, clock):
    case = setup_case(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="AWARE_UTC"):
        run(case, clock=clock)


def test_backward_clock_rejected(tmp_path, monkeypatch):
    case = setup_case(tmp_path, monkeypatch)
    moments = iter([stamp(), stamp("2026-09-14T11:59:59+00:00")])
    with pytest.raises(ValueError, match="CLOCK_MOVED"):
        run(case, clock=lambda: next(moments))
    assert not case["output"].exists()


@pytest.mark.parametrize("day", ["20260911", "20260814", "bad"])
def test_old_D_rejected_before_model_or_source_io(tmp_path, monkeypatch, day):
    monkeypatch.setattr(m, "_read", lambda *a: pytest.fail("OLD_D_READ"))
    with pytest.raises(ValueError):
        m.freeze_natural_day(tmp_path, tmp_path, tmp_path, signal_date=day, expected_p0_sha256={})


def test_missing_observed_bar_blocks_without_partial_slots(tmp_path, monkeypatch):
    case = setup_case(tmp_path, monkeypatch, missing_bar=True)
    with pytest.raises(ValueError, match="PROJECTION_BLOCKED"):
        run(case)
    assert not case["output"].exists()


@pytest.mark.parametrize("where", ["model", "source", "same_bytes_new_inode", "registration", "arguments"])
def test_mid_score_mutation_prevents_freeze(tmp_path, monkeypatch, where):
    case = setup_case(tmp_path, monkeypatch)
    original = m.scorer.predict_forward
    def changed(*a, **k):
        value = original(*a, **k)
        if where == "model":
            case["evaluation"].write_bytes(case["evaluation"].read_bytes() + b" ")
        elif where in ("source", "same_bytes_new_inode"):
            path = case["root"] / m.source.CALENDAR_PATH
            if where == "source":
                path.write_bytes(path.read_bytes() + b"\n")
            else:
                replacement = path.with_suffix(".replacement")
                replacement.write_bytes(path.read_bytes()); replacement.replace(path)
        elif where == "registration":
            m.REGISTRATION_PATH.write_bytes(m.REGISTRATION_PATH.read_bytes() + b" ")
        else:
            case["expected"]["receipt"] = "0" * 64
        return value
    monkeypatch.setattr(m.scorer, "predict_forward", changed)
    with pytest.raises(ValueError, match="CHANGED"):
        run(case)
    assert not case["output"].exists()


@pytest.mark.parametrize("target", ["wrong_name", "source_data", "source_outputs", "source_models", "source_forward", "symlink"])
def test_isolated_output_cannot_enter_production_inputs_or_aliases(tmp_path, monkeypatch, target):
    case = setup_case(tmp_path, monkeypatch)
    if target == "wrong_name":
        case["output"] = tmp_path / "not_allowed"
    elif target == "symlink":
        destination = tmp_path / "real"; destination.mkdir()
        case["output"].symlink_to(destination, target_is_directory=True)
    else:
        folder = target.removeprefix("source_")
        case["output"] = case["root"] / folder / "candidate_natural_forward"
    with pytest.raises(ValueError, match="OUTPUT|UNALIASED"):
        run(case)


def test_model_envelope_or_wrong_sha_rejected_without_scoring(tmp_path, monkeypatch):
    case = setup_case(tmp_path, monkeypatch)
    case["evaluation"].write_text("{}")
    monkeypatch.setattr(m.scorer, "predict_forward", lambda *a, **k: pytest.fail("SCORE"))
    with pytest.raises(ValueError, match="FIXED_REAL_EVALUATION"):
        run(case)


@pytest.mark.parametrize("field,value", [("production_activation_allowed", True), ("start_signal_date", "20260911"),
    ("replacement_review_thresholds", 60), ("shadow_notional_cny", 200000), ("round_trip_cost_rate", .009),
    ("same_D_replacement_allowed", True)])
def test_registration_has_no_activation_or_policy_switch(tmp_path, monkeypatch, field, value):
    case = setup_case(tmp_path, monkeypatch)
    plan = json.loads(m.REGISTRATION_PATH.read_bytes()); plan[field] = value
    m.REGISTRATION_PATH.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="FIXED_REGISTRATION"):
        run(case)


def test_duplicate_json_registration_rejected(tmp_path, monkeypatch):
    case = setup_case(tmp_path, monkeypatch)
    m.REGISTRATION_PATH.write_text('{"same":1,"same":2}')
    with pytest.raises(ValueError, match="DUPLICATE_JSON"):
        run(case)


def test_cas_conflict_does_not_overwrite_winner(tmp_path, monkeypatch):
    case = setup_case(tmp_path, monkeypatch)
    original = m.storage.compare_and_swap
    def conflict(path, value, expected):
        path.parent.mkdir(parents=True)
        path.write_text('{"concurrent":"winner"}')
        return original(path, value, expected)
    monkeypatch.setattr(m.storage, "compare_and_swap", conflict)
    with pytest.raises(ValueError, match="CAS conflict"):
        run(case)
    assert json.loads((case["output"] / ("day_" + case["day"] + ".json")).read_bytes()) == {"concurrent": "winner"}


def test_no_clock_activation_model_override_cli_flags():
    required = ["--source-root", "/source", "--output-root", "/candidate_natural_forward",
        "--model-evaluation", "/model", "--signal-date", "20260914", "--p0-hashes", "/hashes"]
    for flag in ("--now", "--activate", "--model-sha", "--registration"):
        with pytest.raises(SystemExit):
            m.main([*required, flag, "untrusted"])


def test_cli_passes_only_explicit_hashes_paths_and_uses_no_clock_override(tmp_path, monkeypatch, capsys):
    case = setup_case(tmp_path, monkeypatch)
    hashes = tmp_path.resolve() / "four_hashes.json"
    hashes.write_text(json.dumps(case["expected"]))
    calls = []
    def freeze(*a, **k):
        calls.append((a, k))
        return {"status": "LOCAL_RESEARCH_SNAPSHOT_FROZEN", "natural_forward_admission_issued": False}
    monkeypatch.setattr(m, "freeze_natural_day", freeze)
    assert m.main(["--source-root", str(case["root"]), "--output-root", str(case["output"]),
        "--model-evaluation", str(case["evaluation"]), "--signal-date", case["day"],
        "--p0-hashes", str(hashes)]) == 0
    assert len(calls) == 1
    assert "clock" not in calls[0][1]
    assert calls[0][1]["expected_p0_sha256"] == case["expected"]
    assert json.loads(capsys.readouterr().out)["natural_forward_admission_issued"] is False


def test_changed_identity_is_rejected_before_any_file_body_read(tmp_path, monkeypatch):
    case = setup_case(tmp_path, monkeypatch)
    _, identity = m._read(case["evaluation"])
    case["evaluation"].write_bytes(case["evaluation"].read_bytes() + b" ")
    monkeypatch.setattr(Path, "open", lambda *a, **k: pytest.fail("CHANGED_BODY_READ_BEFORE_IDENTITY_CHECK"))
    with pytest.raises(ValueError, match="SOURCE_IDENTITY_CHANGED_BEFORE_READ"):
        m._read(case["evaluation"], identity)


def test_single_link_file_required(tmp_path, monkeypatch):
    case = setup_case(tmp_path, monkeypatch)
    os.link(case["evaluation"], tmp_path / "second_link")
    with pytest.raises(ValueError, match="SINGLE_LINK"):
        run(case)
    assert not case["output"].exists()


@pytest.mark.parametrize("kind", ["model", "source", "stored_snapshot", "backward_clock"])
def test_post_cas_failure_leaves_only_non_authoritative_snapshot_without_success_receipt(tmp_path, monkeypatch, kind):
    case = setup_case(tmp_path, monkeypatch)
    original = m.storage.compare_and_swap
    def changed(path, value, expected):
        result = original(path, value, expected)
        if kind == "model":
            case["evaluation"].write_bytes(case["evaluation"].read_bytes() + b" ")
        elif kind == "source":
            source = case["root"] / m.source.CALENDAR_PATH
            source.write_bytes(source.read_bytes() + b"\n")
        elif kind == "stored_snapshot":
            stored = json.loads(path.read_bytes()); stored["signal_date"] = "20990101"
            path.write_bytes(m.storage.encoded(stored))
        return result
    monkeypatch.setattr(m.storage, "compare_and_swap", changed)
    options = {}
    if kind == "backward_clock":
        moments = iter([stamp(), stamp(), stamp(), stamp("2026-09-14T11:59:59+00:00")])
        options["clock"] = lambda: next(moments)
    with pytest.raises(ValueError, match="CHANGED|NOT_ADMITTED"):
        run(case, **options)
    saved = json.loads((case["output"] / ("day_" + case["day"] + ".json")).read_bytes())
    assert saved["natural_forward_admission_issued"] is False
    assert saved["git_publication_verified"] is False


def test_prior_snapshot_expected_but_missing_cannot_create_new_day(tmp_path, monkeypatch):
    case = setup_case(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="EXPECTED_PRIOR_SNAPSHOT_MISSING"):
        run(case, expected_existing_snapshot_sha256="0" * 64)
    assert not case["output"].exists()


def test_real_779_evaluation_with_unmodified_999_model_opt_in(tmp_path, monkeypatch):
    if os.environ.get("DC20_RUN_FIXED_NATURAL_MODEL_SMOKE") != "1":
        pytest.skip("opt-in real evaluation bytes; P0 and time remain synthetic, never natural admission")
    case = setup_case(tmp_path, monkeypatch, actual=True)
    result = run(case)
    assert record(result)["model_evaluation"]["sha256"] == FIXED_EVAL_SHA
    assert m.EVALUATION_SHA == FIXED_EVAL_SHA
    assert not result["natural_forward_admission_issued"]
