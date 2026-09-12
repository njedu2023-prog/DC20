"""Synthetic/offline overlay economics; never grants real collection authority."""
from __future__ import annotations

from copy import deepcopy
import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from work.profit_1000_upgrade import auction_candidate_scope as source
from work.profit_1000_upgrade import auction_truth_v3, candidate_labels_overlay as overlay, labels_v3, policy_v3
from work.profit_1000_upgrade.test_labels import CODE, D, T, T1, NEXT, case, daily, binding, write_csv
from work.profit_1000_upgrade.test_labels_v2 import research_minutes
from work.profit_1000_upgrade.test_labels_v3 import canonical


def candidate(root, *, code=CODE, day=T, price=10, vol=2_000_000, amount=20_000_000, pre=10, empty=False):
    data = {"fields": list(source.FIELDS), "items": [] if empty else [[code, day, price, vol, amount, pre]],
            "count": 0, "has_more": False}
    raw = json.dumps({"code": 0, "msg": "", "detail": "...", "data": data}).encode()
    bodies = source.source_bytes(raw, day, code, request=source.request_contract(day, code),
        fetched_at_utc="2026-09-15T08:00:00Z", network_request_performed=True)
    paths = source.source_paths(root, day, code)
    paths[0].parent.mkdir(parents=True, exist_ok=True)
    for path, body in zip(paths, bodies):
        path.write_bytes(body)
    return paths


@pytest.fixture
def overlay_case(case, tmp_path_factory):
    root, manifest = case
    manifest["rows"][0]["shadow_max_price"] = None
    manifest.update(policy_v3.CONTRACT)
    manifest["plan_version"] = "v3"
    research_minutes(root)
    source_root = tmp_path_factory.mktemp("candidate-source")
    candidate(source_root)
    return root, manifest, source_root


def authority_for(case, monkeypatch, *, successes=((T, CODE),), gaps=((T, CODE),), asof=T1):
    """Tests explicitly replace only permission gate; no synthetic verify success."""
    root, manifest, source_root = case
    base = [binding(root, p) for p in root.rglob("*") if p.is_file()]
    sources = [binding(source_root, p) for p in source_root.rglob("*") if p.is_file()]
    scope = SimpleNamespace(root=source_root, base_archive_sha256="a" * 64, receipt_sha256="b" * 64,
        frozen_manifest_sha256=overlay._digest(manifest), gap_pairs=tuple(gaps), successful_pairs=tuple(successes),
        source_bindings=tuple(sources), base_file_bindings=tuple(base), as_of_date=asof, checks=0)
    def unchanged():
        scope.checks += 1
        for b in base:
            labels_v3._binding(root, b)
        for b in sources:
            labels_v3._binding(source_root, b)
    scope.assert_unchanged = unchanged
    monkeypatch.setattr(overlay, "_authority", lambda value: value if value is scope else pytest.fail("wrong test scope"))
    return scope


def report(case, monkeypatch, *, asof=T1, **kwargs):
    scope = authority_for(case, monkeypatch, asof=asof, **kwargs)
    return overlay.build_labels(case[0], case[1], as_of_date=asof, candidate_source_root=case[2], verified_scope=scope)


def row(case, monkeypatch, **kwargs):
    return report(case, monkeypatch, **kwargs)["rows"][0]


def test_missing_verifier_or_non_authority_cannot_authorize():
    # A missing verifier closes the gate too. Once installed, wrong types fail.
    for value in (True, {}, {"accepted": True}, SimpleNamespace()):
        with pytest.raises((ImportError, ValueError), match="candidate_scope_verify|VERIFIED_CANDIDATE"):
            overlay._authority(value)


def test_actual_new_source_identity_and_same_negative_economics(overlay_case, monkeypatch):
    root, manifest, source_root = overlay_case
    original = labels_v3.build_labels(root, manifest, as_of_date=T1)
    assert original["rows"][0]["label_status"] == "PENDING_T_MISSING_CANONICAL_AUCTION"
    out = report(overlay_case, monkeypatch)
    item = out["rows"][0]
    assert item["label_status"] == labels_v3.SETTLED
    assert item["net_return"] == pytest.approx(-.0245)
    assert item["entry_policy_id"] == policy_v3.ENTRY_POLICY_ID
    assert item["auction_source_policy_id"] == source.SOURCE_POLICY_ID
    assert item["entry_price_evidence"]["source_policy_id"] == source.SOURCE_POLICY_ID
    assert item["entry_price_evidence"]["qualification_policy_id"] == auction_truth_v3.SOURCE_POLICY_ID
    assert item["source_overlay_policy_id"] == overlay.OVERLAY_POLICY_ID
    assert overlay.validate_label_contract(item) == overlay.CONTRACT
    with pytest.raises(ValueError):
        policy_v3.validate_label_contract(item)
    assert out["overlay_consumed_pairs"] == [[T, CODE]] and not out["unresolved_source_pairs"]
    assert out["cohorts_by_date"][D]["complete"] and item["cohort_complete"]
    assert out["source_overlay_contract"] == overlay.CONTRACT
    assert out["source_only_metadata_rewritten"] is out["training_performed"] is out["production_activation_allowed"] is False
    assert out["files_written"] == 0
    assert {b["origin"] for b in out["source_files"]} == {"base", "candidate"}
    meta = json.loads(source.source_paths(source_root, T, CODE)[1].read_bytes())
    assert meta["source_only"] and meta["label_source_eligible"] is False
    canonical(root)
    old = labels_v3.build_labels(root, manifest, as_of_date=T1)["rows"][0]
    for key in ("signal_date", "ts_code", "features", "promotion_rank", "net_return", "conditional_net_return",
                "slot_net_return", "actual_exit_date", "actual_exit_time", "decision_time", "label_available_at",
                "held_limit_up_sessions", "entry_price", "capacity_proxy_verified", "capacity_amount"):
        assert item[key] == old[key]


@pytest.mark.parametrize("amount", [None, True, -1, "NaN", 0, 20_000_001, 19_999_999])
def test_unknown_capacity_does_not_zero_a_negative_price_proxy(overlay_case, monkeypatch, amount):
    candidate(overlay_case[2], amount=amount)
    item = row(overlay_case, monkeypatch)
    assert item["label_status"] == labels_v3.SETTLED and item["net_return"] == pytest.approx(-.0245)
    assert item["capacity_proxy_verified"] is False and item["capacity_amount"] is None
    assert item["proxy_fill"] == 1 and item["reported_auction_amount"] == amount


@pytest.mark.parametrize("changes,status", [
    ({"price": None}, "PENDING_ENTRY_INVALID_PRICE"), ({"price": 0}, "PENDING_ENTRY_INVALID_PRICE"),
    ({"price": 10.01}, "PENDING_ENTRY_PRICE_DAILY_OPEN_CONFLICT"),
    ({"vol": 100.5}, "PENDING_ENTRY_INVALID_SHARE_VOLUME"), ({"vol": True}, "PENDING_ENTRY_INVALID_SHARE_VOLUME"),
    ({"pre": 0}, "PENDING_ENTRY_INVALID_PRE_CLOSE"),
    ({"vol": 0, "amount": 0, "price": 10}, "PENDING_ENTRY_ZERO_VOLUME_DATA_CONFLICT"),
])
def test_bad_price_or_volume_stays_pending_without_fallback(overlay_case, monkeypatch, changes, status):
    candidate(overlay_case[2], **changes)
    item = row(overlay_case, monkeypatch)
    assert item["label_status"] == status and item["entry_price_fallback_reason"] is None
    assert item["proxy_fill"] is item["net_return"] is item["slot_net_return"] is None
    assert item["minute_source_observed"] is False


@pytest.mark.parametrize("kind,status", [
    ("small", "NO_FILL_CAPACITY"), ("zero", "NO_FILL_CANONICAL_AUCTION_ZERO_VOLUME"),
    ("up", "NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED"), ("suspended", "NO_FILL_SUSPENDED")])
def test_unchanged_four_no_fill_rules(overlay_case, monkeypatch, kind, status):
    root, _, source_root = overlay_case
    if kind == "small":
        candidate(source_root, vol=100_000, amount=1_000_000)
    elif kind == "zero":
        candidate(source_root, price=None, vol=0, amount=0)
    elif kind == "up":
        daily(root, T, price=11)
        candidate(source_root, price=11, amount=22_000_000)
    else:
        daily(root, T, volume=0)
        candidate(source_root, empty=True)
    item = row(overlay_case, monkeypatch)
    assert item["label_status"] == status and item["proxy_fill"] == 0 and item["slot_net_return"] == 0
    assert item["net_return"] is item["conditional_net_return"] is None and item["cohort_complete"]


def test_exact_complete_empty_may_fallback_not_claim_auction_capacity(overlay_case, monkeypatch):
    candidate(overlay_case[2], empty=True)
    item = row(overlay_case, monkeypatch)
    assert item["label_status"] == labels_v3.SETTLED and item["net_return"] == pytest.approx(-.0245)
    assert item["entry_price_source"] == "DAILY_OPEN_PROXY" and item["auction_request_receipt_observed"] is True
    assert item["auction_trade_observed"] is None and item["price_qualified"] is False
    assert item["capacity_proxy_verified"] is False and item["capacity_amount"] is None
    assert item["entry_price_fallback_reason"] == "CANONICAL_ROW_ABSENT_AFTER_VALID_REQUEST"


def test_unattempted_or_rejected_source_cannot_fallback(overlay_case, monkeypatch):
    out = report(overlay_case, monkeypatch, successes=())
    item = out["rows"][0]
    assert item["label_status"] == "PENDING_T_MISSING_CANONICAL_AUCTION"
    assert item["entry_price"] is item["slot_net_return"] is None
    assert out["unresolved_source_pairs"] == [[T, CODE]] and out["overlay_consumed_pairs"] == []
    assert item["auction_source_policy_id"] == policy_v3.AUCTION_SOURCE_POLICY_ID


def test_missing_minutes_stay_pending(overlay_case, monkeypatch):
    for path in minute_truth_paths(overlay_case[0], T1):
        path.unlink()
    item = row(overlay_case, monkeypatch)
    assert item["label_status"] == "PENDING_EXIT_MISSING_MINUTES" and item["proxy_fill"] == 1
    assert item["net_return"] is item["slot_net_return"] is None
    assert item["missing_evidence_date"] == T1 and item["missing_evidence_kind"] == "research_exit_1000_1m_0931"


def minute_truth_paths(root, day):
    return overlay.minute_truth.paths(root, day, CODE)


def test_hold_to_later_day_and_real_maturity(overlay_case, monkeypatch):
    root = overlay_case[0]
    daily(root, T1, price=11)
    research_minutes(root, price=11)
    item = row(overlay_case, monkeypatch)
    assert item["label_status"] == "PENDING_EXIT_LIMIT_UP_HELD" and item["slot_net_return"] is None
    daily(root, NEXT, price=11.5, pre=11, up=12.1, down=9.9)
    research_minutes(root, NEXT, price=11.5)
    item = row(overlay_case, monkeypatch, asof=NEXT)
    assert item["label_status"] == labels_v3.SETTLED and item["actual_exit_date"] == NEXT
    assert item["held_limit_up_sessions"] == 1 and item["net_return"] == pytest.approx(.1455)


def test_noncent_reported_price_not_replaced(overlay_case, monkeypatch):
    candidate(overlay_case[2], price="9.99999", amount=None)
    item = row(overlay_case, monkeypatch)
    assert item["entry_price"] == "9.99999"
    assert item["net_return"] == pytest.approx(9.8 / 9.99999 - 1 - .0045)


def test_existing_full_market_row_cannot_be_replaced(overlay_case, monkeypatch):
    canonical(overlay_case[0])
    with pytest.raises(ValueError, match="OVERLAY_CANNOT_REPLACE"):
        report(overlay_case, monkeypatch)


def test_unscoped_base_rows_remain_byte_equivalent(overlay_case, monkeypatch):
    canonical(overlay_case[0])
    expected = labels_v3.build_labels(overlay_case[0], overlay_case[1], as_of_date=T1)
    out = report(overlay_case, monkeypatch, successes=(), gaps=())
    assert out["rows"] == expected["rows"] and out["cohorts_by_date"] == expected["cohorts_by_date"]
    assert all(b["origin"] == "base" for b in out["source_files"])


@pytest.mark.parametrize("corrupt", ["manifest", "root", "scope", "binding", "extra_base", "source_changed"])
def test_authority_binding_failures_close_before_success(overlay_case, monkeypatch, corrupt):
    root, manifest, source_root = overlay_case
    scope = authority_for(overlay_case, monkeypatch)
    if corrupt == "manifest":
        scope.frozen_manifest_sha256 = "0" * 64
    elif corrupt == "root":
        scope.root = root
    elif corrupt == "scope":
        scope.gap_pairs = ((T, "600001.SH"),)
    elif corrupt == "binding":
        scope.source_bindings = ()
    elif corrupt == "extra_base":
        (root / "unregistered.json").write_text("{}")
    else:
        source.source_paths(source_root, T, CODE)[0].write_text("{}")
    with pytest.raises(ValueError):
        overlay.build_labels(root, manifest, as_of_date=T1, candidate_source_root=source_root, verified_scope=scope)


def test_loaded_source_type_not_legacy_or_dict(overlay_case):
    daily_binding = binding(overlay_case[0], overlay_case[0] / f"data/market/raw/2026/{T}/daily.csv")
    for loaded in (None, {}, True, SimpleNamespace(trade_date=T, ts_code=CODE)):
        with pytest.raises(ValueError, match="EXACT_LOADED"):
            overlay.qualify_candidate(loaded, T, CODE, 10, daily_source_binding=daily_binding)


@pytest.mark.parametrize("key,value", [
    ("auction_source_policy_id", policy_v3.AUCTION_SOURCE_POLICY_ID), ("entry_policy_id", "v2"),
    ("round_trip_cost_rate", 0), ("actual_execution_claimed", True), ("actual_capacity_verified", True),
    ("known_before_0925", True), ("source_overlay_policy_id", "unknown"), ("net_return", 0),
    ("minute_source_observed", False), ("label_available_at", "2026-09-14T10:00:00+08:00"),
    ("auction_trade_observed", 1), ("unregistered_source_policy_id", "unknown"),
    ("source_policy_contract", dict(policy_v3.CONTRACT)), ("plan_version", "v2"),
])
def test_new_contract_rejects_identity_or_economic_mutation(overlay_case, monkeypatch, key, value):
    item = row(overlay_case, monkeypatch)
    item[key] = value
    with pytest.raises((ValueError, TypeError)):
        overlay.validate_label_contract(item)


def test_zero_capacity_cannot_replace_pending(overlay_case, monkeypatch):
    candidate(overlay_case[2], price=None)
    item = row(overlay_case, monkeypatch)
    item.update(label_status="NO_FILL_CAPACITY", proxy_fill=0, slot_net_return=0.)
    with pytest.raises(ValueError):
        overlay.validate_label_contract(item)


def test_precise_source_and_http_metadata_never_modified(overlay_case, monkeypatch):
    root, manifest, source_root = overlay_case
    all_paths = [p for path in (root, source_root) for p in path.rglob("*") if p.is_file()]
    before = {str(p): p.read_bytes() for p in all_paths}
    report(overlay_case, monkeypatch)
    assert {str(p): p.read_bytes() for p in all_paths} == before


def test_wrong_asof_is_not_an_implicit_new_research_window(overlay_case, monkeypatch):
    scope = authority_for(overlay_case, monkeypatch)
    with pytest.raises(ValueError, match="VERIFIED_AS_OF"):
        overlay.build_labels(overlay_case[0], overlay_case[1], as_of_date=NEXT,
                             candidate_source_root=overlay_case[2], verified_scope=scope)


def test_same_day_all_candidates_retained_when_one_source_missing(overlay_case, monkeypatch):
    root, manifest, _ = overlay_case
    other = "600001.SH"
    addition = deepcopy(manifest["rows"][0])
    addition.update(ts_code=other, promotion_rank=2)
    manifest["rows"].append(addition)
    manifest["expected_candidate_codes"][D].append(other)
    for name in ("daily", "stk_limit"):
        path = root / f"data/market/raw/2026/{T}/{name}.csv"
        rows = list(csv.DictReader(path.open()))
        rows.append({**rows[0], "ts_code": other})
        write_csv(path, rows)
    out = report(overlay_case, monkeypatch, gaps=((T, CODE), (T, other)))
    assert [(r["ts_code"], r["promotion_rank"]) for r in out["rows"]] == [(CODE, 1), (other, 2)]
    assert [r["label_status"] for r in out["rows"]] == [labels_v3.SETTLED, "PENDING_T_MISSING_CANONICAL_AUCTION"]
    assert out["rows"][0]["net_return"] < 0 and out["rows"][1]["slot_net_return"] is None
    assert all(not r["cohort_complete"] for r in out["rows"])
    assert out["cohorts_by_date"][D]["expected_rows"] == 2 and out["cohorts_by_date"][D]["terminal_rows"] == 1


@pytest.mark.parametrize("change", [{"high": 10.01}, {"low": 9.99}, {"high": 10.01, "close": 10.01}])
def test_base_zero_volume_nonflat_is_never_overwritten_with_zero(overlay_case, monkeypatch, change):
    root, _, source_root = overlay_case
    daily(root, T, volume=0)
    path = root / f"data/market/raw/2026/{T}/daily.csv"
    values = list(csv.DictReader(path.open()))
    values[0].update(change)
    write_csv(path, values)
    candidate(source_root, empty=True)
    # Base-invalid rows are not entry-missing seeds and cannot be promoted by
    # an auction response. Exact real scope did not contain such seeds.
    with pytest.raises(ValueError, match="OVERLAY_CANNOT_REPLACE"):
        report(overlay_case, monkeypatch)


def test_entry_zero_daily_conflicts_with_positive_auction(overlay_case, monkeypatch):
    daily(overlay_case[0], T, volume=0)
    item = row(overlay_case, monkeypatch)
    assert item["label_status"] == "PENDING_ENTRY_SOURCE_CONFLICT" and item["slot_net_return"] is None


def test_missing_exit_daily_is_not_assumed_suspension(overlay_case, monkeypatch):
    path = overlay_case[0] / f"data/market/raw/2026/{T1}/daily.csv"
    path.unlink()
    item = row(overlay_case, monkeypatch)
    assert item["label_status"] == "PENDING_EXIT_MISSING_DAILY"
    assert item["slot_net_return"] is None and item["proxy_fill"] == 1
    assert item["missing_evidence_kind"] == "daily" and item["missing_evidence_date"] == T1


def test_mid_replay_changed_source_fails_final_binding(overlay_case, monkeypatch):
    scope = authority_for(overlay_case, monkeypatch)
    original = overlay._resume
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        source.source_paths(overlay_case[2], T, CODE)[0].write_text("{}")
        return result
    monkeypatch.setattr(overlay, "_resume", changed)
    with pytest.raises(ValueError, match="SHA mismatch"):
        overlay.build_labels(overlay_case[0], overlay_case[1], as_of_date=T1,
                             candidate_source_root=overlay_case[2], verified_scope=scope)


def test_public_replay_rechecks_authority_after_work(overlay_case, monkeypatch):
    scope = authority_for(overlay_case, monkeypatch)
    checked = []
    original = scope.assert_unchanged
    def changed():
        checked.append(True)
        original()
        raise ValueError("RECEIPT_CHANGED")
    scope.assert_unchanged = changed
    with pytest.raises(ValueError, match="RECEIPT_CHANGED"):
        overlay.build_labels(overlay_case[0], overlay_case[1], as_of_date=T1,
                             candidate_source_root=overlay_case[2], verified_scope=scope)
    assert checked


@pytest.mark.parametrize("field,value", [("source_policy_id", policy_v3.AUCTION_SOURCE_POLICY_ID),
                                      ("source_origin", "base"), ("loaded_source_qualification_claimed", False),
                                      ("source_only_metadata_rewritten", True), ("ts_code", "600001.SH"),
                                      ("trade_date", NEXT), ("capacity_amount", 1)])
def test_new_entry_evidence_identity_cannot_be_legacy_or_mismatched(overlay_case, monkeypatch, field, value):
    item = row(overlay_case, monkeypatch)
    item["entry_price_evidence"][field] = value
    with pytest.raises(ValueError):
        overlay.validate_label_contract(item)


@pytest.mark.parametrize("field,value", [("actual_execution_claimed", True), ("known_before_0925", True),
    ("actual_capacity_verified", True), ("production_activation_allowed", True),
    ("price_reporting_precision_confirmed", True), ("reported_price_preserved", False),
    ("basis", "PRE0925"), ("research_only", False)])
def test_entry_evidence_cannot_claim_more_than_posthoc_research(overlay_case, monkeypatch, field, value):
    item = row(overlay_case, monkeypatch)
    item["entry_price_evidence"][field] = value
    with pytest.raises(ValueError):
        overlay.validate_label_contract(item)
