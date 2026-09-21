"""Eligibility regression: synthetic arithmetic, never production evidence."""
from copy import deepcopy
import itertools
import pytest
from work.profit_1000_upgrade import candidate_forward_predict as scorer
from work.profit_1000_upgrade import candidate_eligible_pool as m


def model():
    return {"schema_version": scorer.MODEL_SCHEMA, "research_only": True, "production_activation_allowed": False,
        "label_policy_id": scorer.EXIT_POLICY_ID, "entry_policy_id": scorer.ENTRY_POLICY_ID,
        "specification": deepcopy(scorer.MODEL_SPEC), "features": list(scorer.FEATURES),
        "imputation_train_medians": [0.] * 29, "scaling_train_means": [0.] * 29,
        "scaling_train_scales": [1.] * 29, "coefficients": [0.] * 29, "intercept": -1.,
        "sklearn_version": "1.7.2", "numpy_version": "2.3.2", "fit_signal_date_min": "20221111",
        "fit_signal_date_max": "20251107", "fit_label_available_date_max": "20251110",
        "round_trip_cost_rate": .0045, "frozen_source_sha256": scorer.SOURCE_SHA,
        "source_overlay_contract": deepcopy(scorer.OVERLAY_CONTRACT),
        "input_bindings": {**{k: "a" * 64 for k in scorer.BINDING_KEYS},
            "source_sha256": scorer.SOURCE_SHA, "base_archive_sha256": scorer.BASE_ARCHIVE_SHA},
        "provider_timestamp_semantics_confirmed": False,
        "training_cutoff_date": scorer.TRAIN_CUTOFF, "holdout_start_date": scorer.FORWARD_START}


def projection(count=10, excluded=()):
    rows = [{**dict.fromkeys(scorer.NUMERIC_FEATURES), **dict.fromkeys(scorer.MISSING_SIGNALS),
        "signal_date": "20260921", "feature_as_of_date": "20260921", "promotion_oof_train_end": "20260911",
        "promotion_rank": n, "board_stage": 2 if n % 2 else 3, "ts_code": f"{600000+n:06}.SH",
        "d_pct_change": None if n in excluded else 10.} for n in range(1, count+1)]
    diagnostics = [{"ts_code": row["ts_code"], "observed_bar_count": 15 if row["promotion_rank"] in excluded else 21,
        "observed_window_requirement_met": row["promotion_rank"] not in excluded,
        "daily_status": "INSUFFICIENT_OBSERVED_HISTORY" if row["promotion_rank"] in excluded else "UNVERIFIED_DAILY_FEATURES_COMPUTED"} for row in rows]
    blocked = [{"ts_code": d["ts_code"], "reason": d["daily_status"]} for d in diagnostics if not d["observed_window_requirement_met"]]
    return {"signal_date": "20260921", "candidate_count": count, "rows": rows, "diagnostics": diagnostics,
        "blocked_candidates": blocked, "projection_window_complete": not blocked,
        "status": "BLOCKED_DAILY_FEATURE_HISTORY" if blocked else ("PROJECTED_UNVERIFIED_D_FEATURES" if rows else "EMPTY_P0_CANDIDATE_INPUT")}


def predict(p, mod=None):
    mod = mod or model()
    return m.predict_eligible(mod,p,expected_model_sha256=scorer.canonical_sha(mod))


@pytest.mark.parametrize("excluded", [(n,) for n in range(1,11)] + [(1,2,3),(2,5,8),tuple(range(1,10)),tuple(range(1,11))])
def test_one_or_more_short_histories_do_not_block_eligible_stocks(excluded):
    p = projection(excluded=excluded)
    before = deepcopy(p)
    result = predict(p)
    assert p == before
    assert result["eligible_candidate_count"] == 10-len(excluded)
    assert [r["promotion_rank"] for r in result["rows"]] == [n for n in range(1,11) if n not in excluded]
    assert [s["promotion_rank"] for s in result["promotion_slots"]] == [1,2,3]
    assert all(r["candidate_score"] == -1 for r in result["rows"])
    assert [r["candidate_rank"] for r in result["rows"]] == list(range(1,11-len(excluded)))
    assert sum(s["status"]=="SELECTED" for s in result["candidate_slots"]) == min(2,10-len(excluded))
    assert result["frozen_selection_issued"] is False
    assert result["natural_forward_ledger_written"] is False


@pytest.mark.parametrize("count", range(11))
def test_full_eligible_pool_is_bit_exact_with_fixed_scorer(count):
    mod = model()
    mod["coefficients"] = [(i-13)*.003 for i in range(29)]
    mod["imputation_train_medians"] = [i*.1 for i in range(29)]
    p = projection(count=count)
    assert predict(p,mod)["rows"] == scorer.predict_forward(mod,p["rows"],
        expected_model_sha256=scorer.canonical_sha(mod),signal_date=p["signal_date"])["rows"]


def test_gap_in_promotion_ranks_preserves_same_model_score_and_original_rank():
    p = projection(excluded=(1,4,6))
    mod = model(); mod["coefficients"][1] = -.03
    actual = predict(p,mod)
    reference = scorer.predict_forward(mod,p["rows"],expected_model_sha256=scorer.canonical_sha(mod),signal_date=p["signal_date"])
    expected = [r for r in reference["rows"] if r["promotion_rank"] not in (1,4,6)]
    assert [(r["ts_code"],r["candidate_score"],r["promotion_rank"]) for r in actual["rows"]] == [(r["ts_code"],r["candidate_score"],r["promotion_rank"]) for r in expected]


@pytest.mark.parametrize("mutation", ["status","count","duplicate","date","fake_complete","missing_reason","bad_feature","partial_feature"])
def test_inconsistent_or_corrupt_source_remains_fatal(mutation):
    p = projection(excluded=(1,))
    if mutation == "status": p["status"] = "PROJECTED_UNVERIFIED_D_FEATURES"
    if mutation == "count": p["candidate_count"] = 9
    if mutation == "duplicate": p["diagnostics"][1] = p["diagnostics"][0]
    if mutation == "date": p["rows"][2]["signal_date"] = "20260922"
    if mutation == "fake_complete": p["diagnostics"][0]["observed_window_requirement_met"] = True
    if mutation == "missing_reason": p["blocked_candidates"] = []
    if mutation == "bad_feature": p["rows"][2]["ret_2d"] = float("nan")
    if mutation == "partial_feature": p["rows"][0]["ret_2d"] = .1
    with pytest.raises(ValueError): predict(p)


def test_all_1024_masks_keep_top3_and_exactly_two_profit_slots_when_possible():
    for flags in itertools.product((False,True),repeat=10):
        excluded = tuple(i+1 for i,flag in enumerate(flags) if flag)
        r = predict(projection(excluded=excluded))
        assert [s["ts_code"] for s in r["promotion_slots"]] == ["600001.SH","600002.SH","600003.SH"]
        assert len(r["candidate_slots"]) == 2
        if 10-len(excluded)>=2:
            assert r["status"] == "SCORED_ELIGIBLE_POOL"
            assert all(s["status"] == "SELECTED" for s in r["candidate_slots"])
        else:
            assert r["status"] == "INSUFFICIENT_ELIGIBLE_CANDIDATES"
            assert all(s["net_return"] is None for s in r["candidate_slots"])
