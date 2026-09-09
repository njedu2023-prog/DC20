from __future__ import annotations

import copy
import hashlib
import json

import pytest

from forward.settlement import verify_day


DATES = ["20260904", "20260907", "20260908", "20260909", "20260910", "20260911"]
D, T, T1 = DATES[:3]
CODE = "600001.SH"


def frozen(codes=(CODE,)):
    return {"signal_date": D, "exec_date": T, "exit_date": T1,
            "freeze_sha256": "a" * 64,
            "rows": [{"ts_code": code, "promotion_rank": i + 1} for i, code in enumerate(codes)]}


def quote(date, code=CODE, **changes):
    row = dict(ts_code=code, trade_date=date, open=10.0, high=11.0, low=9.0,
               close=10.5, vol=100.0, up_limit=11.0, down_limit=9.0)
    row.update(changes)
    return row


def assurance(through=T1, code=CODE, **changes):
    # Synthetic test evidence only; production callers must bind a real source.
    evidence = dict(ts_code=code, entry_date=T, through_date=through,
                    basis="NO_CORPORATE_ACTION_IN_WINDOW", source_sha256="b" * 64)
    evidence.update(changes)
    return evidence


def verify(market, as_of=T1, day=None, **kwargs):
    day = day or frozen()
    if "corporate_action_evidence" not in kwargs:
        kwargs["corporate_action_evidence"] = {
            row["ts_code"]: assurance(as_of, row["ts_code"]) for row in day["rows"]}
    return verify_day(day, market, DATES, as_of_date=as_of, **kwargs)


class FutureForbidden(dict):
    def get(self, date, *args):
        if date > D:
            raise AssertionError("future price was read")
        return super().get(date, *args)


def test_pending_before_t_never_reads_future_market_data():
    result = verify(FutureForbidden({T: object()}), as_of=D)
    assert result["freeze_sha256"] == "a" * 64
    assert result["rows"][0]["t_status"] == "PENDING"
    assert result["rows"][0]["t1_status"] == "PENDING"
    assert result["rows"][0]["truth_evidence"] == []
    assert result["rows"][0]["slot_return"] is None


def test_t_promotion_is_available_without_t1_and_t1_is_not_read():
    result = verify({T: [quote(T, close=11)], T1: object()}, as_of=T)["rows"][0]
    assert result["t_status"] == "PROMOTED"
    assert result["t1_status"] == "PENDING"
    assert result["entry_price"] == 10
    assert result["net_return"] is result["slot_return"] is None
    assert [item["trade_date"] for item in result["truth_evidence"]] == [T]


def test_cost_adjusted_return_exact_identity_and_readonly_inputs():
    market = {T: [quote(T, close=11)], T1: [quote(T1, open=10.8)]}
    day = frozen()
    before = copy.deepcopy((market, day, DATES))
    event = verify(market, day=day)
    row = event["rows"][0]
    assert row["t_status"] == "PROMOTED"
    assert row["t1_status"] == "SETTLED"
    assert row["net_return"] == pytest.approx(10.8 / 10 - 1 - 0.0045)
    assert row["slot_return"] == row["net_return"]
    assert row["entry_price"] == 10 and row["exit_price"] == 10.8
    assert row["actual_exit_date"] == T1
    assert event["price_basis"] == row["price_basis"] == "daily_open_proxy"
    assert event["freeze_sha256"] == day["freeze_sha256"]
    assert row["truth_evidence"] == row["t_evidence"] + row["t1_evidence"]
    assert len(row["t_evidence"]) == len(row["t1_evidence"]) == 1
    assert row["corporate_action_evidence"]["basis"] == "NO_CORPORATE_ACTION_IN_WINDOW"
    assert len(row["corporate_action_evidence"]["evidence_sha256"]) == 64
    assert before == (market, day, DATES)
    for evidence in row["truth_evidence"]:
        raw = json.dumps(market[evidence["trade_date"]], ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        assert evidence["partition_sha256"] == hashlib.sha256(raw).hexdigest()
        assert len(evidence["row_sha256"]) == 64


@pytest.mark.parametrize("market", [{}, {T: []}, {T: [quote(T, code="600002.SH")]}])
def test_absent_t_date_or_code_is_missing_not_failed_prediction_or_zero(market):
    row = verify(market)["rows"][0]
    assert row["t_status"] == row["t1_status"] == "MISSING"
    assert row["net_return"] is row["slot_return"] is None
    assert row["entry_price"] is None


def test_missing_t_with_immature_t1_keeps_t1_pending():
    row = verify({}, as_of=T)["rows"][0]
    assert row["t_status"] == "MISSING"
    assert row["t1_status"] == "PENDING"


@pytest.mark.parametrize("changes,t_status", [({"open": 11, "close": 11}, "PROMOTED"),
                                               ({"vol": 0}, "NOT_PROMOTED")])
def test_limit_up_open_or_suspension_is_no_fill_only_after_t1(changes, t_status):
    market = {T: [quote(T, **changes)]}
    early = verify(market, as_of=T)["rows"][0]
    assert early["t_status"] == t_status
    assert early["t1_status"] == "PENDING" and early["slot_return"] is None
    mature = verify(market)["rows"][0]
    assert mature["t1_status"] == "NO_FILL"
    assert mature["net_return"] is None and mature["slot_return"] == 0.0
    assert mature["entry_price"] is mature["exit_price"] is None
    assert [e["trade_date"] for e in mature["truth_evidence"]] == [T]


def test_intraday_limit_up_but_close_below_limit_is_not_promoted():
    assert verify({T: [quote(T)]}, as_of=T)["rows"][0]["t_status"] == "NOT_PROMOTED"


def test_locked_down_and_suspension_wait_then_use_first_tradable_open():
    market = {
        T: [quote(T)], T1: [quote(T1, open=9, close=9, high=9)],
        DATES[3]: [quote(DATES[3], vol=0)],
        DATES[4]: [quote(DATES[4], open=10.7)],
        DATES[5]: object(),
    }
    blocked = verify(market, as_of=DATES[3])["rows"][0]
    assert blocked["t1_status"] == "EXIT_BLOCKED"
    assert blocked["net_return"] is blocked["slot_return"] is None
    assert blocked["blocked_exit_sessions"] == 2
    final = verify(market, as_of=DATES[5])["rows"][0]
    assert final["t1_status"] == "SETTLED"
    assert final["actual_exit_date"] == DATES[4]
    assert final["net_return"] == pytest.approx(10.7 / 10 - 1 - .0045)
    assert [e["trade_date"] for e in final["truth_evidence"]] == [T, T1, DATES[3], DATES[4]]


@pytest.mark.parametrize("missing", [None, []])
def test_missing_intervening_session_never_skips_to_later_profitable_exit(missing):
    market = {T: [quote(T)], T1: [quote(T1, open=9)], DATES[4]: [quote(DATES[4], open=11)]}
    if missing is not None:
        market[DATES[3]] = missing
    row = verify(market, as_of=DATES[4])["rows"][0]
    assert row["t1_status"] == "MISSING"
    assert row["net_return"] is row["slot_return"] is None
    assert row["actual_exit_date"] is None
    assert row["truth_evidence"][-1]["trade_date"] == DATES[3]


def test_n_zero_emits_empty_exact_member_event_without_reading_prices():
    result = verify(FutureForbidden({T: object()}), day=frozen(()))
    assert result["rows"] == []
    assert result["freeze_sha256"] == "a" * 64


def test_every_frozen_member_remains_in_order_even_if_some_truth_missing():
    codes = ("600003.SH", "000002.SZ", "600001.SH")
    result = verify({T: [quote(T, code=codes[1])], T1: [quote(T1, code=codes[1])]}, day=frozen(codes))
    assert [r["ts_code"] for r in result["rows"]] == list(codes)
    assert [r["t1_status"] for r in result["rows"]] == ["MISSING", "SETTLED", "MISSING"]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), 0, -1, None, "", True])
def test_invalid_prices_fail_closed_instead_of_counting_a_loss(bad):
    with pytest.raises(ValueError):
        verify({T: [quote(T, close=bad)]})


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, None, True])
def test_invalid_volume_is_not_no_fill_zero(bad):
    with pytest.raises(ValueError):
        verify({T: [quote(T, vol=bad)]})


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, True, None])
def test_cost_must_be_finite_nonnegative(bad):
    with pytest.raises(ValueError):
        verify({}, as_of=D, costs_bps=bad)


def test_stress_cost_is_explicit_and_not_hardcoded_to_45bps():
    row = verify({T: [quote(T)], T1: [quote(T1)]}, costs_bps=90)["rows"][0]
    assert row["net_return"] == pytest.approx(-.009)


@pytest.mark.parametrize("changes", [{"open": 12}, {"close": 8}, {"low": 10.6},
                                      {"high": 9.5}, {"down_limit": 11}, {"up_limit": 9}])
def test_impossible_bar_or_price_limit_is_rejected(changes):
    with pytest.raises(ValueError, match="OHLC"):
        verify({T: [quote(T, **changes)]})


def test_extreme_finite_prices_cannot_emit_infinite_return():
    entry = quote(T, open=1e-300, high=2e-300, low=1e-300, close=1e-300,
                  up_limit=2e-300, down_limit=1e-301)
    exit_row = quote(T1, open=1e300, high=1e300, low=1e300, close=1e300,
                     up_limit=2e300, down_limit=1e299)
    with pytest.raises(ValueError, match="nonfinite return"):
        verify({T: [entry], T1: [exit_row]})


@pytest.mark.parametrize("market", [{T: [quote(T), quote(T)]},
                                     {T: [quote(T), quote(T1, code="000002.SZ")]},
                                     {T: [quote(T, ts_code="600001")]},
                                     {T: [{"ts_code": CODE, "trade_date": T}]}, {T: {}}])
def test_duplicate_mixed_date_invalid_code_and_missing_schema_are_rejected(market):
    with pytest.raises(ValueError):
        verify(market)


@pytest.mark.parametrize("field,value", [("exec_date", "20260905"), ("exit_date", "20260909"),
                                         ("signal_date", "20260931"), ("signal_date", "2026-09-04"),
                                         ("freeze_sha256", ""), ("freeze_sha256", "z" * 64)])
def test_wrong_frozen_dates_or_identity_are_rejected(field, value):
    day = frozen()
    day[field] = value
    with pytest.raises(ValueError):
        verify({}, day=day)


@pytest.mark.parametrize("as_of", ["20260905", "2026-09-08", "20260903"])
def test_asof_must_be_closed_strict_session_not_weekend_or_before_d(as_of):
    with pytest.raises(ValueError):
        verify({}, as_of=as_of)


@pytest.mark.parametrize("dates", [[], DATES + [DATES[-1]], list(reversed(DATES)), "20260904"])
def test_calendar_cannot_be_inferred_sorted_or_deduplicated(dates):
    with pytest.raises(ValueError):
        verify_day(frozen(), {}, dates, as_of_date=D)


def test_duplicate_frozen_members_and_more_than_ten_are_rejected():
    with pytest.raises(ValueError):
        verify({}, day=frozen((CODE, CODE)))
    with pytest.raises(ValueError):
        verify({}, day=frozen(tuple(f"{600001+i}.SH" for i in range(11))))


@pytest.mark.parametrize("evidence", [None, {}, {CODE: assurance(T)}])
def test_unassured_corporate_actions_never_become_settled_return(evidence):
    row = verify({T: [quote(T, close=11)], T1: [quote(T1, open=10.8)]},
                 corporate_action_evidence=evidence)["rows"][0]
    assert row["t_status"] == "PROMOTED"
    assert row["t1_status"] == "MISSING"
    assert row["settlement_blocker"] == "CORPORATE_ACTION_REVIEW_REQUIRED"
    assert row["net_return"] is row["slot_return"] is None
    assert row["exit_price"] is row["actual_exit_date"] is None
    assert row["truth_evidence"] == row["t_evidence"] + row["t1_evidence"]


def test_default_api_has_no_implicit_corporate_action_assurance():
    row = verify_day(frozen(), {T: [quote(T)], T1: [quote(T1)]}, DATES,
                     as_of_date=T1)["rows"][0]
    assert row["t1_status"] == "MISSING"
    assert row["net_return"] is None


def test_no_fill_does_not_require_or_read_corporate_action_evidence():
    row = verify({T: [quote(T, open=11)]}, corporate_action_evidence={CODE: object()})["rows"][0]
    assert row["t1_status"] == "NO_FILL" and row["slot_return"] == 0
    assert row["corporate_action_evidence"] is None
    assert row["settlement_blocker"] is None


def test_delayed_exit_requires_evidence_covering_the_actual_exit_session():
    market = {T: [quote(T)], T1: [quote(T1, open=9)], DATES[3]: [quote(DATES[3])]}
    evidence = {CODE: assurance(T1)}
    row = verify(market, as_of=DATES[3], corporate_action_evidence=evidence)["rows"][0]
    assert row["t1_status"] == "MISSING"
    assert row["settlement_blocker"] == "CORPORATE_ACTION_REVIEW_REQUIRED"
    evidence[CODE]["through_date"] = DATES[3]
    before = copy.deepcopy(evidence)
    row = verify(market, as_of=DATES[3], corporate_action_evidence=evidence)["rows"][0]
    assert row["t1_status"] == "SETTLED" and row["actual_exit_date"] == DATES[3]
    assert before == evidence


@pytest.mark.parametrize("changes", [{"ts_code": "600002.SH"}, {"entry_date": D},
                                      {"through_date": "20260906"}, {"through_date": DATES[-1]},
                                      {"basis": "COMPARABLE_TOTAL_RETURN_ADJUSTED"},
                                      {"source_sha256": "z" * 64}, {"source_sha256": True},
                                      {"extra": "unreviewed"}])
def test_misbound_or_unsupported_corporate_action_evidence_is_rejected(changes):
    with pytest.raises(ValueError):
        verify({T: [quote(T)], T1: [quote(T1)]},
               corporate_action_evidence={CODE: assurance(**changes)})


@pytest.mark.parametrize("bad", [True, [], {"600002.SH": assurance(code="600002.SH")}])
def test_corporate_action_mapping_cannot_introduce_nonmembers(bad):
    with pytest.raises(ValueError):
        verify({}, corporate_action_evidence=bad)
