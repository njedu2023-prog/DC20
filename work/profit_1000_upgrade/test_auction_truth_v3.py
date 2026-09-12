"""Synthetic v3 isolation tests. No provider calls or diagnostic source import."""
from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
import json

import pytest

from work.profit_1000_upgrade import auction_truth as old
from work.profit_1000_upgrade import auction_truth_v3 as auction

DAY, CODE, OTHER = "20260817", "600001.SH", "600002.SH"
FETCHED = "2026-09-12T10:00:00Z"
DAILY = {"path": "data/market/raw/2026/20260817/daily.csv", "sha256": "a" * 64}


def row(*, code=CODE, day=DAY, price=10, vol=100, amount=1000, pre_close=9):
    return {"ts_code": code, "trade_date": day, "price": price, "vol": vol, "amount": amount, "pre_close": pre_close}


def table(rows=None):
    return {"fields": list(auction.FIELDS), "items": [[value[field] for field in auction.FIELDS]
                for value in ([row()] if rows is None else rows)], "count": 0, "has_more": False}


def envelope(data=None, *, code=0, msg=""):
    return json.dumps({"code": code, "msg": msg, "data": table() if data is None and code == 0 else data}).encode()


def pair(raw=None, *, day=DAY, code=None, **kwargs):
    return auction.source_bytes(envelope() if raw is None else raw, day,
            request=auction.request_contract(day, code), fetched_at_utc=FETCHED,
            network_request_performed=True, **kwargs)


def save(root, bodies=None, *, day=DAY):
    paths = auction.source_paths(root, day)
    paths[0].parent.mkdir(parents=True, exist_ok=True)
    for path, body in zip(paths, pair(day=day) if bodies is None else bodies):
        path.write_bytes(body)
    return paths


def entry(source, *, day=DAY, code=CODE, opening=10):
    return auction.entry_price(source, day, code, opening, daily_source_binding=DAILY)


def qualify(value=None, *, opening=10):
    return auction.qualify_row(row() if value is None else value, DAY, CODE, opening)


def test_good_price_and_amount_are_posthoc_proxy_not_capacity_or_signal():
    result = qualify()
    assert result["status"] == "CANONICAL_PRICE_OBSERVED"
    assert result["price"] == 10 and result["price_qualified"] is True
    assert result["capacity_proxy_verified"] is True and result["amount"] == result["capacity_amount"] == 1000
    assert result["amount_identity_delta"] == "0"
    assert result["basis"] == "POSTHOC_ENTRY_PRICE_CHECK_NOT_PRE0925_FEATURE"
    for key in ("actual_execution_claimed", "actual_capacity_verified", "known_before_0925",
                "production_activation_allowed", "price_reporting_precision_confirmed", "source_import_allowed",
                "loaded_source_qualification_claimed"):
        assert result[key] is False


@pytest.mark.parametrize("bad", [dict(price=None), dict(price=0), dict(price=-1), dict(price="NaN"),
                                 dict(vol=None), dict(vol=10.2), dict(vol=True), dict(pre_close=0),
                                 dict(pre_close=None), dict(amount=None), dict(amount=True), dict(amount=-1),
                                 dict(amount=1001), dict(amount="NaN")])
def test_non_candidate_bad_numeric_does_not_poison_good_candidate(tmp_path, bad):
    data = table([row(), row(code=OTHER, **bad)])
    raw = envelope(data)
    with pytest.raises(old.AuctionSourceError):
        # pre_close=0 is already allowed globally in v2; requested candidate
        # validation still rejects it, while v3 isolates it per candidate.
        if bad == {"pre_close": 0}:
            old_rows = old._table(data, DAY, None)
            old._fail(old._number(old_rows[OTHER]["pre_close"]) > 0, "CANDIDATE_INVALID")
        else:
            old.source_bytes(raw, DAY, request=old.request_contract(DAY), fetched_at_utc=FETCHED,
                             network_request_performed=True)
    bodies = pair(raw)
    assert json.loads(bodies[0]) == data
    save(tmp_path, bodies)
    source = auction.load(tmp_path, DAY)
    assert entry(source)["price"] == 10
    assert entry(source)["price_qualified"] is True
    assert source.rows[OTHER] == row(code=OTHER, **bad)


@pytest.mark.parametrize("amount,reason", [(None, "AMOUNT_INVALID_OR_MISSING"), (True, "AMOUNT_INVALID_OR_MISSING"),
                                           (-1, "AMOUNT_INVALID_OR_MISSING"), ("NaN", "AMOUNT_INVALID_OR_MISSING"),
                                           (float("nan"), "AMOUNT_INVALID_OR_MISSING"),
                                           (0, "POSITIVE_VOLUME_WITH_NONPOSITIVE_AMOUNT"),
                                           (1001, "PRICE_VOLUME_AMOUNT_UNIT_CONFLICT"),
                                           (999.97, "PRICE_VOLUME_AMOUNT_UNIT_CONFLICT")])
def test_bad_amount_only_removes_capacity_arithmetic(amount, reason):
    result = qualify(row(amount=amount))
    assert result["price"] == 10 and result["price_qualified"]
    assert result["capacity_proxy_verified"] is False and result["capacity_evidence"] == "UNKNOWN"
    assert result["capacity_reason"] == reason
    assert result["amount"] is result["capacity_amount"] is None
    assert result["reported_auction_amount"] is amount
    assert result["fallback_reason"] is None
    if amount == 1001:
        assert result["amount_identity_delta"] == "-1"


@pytest.mark.parametrize("amount,valid", [("999.98", True), ("1000.02", True),
                                         ("999.97999", False), ("1000.02001", False)])
def test_existing_two_cent_arithmetic_threshold_not_relaxed(amount, valid):
    result = qualify(row(amount=amount))
    assert result["price_qualified"] is True
    assert result["capacity_proxy_verified"] is valid
    assert result["capacity_arithmetic_tolerance_cny"] == "0.02"


@pytest.mark.parametrize("price", [7.65999, "7.65999", "7.66000"])
def test_price_precision_and_original_type_preserved(price):
    result = qualify(row(price=price, amount=766), opening=7.66)
    assert result["price"] == result["reported_auction_price"] == price
    assert type(result["price"]) is type(price)
    assert result["daily_open_cent_match"] is True
    assert result["reported_price_preserved"] is True
    assert result["price_reporting_precision_confirmed"] is False


@pytest.mark.parametrize("bad,reason", [(dict(price=None), "INVALID_PRICE"), (dict(price=0), "INVALID_PRICE"),
                                       (dict(price=True), "INVALID_PRICE"), (dict(price=float("nan")), "INVALID_PRICE"),
                                       (dict(vol=None), "INVALID_SHARE_VOLUME"), (dict(vol=-1), "INVALID_SHARE_VOLUME"),
                                       (dict(vol=1.5), "INVALID_SHARE_VOLUME"), (dict(vol=True), "INVALID_SHARE_VOLUME"),
                                       (dict(pre_close=0), "INVALID_PRE_CLOSE"), (dict(pre_close=None), "INVALID_PRE_CLOSE"),
                                       (dict(pre_close=True), "INVALID_PRE_CLOSE"), (dict(price=10.01), "PRICE_DAILY_OPEN_CONFLICT")])
def test_candidate_bad_numeric_is_pending_not_fallback_or_zero(bad, reason):
    result = qualify(row(**bad))
    assert result["status"] == "PENDING_CANONICAL_" + reason
    assert result["price"] is None and result["price_qualified"] is False
    assert result["amount"] is result["capacity_amount"] is None
    assert result["fallback_reason"] is None and result["capacity_proxy_verified"] is False
    assert "net_return" not in result


@pytest.mark.parametrize("price", [None, 0, "0"])
def test_zero_volume_amount_no_trade_is_explicit_not_capacity_or_zero_profit(price):
    result = qualify(row(price=price, vol=0, amount=0))
    assert result["status"] == "OBSERVED_NO_AUCTION_TRADE"
    assert result["price"] is result["amount"] is result["capacity_amount"] is None
    assert result["auction_trade_observed"] is False
    assert result["price_qualified"] is result["capacity_proxy_verified"] is False
    assert result["capacity_reason"] == "NO_AUCTION_TRADE_NOT_CAPACITY"
    assert "net_return" not in result and result["fallback_reason"] is None


@pytest.mark.parametrize("bad", [dict(price=-1), dict(price=True), dict(price=10), dict(amount=None), dict(amount=1), dict(amount=True)])
def test_zero_volume_conflict_not_no_trade(bad):
    result = qualify(row(**{**dict(vol=0, amount=0), **bad}))
    assert result["status"] == "PENDING_CANONICAL_ZERO_VOLUME_DATA_CONFLICT"
    assert result["auction_trade_observed"] is None and result["price"] is None


@pytest.mark.parametrize("pre", [None, 0, -1, True, "NaN"])
def test_zero_volume_with_invalid_preclose_remains_pending(pre):
    result = qualify(row(price=None, vol=0, amount=0, pre_close=pre))
    assert result["status"] == "PENDING_CANONICAL_INVALID_PRE_CLOSE"
    assert result["price"] is None and result["auction_trade_observed"] is None


@pytest.mark.parametrize("change,reason", [
    (lambda d: d["items"].append(d["items"][0]), "DUPLICATE_STOCK_ROW"),
    (lambda d: d["items"][0].__setitem__(1, "20260818"), "SOURCE_WRONG_TRADE_DATE"),
    (lambda d: d["items"][0].__setitem__(0, "wrong"), "INVALID_STOCK_CODE"),
    (lambda d: d.update(has_more=True), "PAGINATED_SOURCE_FORBIDDEN"),
    (lambda d: d.update(count=10), "POSITIVE_COUNT_MISMATCH"),
    (lambda d: d.update(count=True), "POSITIVE_COUNT_MISMATCH"),
    (lambda d: d["items"][0].pop(), "INVALID_ROW_SHAPE"),
    (lambda d: d["fields"].pop(), "INVALID_FIELDS"),
    (lambda d: d.update(extra=1), "INVALID_DATA_TABLE"),
    (lambda d: d["items"][0].__setitem__(2, {}), "UNSAFE_NESTED_TABLE_VALUE"),
])
def test_structure_identity_pagination_remain_fulltable_rejection(change, reason):
    data = table()
    change(data)
    with pytest.raises(auction.AuctionSourceError, match=reason):
        pair(envelope(data))


@pytest.mark.parametrize("count", [8000, 8001])
def test_potential_truncation_at_api_limit_rejected(count):
    data = table([row(code=f"{i:06}.SH") for i in range(count)])
    with pytest.raises(auction.AuctionSourceError, match="POSSIBLE_TRUNCATION"):
        auction.table_rows(data, DAY)


def test_json_nonfinite_bad_foreign_row_is_not_persistable():
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(auction.AuctionSourceError, match="NONFINITE_JSON"):
            pair(envelope(table([row(), row(code=OTHER, amount=value)])))
    raw = envelope().replace(b"1000", b"1e9999")
    with pytest.raises(auction.AuctionSourceError, match="NONFINITE_JSON"):
        pair(raw)


def test_reordered_fields_original_data_types_order_and_http_sha(tmp_path):
    data = table([row(price="10.000", amount="1000"), row(code="920001.BJ", pre_close=0)])
    data["fields"].reverse()
    for item in data["items"]:
        item.reverse()
    raw = envelope(data, msg="private response message")
    bodies = pair(raw)
    assert json.loads(bodies[0]) == data
    meta = json.loads(bodies[1])
    assert meta["http_response_sha256"] == auction._sha(raw)
    assert meta["data_sha256"] == auction._sha(bodies[0])
    assert b"private response message" not in b"".join(bodies)
    save(tmp_path, bodies)
    assert entry(auction.load(tmp_path, DAY))["price"] == "10.000"


@pytest.mark.parametrize("kind", ["empty", "absent", "denied", "precoverage"])
def test_only_supported_valid_unavailability_can_fallback(tmp_path, kind):
    if kind == "precoverage":
        result = entry(None, day="20241231")
        assert result["fallback_reason"] == "HISTORY_BEFORE_CANONICAL_COVERAGE"
    else:
        raw = envelope(table([] if kind == "empty" else [row(code=OTHER)]))
        if kind == "denied":
            raw = envelope(code=-1, msg="没有权限", data=None)
        save(tmp_path, pair(raw))
        result = entry(auction.load(tmp_path, DAY))
    assert result["status"] == "DAILY_OPEN_PRICE_PROXY_CAPACITY_UNKNOWN"
    assert result["price"] == 10 and result["price_qualified"] is False
    assert result["amount"] is result["capacity_amount"] is None
    assert result["capacity_proxy_verified"] is False


@pytest.mark.parametrize("msg", ["token invalid", "rate limit", "每分钟限频", "unknown error", "没有权限 token invalid"])
def test_operational_error_not_fallback(msg):
    with pytest.raises(auction.AuctionSourceError):
        pair(envelope(code=-1, msg=msg))


def test_missing_and_corrupt_pair_remain_distinct(tmp_path):
    with pytest.raises(auction.AuctionSourceMissing):
        auction.load(tmp_path, DAY)
    with pytest.raises(auction.AuctionSourceError, match="ATTEMPT_REQUIRED"):
        entry(None)
    paths = save(tmp_path)
    paths[1].unlink()
    with pytest.raises(auction.AuctionSourceError, match="INCOMPLETE_SOURCE_PAIR"):
        auction.load(tmp_path, DAY)
    paths[1].write_bytes(b"corrupt")
    with pytest.raises(auction.AuctionSourceError, match="INVALID_SOURCE_JSON"):
        auction.load(tmp_path, DAY)


@pytest.mark.parametrize("field,value", [("source_policy_id", "v2"), ("data_sha256", "0" * 64),
                                         ("rows", 999), ("network_request_performed", False),
                                         ("diagnostic_table_imported", True), ("old_codec_sha256", "0" * 64),
                                         ("known_before_0925", True), ("status", "CANONICAL_TABLE_EMPTY")])
def test_metadata_forgery_rejected(tmp_path, field, value):
    bodies = list(pair())
    meta = json.loads(bodies[1])
    meta[field] = value
    bodies[1] = auction._json(meta)
    save(tmp_path, bodies)
    with pytest.raises(auction.AuctionSourceError):
        auction.load(tmp_path, DAY)


def test_different_source_namespaces_no_v2_or_diagnostic_import(tmp_path):
    old_bodies = old.source_bytes(envelope(), DAY, request=old.request_contract(DAY),
                                 fetched_at_utc=FETCHED, network_request_performed=True)
    old_paths = old.source_paths(tmp_path, DAY)
    old_paths[0].parent.mkdir(parents=True)
    for path, body in zip(old_paths, old_bodies):
        path.write_bytes(body)
    with pytest.raises(auction.AuctionSourceMissing):
        auction.load(tmp_path, DAY)
    save(tmp_path, old_bodies)
    with pytest.raises(auction.AuctionSourceError):
        auction.load(tmp_path, DAY)
    for value in (table(), {"code": 0, "data": table(), "diagnostic_only": True}):
        with pytest.raises(auction.AuctionSourceError, match="INVALID_API_ENVELOPE"):
            pair(json.dumps(value).encode())
    source = old.load(tmp_path, DAY)
    with pytest.raises(auction.AuctionSourceError, match="V3_PRELOADED_SOURCE"):
        entry(source)


def test_pure_qualification_cannot_be_used_as_loaded_source():
    result = qualify()
    with pytest.raises(auction.AuctionSourceError, match="V3_PRELOADED_SOURCE"):
        entry(result)
    with pytest.raises(auction.AuctionSourceError, match="PRELOAD_MUST"):
        auction.LoadedSource(DAY, None, "CANONICAL_TABLE_PRESENT", {}, [])


def test_pure_missing_row_remains_pending_without_fallback_authority():
    result = auction.qualify_row(None, DAY, CODE, 10)
    assert result["status"] == "PENDING_CANONICAL_ROW_ABSENT"
    assert result["price"] is result["raw_values"] is result["fallback_reason"] is None
    assert result["capacity_proxy_verified"] is result["loaded_source_qualification_claimed"] is False
    assert result["price_qualified"] is result["source_import_allowed"] is False


def test_loaded_source_and_rows_bindings_are_immutable(tmp_path):
    paths = save(tmp_path)
    source = auction.load(tmp_path, DAY)
    result = entry(source)
    assert result["loaded_source_qualification_claimed"] is True
    assert result["source_files"] == [{"path": p.relative_to(tmp_path).as_posix(), "sha256": auction._sha(p.read_bytes())} for p in paths]
    with pytest.raises(FrozenInstanceError):
        source.status = "ENTITLEMENT_DENIED"
    with pytest.raises(TypeError):
        source.rows[CODE]["price"] = 99
    with pytest.raises(TypeError):
        source.source_files[0]["sha256"] = "0" * 64
    result["raw_values"]["price"] = 99
    assert source.rows[CODE]["price"] == 10


@pytest.mark.parametrize("target", ["root", "data", "meta"])
def test_symlink_roots_and_source_pair_rejected(tmp_path, target):
    root = tmp_path / "root"
    root.mkdir()
    paths = save(root)
    if target == "root":
        alias = tmp_path / "alias"
        alias.symlink_to(root, target_is_directory=True)
        root = alias
    else:
        path = paths[0 if target == "data" else 1]
        body = path.read_bytes()
        destination = tmp_path / "outside"
        destination.write_bytes(body)
        path.unlink()
        path.symlink_to(destination)
    with pytest.raises(auction.AuctionSourceError, match="UNSAFE"):
        auction.load(root, DAY)


@pytest.mark.parametrize("path", ["../daily.csv", "/daily.csv", "data//daily.csv", "a\\daily.csv", "C:daily.csv"])
def test_daily_binding_path_must_be_safe(path):
    with pytest.raises(auction.AuctionSourceError, match="UNSAFE_DAILY"):
        auction.entry_price(None, "20241231", CODE, 10,
                            daily_source_binding={"path": path, "sha256": "a" * 64})


@pytest.mark.parametrize("opening", [None, 0, -1, True, float("nan")])
def test_invalid_daily_open_fails(opening):
    with pytest.raises(auction.AuctionSourceError, match="INVALID_DAILY"):
        qualify(opening=opening)


def test_network_false_or_boolean_one_cannot_create_source():
    for observed in (False, 1, None):
        with pytest.raises(auction.AuctionSourceError, match="REAL_NETWORK"):
            auction.source_bytes(envelope(), DAY, request=auction.request_contract(DAY),
                                 fetched_at_utc=FETCHED, network_request_performed=observed)


def test_actual_token_in_data_or_envelope_not_persisted():
    token = "abcd1234_actual_credential_567890"
    for raw in (envelope(table([row(amount=token)])), envelope(msg=token),
                envelope(msg=token).replace(token.encode(), ("\\u0061" + token[1:]).encode())):
        with pytest.raises(auction.AuctionSourceError, match="CREDENTIAL"):
            pair(raw, token=token)


@pytest.mark.parametrize("request_id", ["9cf00385-1e41-44dd-ae49-7e0de2d9e4a0", "ab73dfee" * 4])
def test_provider_opaque_request_id_is_not_retained_or_misclassified(request_id):
    payload = json.loads(envelope())
    payload["request_id"] = request_id
    payload["msg"] = "discarded request metadata " + request_id
    bodies = pair(json.dumps(payload).encode(), token="actual_access_value_1234567890")
    assert json.loads(bodies[0]) == payload["data"]
    assert request_id.encode() not in b"".join(bodies)
    with pytest.raises(auction.AuctionSourceError, match="CREDENTIAL"):
        pair(envelope(table([row(), row(code=OTHER, price=request_id)])), token="actual_access_value_1234567890")


@pytest.mark.parametrize("opaque", ["a17cb690df473201ac709efd631852bd", "d73a" * 16,
                                   "mPt6/Qr+4X=" * 5])
def test_opaque_foreign_row_credential_rejected_when_token_argument_omitted(opaque):
    data = table([row(), row(code=OTHER, price=opaque)])
    with pytest.raises(auction.AuctionSourceError, match="CREDENTIAL"):
        pair(envelope(data))
    with pytest.raises(auction.AuctionSourceError, match="CREDENTIAL"):
        auction.table_rows(data, DAY)


def test_v2_sha_pin_cannot_be_changed(monkeypatch):
    monkeypatch.setattr(auction, "OLD_CODEC_SHA256", "0" * 64)
    with pytest.raises(auction.AuctionSourceError, match="PINNED_V2_CODEC"):
        qualify()


def test_full_table_input_and_returned_mapping_not_mutated():
    data = table([row(), row(code=OTHER, amount=None)])
    before = copy.deepcopy(data)
    rows = auction.table_rows(data, DAY)
    assert data == before
    with pytest.raises(TypeError):
        rows[CODE]["price"] = 99
    data["items"][0][2] = 99
    assert rows[CODE]["price"] == 10
