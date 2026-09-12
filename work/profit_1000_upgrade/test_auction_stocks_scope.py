"""Synthetic source-only STK scope, provenance, and immutable-load tests."""
from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
import hashlib
import json

import pytest

from work.profit_1000_upgrade import auction_http_v3, auction_stocks_scope as stocks, auction_truth_v3 as codec

DAY, OTHER_DAY = "20250319", "20250320"
CODE, OTHER = "600001.SH", "600002.SH"
FETCHED = "2026-09-12T10:00:00Z"
TOKEN = "synthetic_credential_123456789_test"


def table(rows=None):
    return {"fields": list(stocks.FIELDS), "items": [[CODE, DAY, "9.99999", 100, 1001, 9]] if rows is None else rows,
            "count": 0, "has_more": False}


def raw(data=None, **extra):
    return json.dumps({"code": 0, "msg": "not persisted", "request_id": "ab73dfee" * 4,
                      "data": table() if data is None else data, "detail": "...", **extra}, ensure_ascii=False).encode()


def pair(response=None, **overrides):
    arguments = {"request": stocks.request_contract(DAY), "fetched_at_utc": FETCHED,
                 "network_request_performed": True, **overrides}
    return stocks.source_bytes(raw() if response is None else response, DAY, **arguments)


def save(root, bodies=None):
    paths = stocks.source_paths(root, DAY)
    paths[0].parent.mkdir(parents=True, exist_ok=True)
    for path, body in zip(paths, pair() if bodies is None else bodies):
        path.write_bytes(body)
    return paths


def forbidden(*args, **kwargs):
    raise AssertionError("old HTTP envelope rewriting, old LoadedSource, entry, or network forbidden")


def test_request_and_source_namespace_are_new_explicit_stocks_only():
    assert stocks.request_contract(DAY) == {"api_name": "stk_auction", "params": {"trade_date": DAY, "ts_type": "STK"},
                                          "fields": list(stocks.FIELDS)}
    assert stocks.ROOT_PATH == "data/research/stocks_scoped_auction_v1"
    assert stocks.SOURCE_POLICY_ID == "dc20_research_stocks_scoped_auction_source_20260912_v1"
    assert stocks.MAX_ROWS == 8000 and stocks.MAX_BYTES == 4_000_000
    assert not hasattr(stocks, "entry_price")


@pytest.mark.parametrize("detail", ["ABSENT", "", "..."])
def test_original_http_and_data_preserved_new_typed_metadata_loads(tmp_path, monkeypatch, detail):
    payload = json.loads(raw())
    if detail == "ABSENT":
        del payload["detail"]
    else:
        payload["detail"] = detail
    response = b" \n" + json.dumps(payload).encode() + b"\n"
    before = copy.deepcopy(payload["data"])
    monkeypatch.setattr(auction_http_v3, "source_bytes", forbidden)
    monkeypatch.setattr(codec, "source_bytes", forbidden)
    monkeypatch.setattr(codec, "load", forbidden)
    monkeypatch.setattr(codec, "LoadedSource", forbidden)
    monkeypatch.setattr(codec, "entry_price", forbidden)
    body, meta_body = pair(response)
    assert list(tmp_path.iterdir()) == []
    assert json.loads(body) == before
    meta = json.loads(meta_body)
    assert meta["request"] == stocks.request_contract(DAY) and meta["security_type"] == "STK"
    assert meta["http_response_sha256"] == hashlib.sha256(response).hexdigest()
    assert meta["http_response_bytes"] == len(response) and meta["data_sha256"] == hashlib.sha256(body).hexdigest()
    assert meta["http_detail_kind"] == ("ABSENT" if detail == "ABSENT" else "EMPTY_STRING" if detail == "" else "ASCII_PLACEHOLDER")
    assert meta["source_policy_id"] != codec.SOURCE_POLICY_ID
    assert b"ab73dfee" not in body + meta_body and b"not persisted" not in body + meta_body
    for key, value in stocks.FLAGS.items():
        assert type(meta[key]) is type(value) and meta[key] == value
    paths = save(tmp_path, (body, meta_body))
    snapshot = stocks.load(tmp_path, DAY)
    assert snapshot.status == "STOCKS_SCOPED_TABLE_PRESENT" and snapshot.rows[CODE]["price"] == "9.99999"
    assert snapshot.rows[CODE]["amount"] == 1001
    assert dict(snapshot.request["params"]) == {"trade_date": DAY, "ts_type": "STK"}
    assert snapshot.request["fields"] == stocks.FIELDS
    assert [dict(b) for b in snapshot.source_files] == [
        {"path": path.relative_to(tmp_path).as_posix(), "sha256": hashlib.sha256(contents).hexdigest()}
        for path, contents in zip(paths, (body, meta_body))]


def test_valid_empty_table_is_observed_source_not_fallback_or_return_zero(tmp_path):
    save(tmp_path, pair(raw(table([]))))
    snapshot = stocks.load(tmp_path, DAY)
    assert snapshot.status == "STOCKS_SCOPED_TABLE_EMPTY" and len(snapshot.rows) == 0
    assert not hasattr(snapshot, "entry_price") and not hasattr(snapshot, "net_return")


@pytest.mark.parametrize("change", ["old_date_only", "ts_code", "offset", "limit", "wrong_type", "wrong_day",
                                    "wrong_endpoint", "wrong_fields", "field_order", "extra_top"])
def test_request_scope_cannot_be_stripped_or_expanded(change):
    request = stocks.request_contract(DAY)
    if change == "old_date_only":
        del request["params"]["ts_type"]
    elif change in {"ts_code", "offset", "limit"}:
        request["params"][change] = CODE if change == "ts_code" else 100
    elif change == "wrong_type":
        request["params"]["ts_type"] = "ETF"
    elif change == "wrong_day":
        request["params"]["trade_date"] = OTHER_DAY
    elif change == "wrong_endpoint":
        request["api_name"] = "stk_auction_o"
    elif change == "wrong_fields":
        request["fields"] = ["price"]
    elif change == "field_order":
        request["fields"].reverse()
    else:
        request["extra"] = 0
    with pytest.raises(stocks.AuctionSourceError, match="REQUEST"):
        pair(request=request)


@pytest.mark.parametrize("value", [False, True, "0", 0.0, None, 1, -1, 2002])
def test_only_success_exact_integer_zero_produces_source(value):
    with pytest.raises(stocks.AuctionSourceError):
        pair(raw(code=value, msg="没有权限"))


@pytest.mark.parametrize("value", [None, [], {}, False, 0, "…", "....", " ...", "... ", "error", "．．．"])
def test_detail_is_narrow_exact_values_without_normalization(value):
    with pytest.raises(stocks.AuctionSourceError, match="DETAIL"):
        pair(raw(detail=value))


@pytest.mark.parametrize("extra", [{"cursor": ""}, {"has_more": False}, {"total": 1}, {"error": None}])
def test_unknown_http_envelope_fields_still_rejected(extra):
    with pytest.raises(stocks.AuctionSourceError, match="ENVELOPE"):
        pair(raw(**extra))


@pytest.mark.parametrize("detail", ["", "..."])
@pytest.mark.parametrize("change", ["missing_has_more", "true", "integer_false", "missing_count", "count_bool", "count_float",
                                    "count_mismatch", "count_negative", "items_null"])
def test_explicit_complete_pagination_required_even_when_detail_empty(detail, change):
    data = table()
    if change == "missing_has_more":
        del data["has_more"]
    elif change == "true":
        data["has_more"] = True
    elif change == "integer_false":
        data["has_more"] = 0
    elif change == "missing_count":
        del data["count"]
    elif change == "count_bool":
        data["count"] = False
    elif change == "count_float":
        data["count"] = 0.0
    elif change == "count_mismatch":
        data["count"] = 2
    elif change == "count_negative":
        data["count"] = -1
    else:
        data["items"] = None
    with pytest.raises(stocks.AuctionSourceError, match="PAGINATION"):
        pair(raw(data, detail=detail))


@pytest.mark.parametrize("change,reason", [("duplicate", "DUPLICATE_STOCK"), ("day", "WRONG_TRADE_DATE"),
    ("code", "CODE"), ("fields", "INVALID_FIELDS"), ("shape", "ROW_SHAPE"), ("extra_data", "INVALID_DATA_TABLE"),
    ("nested", "UNSAFE_NESTED"), ("row_cap", "TRUNCATION")])
def test_full_table_frozen_structure_and_identity_validation_retained(change, reason):
    data = table()
    if change == "duplicate":
        data["items"] *= 2
    elif change == "day":
        data["items"][0][1] = OTHER_DAY
    elif change == "code":
        data["items"][0][0] = "bad-code"
    elif change == "fields":
        data["fields"][2] = "bad_field"
    elif change == "shape":
        data["items"][0].pop()
    elif change == "extra_data":
        data["offset"] = 0
    elif change == "nested":
        data["items"][0][2] = {"bad": 1}
    else:
        data["items"] *= 8000
    with pytest.raises(stocks.AuctionSourceError, match=reason):
        pair(raw(data))


@pytest.mark.parametrize("response", [b"not JSON", b"\xff", b'{"code":0,"code":0}',
    b'{"data":{"count":0,"count":1}}', b'{"data":NaN}', b'{"data":1e400}', b'[]', b'null'])
def test_strict_original_json_rejects_malformed_duplicate_and_nonfinite(response):
    with pytest.raises(stocks.AuctionSourceError):
        pair(response)


@pytest.mark.parametrize("where", ["data", "msg", "request_id", "escaped"])
def test_credential_guard_covers_original_envelope_and_escaped_json(where):
    data = table()
    extra = {}
    if where == "data":
        data["items"][0][4] = TOKEN
    else:
        extra["request_id" if where == "request_id" else "msg"] = TOKEN
    response = raw(data, **extra)
    if where == "escaped":
        response = response.replace(TOKEN.encode(), ("\\u0073" + TOKEN[1:]).encode())
    with pytest.raises(stocks.AuctionSourceError, match="CREDENTIAL"):
        pair(response, token=TOKEN)


@pytest.mark.parametrize("value", ["secret", "ab12" * 16, "mPt6/Qr+4X=" * 5])
def test_unknown_candidate_credential_not_persisted_when_token_omitted(value):
    data = table([[CODE, DAY, 10, 100, 1000, 9], [OTHER, DAY, value, 100, 1000, 9]])
    with pytest.raises(stocks.AuctionSourceError, match="CREDENTIAL"):
        pair(raw(data))


def test_bad_numeric_candidate_rows_remain_raw_without_entry_qualification(tmp_path):
    data = table([[CODE, DAY, None, True, "NaN", 0], [OTHER, DAY, 10, 100, None, 9]])
    bodies = pair(raw(data))
    assert json.loads(bodies[0]) == data
    save(tmp_path, bodies)
    snapshot = stocks.load(tmp_path, DAY)
    assert snapshot.rows[CODE]["price"] is None and snapshot.rows[CODE]["vol"] is True
    assert json.loads(bodies[1])["numeric_qualification_scope"] == "NOT_PERFORMED_SOURCE_ONLY"


@pytest.mark.parametrize("field,value", [("source_policy_id", codec.SOURCE_POLICY_ID), ("security_type", "ETF"),
    ("api_code", False), ("api_code", 2002), ("rows", True), ("rows", 2), ("http_response_bytes", True),
    ("http_response_bytes", 4_000_001), ("http_response_sha256", "bad"), ("data_sha256", "0" * 64),
    ("status", "ENTITLEMENT_DENIED"), ("status", "STOCKS_SCOPED_TABLE_EMPTY"), ("http_detail_kind", "IGNORED"),
    ("training_performed", True), ("actual_execution_claimed", True), ("label_source_eligible", True),
    ("network_request_performed", 1), ("fetched_at_utc", "2025-03-19T01:25:00Z")])
def test_load_rejects_metadata_type_provenance_status_or_source_hash_changes(tmp_path, field, value):
    paths = save(tmp_path)
    metadata = json.loads(paths[1].read_bytes())
    metadata[field] = value
    paths[1].write_bytes(codec._json(metadata))
    with pytest.raises(stocks.AuctionSourceError):
        stocks.load(tmp_path, DAY)


@pytest.mark.parametrize("change", ["strip_scope", "offset", "extra_meta", "raw_whitespace", "data_without_rehash", "rehash_bad_pagination"])
def test_load_cannot_hide_new_request_or_invalid_data_behind_metadata(tmp_path, change):
    paths = save(tmp_path)
    metadata = json.loads(paths[1].read_bytes())
    if change == "strip_scope":
        del metadata["request"]["params"]["ts_type"]
    elif change == "offset":
        metadata["request"]["params"]["offset"] = 0
    elif change == "extra_meta":
        metadata["unknown"] = 0
    elif change == "raw_whitespace":
        paths[0].write_bytes(paths[0].read_bytes() + b" ")
    else:
        data = json.loads(paths[0].read_bytes())
        data["has_more"] = True
        body = codec._json(data)
        paths[0].write_bytes(body)
        if change == "rehash_bad_pagination":
            metadata["data_sha256"] = hashlib.sha256(body).hexdigest()
    paths[1].write_bytes(codec._json(metadata))
    with pytest.raises(stocks.AuctionSourceError):
        stocks.load(tmp_path, DAY)


def test_loaded_snapshot_is_deeply_immutable_and_does_not_re_read_later_file_edits(tmp_path):
    paths = save(tmp_path)
    snapshot = stocks.load(tmp_path, DAY)
    with pytest.raises(FrozenInstanceError):
        snapshot.status = "changed"
    for operation in (lambda: snapshot.rows[CODE].__setitem__("price", 0),
                      lambda: snapshot.source_files[0].__setitem__("sha256", "0" * 64),
                      lambda: snapshot.request["params"].__setitem__("ts_type", "ETF")):
        with pytest.raises((TypeError, AttributeError)):
            operation()
    assert type(snapshot.request["fields"]) is tuple
    before = [dict(item) for item in snapshot.source_files]
    paths[0].write_bytes(b"changed later")
    assert snapshot.rows[CODE]["price"] == "9.99999" and [dict(item) for item in snapshot.source_files] == before
    with pytest.raises(stocks.AuctionSourceError):
        stocks.load(tmp_path, DAY)


def test_loaded_source_cannot_be_constructed_from_diagnostic_dictionary():
    with pytest.raises(stocks.AuctionSourceError, match="MUST_BE_CREATED_BY_LOAD"):
        stocks.LoadedStockSource(DAY, "STOCKS_SCOPED_TABLE_EMPTY", {}, [], stocks.request_contract(DAY))


def test_missing_or_partial_pair_never_creates_files_or_an_empty_source(tmp_path):
    with pytest.raises(stocks.StockSourceMissing):
        stocks.load(tmp_path, DAY)
    assert list(tmp_path.iterdir()) == []
    paths = save(tmp_path)
    paths[1].unlink()
    with pytest.raises(stocks.AuctionSourceError, match="INCOMPLETE"):
        stocks.load(tmp_path, DAY)


@pytest.mark.parametrize("target", ["root", "data", "metadata"])
def test_source_root_and_pairs_reject_symlinks(tmp_path, target):
    root = tmp_path / "root"
    root.mkdir()
    paths = save(root)
    if target == "root":
        alias = tmp_path / "alias"
        alias.symlink_to(root, target_is_directory=True)
        root = alias
    else:
        path = paths[0 if target == "data" else 1]
        destination = tmp_path / "outside"
        destination.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(destination)
    with pytest.raises(stocks.AuctionSourceError, match="UNSAFE"):
        stocks.load(root, DAY)


@pytest.mark.parametrize("pin", ["CODEC_SHA256", "HTTP_ADAPTER_SHA256"])
def test_frozen_dependencies_checked_on_source_creation_and_load(tmp_path, monkeypatch, pin):
    save(tmp_path)
    monkeypatch.setattr(stocks, pin, "0" * 64)
    with pytest.raises(stocks.AuctionSourceError, match="DEPENDENCY_CHANGED"):
        pair()
    with pytest.raises(stocks.AuctionSourceError, match="DEPENDENCY_CHANGED"):
        stocks.load(tmp_path, DAY)


def test_dependencies_checked_again_after_validation(monkeypatch):
    original = stocks._metadata_expected
    def changed(*args):
        result = original(*args)
        monkeypatch.setattr(stocks, "HTTP_ADAPTER_SHA256", "0" * 64)
        return result
    monkeypatch.setattr(stocks, "_metadata_expected", changed)
    with pytest.raises(stocks.AuctionSourceError, match="DEPENDENCY_CHANGED"):
        pair()


@pytest.mark.parametrize("value", [None, False, 1])
def test_real_network_attempt_flag_is_exact_true(value):
    with pytest.raises(stocks.AuctionSourceError, match="REAL_NETWORK"):
        pair(network_request_performed=value)


@pytest.mark.parametrize("response", [b"", "not bytes", bytearray(b"{}"), pytest.param(b" " * 4_000_001, id="oversized-4mb")])
def test_bounded_original_http_bytes_only(response):
    with pytest.raises(stocks.AuctionSourceError, match="HTTP_RESPONSE_BYTES"):
        pair(response)
