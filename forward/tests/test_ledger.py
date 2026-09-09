import copy
import hashlib
import json

import pytest

from forward.ledger import freeze_day, freeze_day_sha256, new_ledger, record_verification


DATES = ["20260904", "20260907", "20260908", "20260909", "20260910", "20260911", "20260914"]


def make_day(n=3, signal="20260907", generated="2026-09-07T13:00:00Z"):
    pos = DATES.index(signal)
    return {"signal_date": signal, "exec_date": DATES[pos + 1], "exit_date": DATES[pos + 2],
            "generated_at_utc": generated, "generation_mode": "NATURAL",
            "source": {"model_sha256": "a" * 64, "source_commit": "b" * 40,
                       "snapshot_created_at_utc": generated},
            "rows": [{"ts_code": f"{600000 + i:06d}.SH", "name": f"公司{i}", "industry": "测试",
                      "stage_transition": "2→3", "promotion_rank": i,
                      "promotion_probability": 0.9 - i * 0.05, "path_label": None,
                      "path_change_pct": None, "profit_rank": n + 1 - i,
                      "profit_score": 0.3 + i * 0.01} for i in range(1, n + 1)]}


def make_ledger(n=3):
    return freeze_day(new_ledger("new-epoch", "2026-09-07T07:00:00Z"), make_day(n),
                      open_dates=DATES, now_utc="2026-09-07T13:00:01Z")


def make_verification(day, as_of="20260909", status="SETTLED", returns=None):
    returns = returns if returns is not None else [0.1] * len(day["rows"])
    result = {key: day[key] for key in ("signal_date", "exec_date", "exit_date", "freeze_sha256")}
    result.update(as_of_date=as_of, rows=[], costs_bps=45.0, price_basis="daily_open_proxy", research_only=True,
                  members_sha256=hashlib.sha256(json.dumps([row["ts_code"] for row in day["rows"]], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
    for row, value in zip(day["rows"], returns):
        settled = status == "SETTLED"
        no_fill = status == "NO_FILL"
        result["rows"].append({"ts_code": row["ts_code"],
                               "t_status": "PROMOTED" if settled or no_fill else status if status in {"PENDING", "MISSING"} else "NOT_PROMOTED",
                               "t1_status": status,
                               "net_return": value if settled else None,
                               "slot_return": value if settled else 0 if no_fill else None,
                               "entry_price": 10 if settled else None,
                               "exit_price": 10 * (1 + value + 0.0045) if settled else None,
                               "actual_exit_date": day["exit_date"] if settled else None,
                               "t_evidence": [], "t1_evidence": [], "truth_evidence": []})
        truth = result["rows"][-1]
        if status in {"SETTLED", "NO_FILL", "EXIT_BLOCKED"}:
            truth["t_evidence"] = [evidence(day["exec_date"], row["ts_code"])]
        elif status == "MISSING" and day["exec_date"] <= as_of:
            truth["t_evidence"] = [evidence(day["exec_date"], row["ts_code"], observed=False)]
        if settled or status == "EXIT_BLOCKED":
            truth["t1_evidence"] = [evidence(day["exit_date"], row["ts_code"])]
        truth["truth_evidence"] = truth["t_evidence"] + truth["t1_evidence"]
        truth["corporate_action_evidence"] = None
        truth["settlement_blocker"] = None
        if settled:
            assurance = corporate_evidence(day, row["ts_code"])
            assurance["evidence_sha256"] = hashlib.sha256(json.dumps(assurance, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            truth["corporate_action_evidence"] = assurance
    return result


def evidence(date, code, observed=True):
    return {"trade_date": date, "ts_code": code, "status": "OBSERVED" if observed else "MISSING_PARTITION",
            "partition_sha256": "a" * 64 if observed else None, "row_sha256": "b" * 64 if observed else None}


def corporate_evidence(day, code):
    return {"ts_code": code, "entry_date": day["exec_date"], "through_date": day["exit_date"],
            "basis": "NO_CORPORATE_ACTION_IN_WINDOW", "source_sha256": "c" * 64}


def test_inactive_and_replay_rejected_without_mutating_original():
    inactive = new_ledger("inactive")
    with pytest.raises(ValueError, match="inactive"):
        freeze_day(inactive, make_day(), open_dates=DATES, now_utc="2026-09-07T13:00:01Z")
    assert inactive["days"] == []
    day = make_day()
    day["generation_mode"] = "REPLAY"
    with pytest.raises(ValueError, match="REPLAY"):
        freeze_day(new_ledger("active", "2026-09-07T07:00:00Z"), day,
                   open_dates=DATES, now_utc="2026-09-07T13:00:01Z")


def test_freeze_atomic_two_rankings_immutable_idempotent_and_hash_binding():
    original = new_ledger("test", "2026-09-07T07:00:00Z")
    day = make_day()
    result = freeze_day(original, day, open_dates=DATES, now_utc="2026-09-07T13:01:00Z")
    assert original["days"] == []
    assert result["start_signal_date"] == "20260907"
    frozen = result["days"][0]
    assert frozen["freeze_sha256"] == freeze_day_sha256(day)
    assert freeze_day_sha256(frozen) == freeze_day_sha256(day)
    duplicate = freeze_day(result, day, open_dates=DATES, now_utc="2026-09-10T13:00:00Z")
    assert duplicate == result and duplicate is not result
    day["rows"][0]["name"] = "changed"
    assert frozen["rows"][0]["name"] != "changed"
    with pytest.raises(ValueError, match="cannot be replaced"):
        freeze_day(result, day, open_dates=DATES, now_utc="2026-09-07T14:00:00Z")


@pytest.mark.parametrize("n", [0, 1, 2, 10])
def test_real_member_count_not_padded(n):
    assert len(make_ledger(n)["days"][0]["rows"]) == n


@pytest.mark.parametrize("field,value", [
    ("promotion_rank", True), ("promotion_rank", 2), ("profit_rank", True),
    ("profit_rank", 4), ("promotion_probability", float("nan")),
    ("promotion_probability", 1.1), ("profit_score", float("inf")),
    ("path_change_pct", True), ("ts_code", "invalid"), ("name", ""),
])
def test_rank_numeric_and_member_errors_fail_closed(field, value):
    day = make_day()
    day["rows"][0][field] = value
    with pytest.raises(ValueError):
        freeze_day(new_ledger("test", "2026-09-07T07:00:00Z"), day,
                   open_dates=DATES, now_utc="2026-09-07T13:00:01Z")


def test_calendar_is_strict_and_weekend_not_added():
    friday = make_day(signal="20260904", generated="2026-09-04T13:00:00Z")
    ledger = freeze_day(new_ledger("friday", "2026-09-04T07:00:00Z"), friday,
                        open_dates=DATES, now_utc="2026-09-04T13:00:01Z")
    assert ledger["days"][0]["exec_date"] == "20260907"
    friday["exec_date"] = "20260905"
    with pytest.raises(ValueError, match="consecutive"):
        freeze_day(new_ledger("friday", "2026-09-04T07:00:00Z"), friday,
                   open_dates=DATES, now_utc="2026-09-04T13:00:01Z")
    with pytest.raises(ValueError, match="consecutive"):
        freeze_day(new_ledger("test", "2026-09-07T07:00:00Z"), make_day(),
                   open_dates=DATES[:3], now_utc="2026-09-07T13:00:01Z")


@pytest.mark.parametrize("generated,now", [
    ("2026-09-07T06:59:59Z", "2026-09-07T13:00:01Z"),
    ("2026-09-07T13:00:00Z", "2026-09-07T12:59:59Z"),
    ("2026-09-08T01:25:00Z", "2026-09-08T01:25:01Z"),
    ("2026-09-07T13:00:00Z", "2026-09-08T01:25:00Z"),
    ("2026-09-07T13:00:00", "2026-09-07T13:00:01Z"),
])
def test_prospective_generation_activation_and_admission_boundaries(generated, now):
    with pytest.raises(ValueError):
        freeze_day(new_ledger("test", "2026-09-07T07:00:00Z"), make_day(generated=generated),
                   open_dates=DATES, now_utc=now)


def test_source_timestamp_after_cutoff_and_blank_hash_rejected():
    for source in ({"model_sha256": ""},
                   {"model_sha256": "a" * 64, "nested": {"observed_at": "2026-09-08T01:25:00Z"}}):
        day = make_day()
        day["source"] = source
        with pytest.raises(ValueError):
            freeze_day(new_ledger("test", "2026-09-07T07:00:00Z"), day,
                       open_dates=DATES, now_utc="2026-09-07T13:00:01Z")


def test_cannot_backfill_or_import_prior_statistics():
    ledger = new_ledger("test", "2026-09-04T07:00:00Z", "20260907")
    with pytest.raises(ValueError, match="epoch start"):
        freeze_day(ledger, make_day(signal="20260904", generated="2026-09-04T13:00:00Z"),
                   open_dates=DATES, now_utc="2026-09-04T13:00:01Z")
    day = make_day()
    day["verifications"] = [{"legacy_stats": 123}]
    with pytest.raises(ValueError, match="previous verifications"):
        freeze_day(ledger, day, open_dates=DATES, now_utc="2026-09-07T13:00:01Z")


def test_validation_is_bound_exactly_and_mutated_frozen_payload_rejected():
    ledger = make_ledger()
    verification = make_verification(ledger["days"][0])
    for mutate in (lambda v: v.update(freeze_sha256="0" * 64),
                   lambda v: v.update(exec_date="20260910"),
                   lambda v: v["rows"].pop(),
                   lambda v: v["rows"][0].update(ts_code="600999.SH")):
        invalid = copy.deepcopy(verification)
        mutate(invalid)
        with pytest.raises(ValueError):
            record_verification(ledger, "20260907", invalid)
    ledger["days"][0]["rows"][0]["profit_score"] = 0.9
    with pytest.raises(ValueError, match="modified"):
        record_verification(ledger, "20260907", verification)


def test_missing_later_read_cannot_erase_final_truth_and_contradictions_rejected():
    ledger = make_ledger()
    verified = record_verification(ledger, "20260907", make_verification(ledger["days"][0]))
    assert ledger["days"][0]["verifications"] == []
    original_truth = verified["days"][0]["verifications"][-1]
    later = make_verification(ledger["days"][0], as_of="20260910", status="MISSING")
    preserved = record_verification(verified, "20260907", later)
    assert preserved["days"][0]["verifications"][-1]["rows"] == original_truth["rows"]
    assert preserved["days"][0]["verifications"][-1]["as_of_date"] == "20260910"
    assert record_verification(preserved, "20260907", later) == preserved
    contradictory = copy.deepcopy(original_truth)
    contradictory["as_of_date"] = "20260910"
    contradictory["rows"][0]["net_return"] = 0.2
    contradictory["rows"][0]["slot_return"] = 0.2
    with pytest.raises(ValueError, match="contradictory|declared costs"):
        record_verification(preserved, "20260907", contradictory)
    contradictory = copy.deepcopy(original_truth)
    contradictory["as_of_date"] = "20260910"
    contradictory["rows"][0]["t_status"] = "NOT_PROMOTED"
    with pytest.raises(ValueError, match="contradictory"):
        record_verification(preserved, "20260907", contradictory)


def test_as_of_date_never_regresses():
    ledger = make_ledger()
    verification = make_verification(ledger["days"][0])
    ledger = record_verification(ledger, "20260907", verification)
    older = make_verification(ledger["days"][0], as_of="20260908", status="MISSING")
    with pytest.raises(ValueError, match="backwards"):
        record_verification(ledger, "20260907", older)


def test_no_fill_and_unsettled_never_have_fake_net_return_zero():
    ledger = make_ledger()
    no_fill = make_verification(ledger["days"][0], status="NO_FILL")
    good = record_verification(ledger, "20260907", no_fill)
    assert good["days"][0]["verifications"][-1]["rows"][0]["net_return"] is None
    no_fill["rows"][0]["net_return"] = 0
    with pytest.raises(ValueError, match="NO_FILL"):
        record_verification(ledger, "20260907", no_fill)
    missing = make_verification(ledger["days"][0], status="MISSING")
    missing["rows"][0]["slot_return"] = 0
    with pytest.raises(ValueError, match="unresolved"):
        record_verification(ledger, "20260907", missing)


def test_future_t_or_exit_truth_rejected():
    ledger = make_ledger()
    verification = make_verification(ledger["days"][0], as_of="20260907")
    with pytest.raises(ValueError):
        record_verification(ledger, "20260907", verification)
    verification["as_of_date"] = "20260908"
    with pytest.raises(ValueError):
        record_verification(ledger, "20260907", verification)


def test_completed_t_evidence_survives_missing_and_truth_list_is_rebuilt():
    ledger = make_ledger(1)
    first = make_verification(ledger["days"][0], as_of="20260908", status="PENDING")
    first["rows"][0].update(t_status="PROMOTED", t_evidence=[evidence("20260908", "600001.SH")], t1_evidence=[])
    first["rows"][0]["truth_evidence"] = first["rows"][0]["t_evidence"][:]
    ledger = record_verification(ledger, "20260907", first)
    later = make_verification(ledger["days"][0], status="MISSING")
    later["rows"][0].update(t_evidence=[evidence("20260908", "600001.SH", observed=False)], t1_evidence=[])
    result = record_verification(ledger, "20260907", later)
    truth = result["days"][0]["verifications"][-1]["rows"][0]
    assert truth["t_status"] == "PROMOTED"
    assert truth["t_evidence"] == first["rows"][0]["t_evidence"]
    assert truth["truth_evidence"] == truth["t_evidence"] + truth["t1_evidence"]


def test_verification_methodology_cannot_change():
    ledger = make_ledger(1)
    first = make_verification(ledger["days"][0])
    first.update(costs_bps=45.0, price_basis="daily_open_proxy", research_only=True)
    ledger = record_verification(ledger, "20260907", first)
    changed = copy.deepcopy(first)
    changed["costs_bps"] = 0
    with pytest.raises(ValueError, match="methodology|declared costs"):
        record_verification(ledger, "20260907", changed)


def test_admission_timestamp_is_not_caller_supplied_or_late():
    ledger = make_ledger()
    assert ledger["days"][0]["admitted_at_utc"] == "2026-09-07T13:00:01Z"
    duplicate = freeze_day(ledger, ledger["days"][0], open_dates=DATES, now_utc="2026-09-10T13:00:00Z")
    assert duplicate == ledger
    verified = record_verification(ledger, "20260907", make_verification(ledger["days"][0]))
    assert freeze_day(verified, verified["days"][0], open_dates=DATES,
                      now_utc="2026-09-10T13:00:00Z") == verified
    day = make_day()
    day["admitted_at_utc"] = "2026-09-07T13:00:00Z"
    with pytest.raises(ValueError, match="caller"):
        freeze_day(new_ledger("new", "2026-09-07T07:00:00Z"), day, open_dates=DATES,
                   now_utc="2026-09-07T13:00:01Z")


@pytest.mark.parametrize("field,value", [("members_sha256", "0" * 64), ("costs_bps", -1),
                                          ("costs_bps", True), ("price_basis", "actual_trade"),
                                          ("research_only", False)])
def test_member_hash_costs_and_research_identity_required(field, value):
    ledger = make_ledger(1)
    verification = make_verification(ledger["days"][0])
    verification[field] = value
    with pytest.raises(ValueError):
        record_verification(ledger, "20260907", verification)


def test_no_fill_before_t1_is_not_settled():
    ledger = make_ledger(1)
    verification = make_verification(ledger["days"][0], as_of="20260908", status="NO_FILL")
    with pytest.raises(ValueError, match="before T1"):
        record_verification(ledger, "20260907", verification)


@pytest.mark.parametrize("mutation", ["missing", "hash", "member", "date", "source"])
def test_settled_requires_bound_corporate_action_review(mutation):
    ledger = make_ledger(1)
    verification = make_verification(ledger["days"][0])
    assurance = verification["rows"][0]["corporate_action_evidence"]
    if mutation == "missing":
        verification["rows"][0]["corporate_action_evidence"] = None
    elif mutation == "hash":
        assurance["evidence_sha256"] = "0" * 64
    elif mutation == "member":
        assurance["ts_code"] = "600002.SH"
    elif mutation == "date":
        assurance["through_date"] = "20260908"
    else:
        assurance["source_sha256"] = ""
    with pytest.raises(ValueError):
        record_verification(ledger, "20260907", verification)


def test_settlement_to_ledger_t_then_missing_corporate_then_resolved():
    from forward.settlement import verify_day

    ledger = make_ledger(1)
    day = ledger["days"][0]
    code = day["rows"][0]["ts_code"]
    market = {
        "20260908": [{"ts_code": code, "trade_date": "20260908", "open": 10,
                       "high": 11, "low": 10, "close": 11, "vol": 100,
                       "up_limit": 11, "down_limit": 9}],
        "20260909": [{"ts_code": code, "trade_date": "20260909", "open": 10.8,
                       "high": 11, "low": 10, "close": 10.5, "vol": 100,
                       "up_limit": 12.1, "down_limit": 9.9}],
    }
    first = verify_day(day, market, DATES, as_of_date="20260908")
    ledger = record_verification(ledger, day["signal_date"], first)
    assert ledger["days"][0]["verifications"][-1]["rows"][0]["t_status"] == "PROMOTED"
    assert ledger["days"][0]["verifications"][-1]["rows"][0]["t1_status"] == "PENDING"
    missing = verify_day(day, market, DATES, as_of_date="20260909")
    ledger = record_verification(ledger, day["signal_date"], missing)
    assert ledger["days"][0]["verifications"][-1]["rows"][0]["net_return"] is None
    final = verify_day(day, market, DATES, as_of_date="20260909",
                       corporate_action_evidence={code: corporate_evidence(day, code)})
    ledger = record_verification(ledger, day["signal_date"], final)
    truth = ledger["days"][0]["verifications"][-1]["rows"][0]
    assert truth["t1_status"] == "SETTLED"
    assert truth["net_return"] == pytest.approx(0.0755)
    missing_later = verify_day(day, {}, DATES, as_of_date="20260910")
    preserved = record_verification(ledger, day["signal_date"], missing_later)
    assert preserved["days"][0]["verifications"][-1]["rows"][0] == truth
