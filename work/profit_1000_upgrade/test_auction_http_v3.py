"""Synthetic original-HTTP adapter tests; no network or archived-table import."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from work.profit_1000_upgrade import auction_http_v3 as adapter
from work.profit_1000_upgrade import auction_truth_v3 as codec
from work.profit_1000_upgrade.test_auction_truth_v3 import DAY, CODE, OTHER, FETCHED, DAILY, row, table


def raw(data=None, **extra):
    return json.dumps({"code": 0, "msg": "", "data": table() if data is None else data, **extra},
                      ensure_ascii=False).encode()


def pair(response=None, *, target=adapter, **overrides):
    arguments = {"request": codec.request_contract(DAY), "fetched_at_utc": FETCHED,
                 "network_request_performed": True, **overrides}
    return target.source_bytes(raw() if response is None else response, DAY, **arguments)


@pytest.mark.parametrize("kind", ["ordinary", "empty", "numeric_strings", "bad_numeric_other", "reordered_fields",
                                   "request_id", "denied", "optional_missing"])
def test_all_legacy_accepted_http_bytes_produce_identical_source_pair(kind):
    data, extra = table(), {}
    if kind == "empty":
        data = table([])
    elif kind == "numeric_strings":
        data = table([row(price="9.99999", amount=None)])
    elif kind == "bad_numeric_other":
        data = table([row(), row(code=OTHER, price=None, amount=True)])
    elif kind == "reordered_fields":
        data["fields"].reverse()
        for values in data["items"]:
            values.reverse()
    elif kind == "request_id":
        extra = {"request_id": "9cf00385-1e41-44dd-ae49-7e0de2d9e4a0", "msg": "unpersisted opaque metadata"}
    elif kind == "optional_missing":
        del data["count"], data["has_more"]
    response = (json.dumps({"code": 2002, "msg": "没有权限", "data": None}).encode()
                if kind == "denied" else raw(data, **extra))
    assert pair(response, token="synthetic_access_value_12345") == pair(response, target=codec, token="synthetic_access_value_12345")


@pytest.mark.parametrize("detail", ["", "..."])
def test_compatible_detail_parses_original_http_without_rewriting_or_calling_old_source_bytes(tmp_path, monkeypatch, detail):
    response = b'  \n' + raw(detail=detail, request_id="ab73dfee" * 4) + b'\n\t'
    with pytest.raises(codec.AuctionSourceError, match="INVALID_API_ENVELOPE"):
        pair(response, target=codec)
    original_parse = codec._strict_json
    seen = []
    def parse_original(value):
        seen.append(value)
        return original_parse(value)
    def forbidden(*args, **kwargs):
        raise AssertionError("rewritten/old source_bytes call forbidden")
    monkeypatch.setattr(codec, "source_bytes", forbidden)
    monkeypatch.setattr(codec, "_strict_json", parse_original)
    body, meta_body = pair(response)
    assert seen == [response]
    meta = json.loads(meta_body)
    assert json.loads(body) == table()
    assert meta["http_response_sha256"] == hashlib.sha256(response).hexdigest()
    assert meta["http_response_bytes"] == len(response)
    assert meta["data_sha256"] == hashlib.sha256(body).hexdigest()
    assert b'"detail"' not in body + meta_body and b'ab73dfee' not in body + meta_body
    paths = codec.source_paths(tmp_path, DAY)
    paths[0].parent.mkdir(parents=True)
    for path, contents in zip(paths, (body, meta_body)):
        path.write_bytes(contents)
    loaded = codec.load(tmp_path, DAY)
    qualified = codec.entry_price(loaded, DAY, CODE, 10, daily_source_binding=DAILY)
    assert qualified["price"] == 10 and qualified["price_qualified"] is True
    assert qualified["actual_execution_claimed"] is qualified["actual_capacity_verified"] is False
    assert qualified["production_activation_allowed"] is False


@pytest.mark.parametrize("detail", ["…", "....", " ...", "... ", "..", "．．．", " ", "\n", "more rows", "permission denied", "token=secret", "{}", None,
                                     False, True, 0, 1, [], {}, [""]])
def test_other_detail_values_are_not_generalized_or_normalized(detail):
    with pytest.raises(codec.AuctionSourceError, match="NONEMPTY_OR_INVALID_API_DETAIL"):
        pair(raw(detail=detail))


@pytest.mark.parametrize("items,count", [([], 0), ([row()], 0), ([row()], 1),
                                       ([row(), row(code=OTHER)], 2)])
def test_exact_ascii_placeholder_with_explicit_complete_table_loads_unchanged(tmp_path, items, count):
    data = table(items)
    data["count"] = count
    response = raw(data, detail="...")
    bodies = pair(response)
    assert json.loads(bodies[0]) == data
    assert json.loads(bodies[1])["http_response_sha256"] == hashlib.sha256(response).hexdigest()
    paths = codec.source_paths(tmp_path, DAY)
    paths[0].parent.mkdir(parents=True)
    for path, body in zip(paths, bodies):
        path.write_bytes(body)
    loaded = codec.load(tmp_path, DAY)
    assert len(loaded.rows) == len(items)
    assert loaded.status == ("CANONICAL_TABLE_PRESENT" if items else "CANONICAL_TABLE_EMPTY")


@pytest.mark.parametrize("change", ["missing_has_more", "has_more_true", "has_more_zero", "has_more_null",
    "missing_count", "count_mismatch", "count_bool", "count_float", "count_negative", "count_null",
    "missing_items", "items_null", "data_null", "data_array", "data_string"])
def test_placeholder_requires_explicit_successful_complete_table_before_codec(change):
    data = table()
    if change == "missing_has_more":
        del data["has_more"]
    elif change.startswith("has_more_"):
        data["has_more"] = {"has_more_true": True, "has_more_zero": 0, "has_more_null": None}[change]
    elif change == "missing_count":
        del data["count"]
    elif change.startswith("count_"):
        data["count"] = {"count_mismatch": 2, "count_bool": False, "count_float": 0.0,
                         "count_negative": -1, "count_null": None}[change]
    elif change == "missing_items":
        del data["items"]
    elif change == "items_null":
        data["items"] = None
    else:
        data = {"data_null": None, "data_array": [], "data_string": "no data"}[change]
    response = json.dumps({"code": 0, "msg": "", "data": data, "detail": "..."}).encode()
    with pytest.raises(codec.AuctionSourceError, match="PLACEHOLDER_DETAIL_REQUIRES_SUCCESS_AND_COMPLETE_TABLE"):
        pair(response)


@pytest.mark.parametrize("code,message", [(2002, "没有权限"), (-1, "unknown error"), (1, "rate limit")])
@pytest.mark.parametrize("data", [None, table()])
def test_placeholder_never_expands_nonzero_api_error_or_unavailability_semantics(code, message, data):
    response = json.dumps({"code": code, "msg": message, "data": data, "detail": "..."}).encode()
    with pytest.raises(codec.AuctionSourceError, match="PLACEHOLDER_DETAIL_REQUIRES_SUCCESS_AND_COMPLETE_TABLE"):
        pair(response)


@pytest.mark.parametrize("code", [False, True, 0.0, "0", None])
def test_placeholder_still_requires_exact_integer_code(code):
    with pytest.raises(codec.AuctionSourceError, match="INVALID_API_ENVELOPE"):
        pair(raw(code=code, detail="..."))


def test_empty_detail_preserves_optional_pagination_compatibility():
    data = table()
    del data["has_more"], data["count"]
    body, metadata = pair(raw(data, detail=""))
    assert json.loads(body) == data and json.loads(metadata)["status"] == "CANONICAL_TABLE_PRESENT"


def test_placeholder_still_runs_strict_table_guards_not_just_pagination():
    variants = []
    duplicate = table([row(), row()])
    variants.append(duplicate)
    wrong_day = table([row(day="20260814")])
    variants.append(wrong_day)
    wrong_fields = table()
    wrong_fields["fields"][2] = "wrong_field"
    variants.append(wrong_fields)
    credential = table([row(), row(code=OTHER, amount="secret-value")])
    variants.append(credential)
    for data in variants:
        with pytest.raises(codec.AuctionSourceError):
            pair(raw(data, detail="..."))


@pytest.mark.parametrize("extra", [{"cursor": ""}, {"has_more": False}, {"total": 1}, {"details": ""},
                                    {"error": None}, {"source_import_allowed": True}])
def test_no_other_envelope_keys_are_ignored(extra):
    with pytest.raises(codec.AuctionSourceError, match="INVALID_API_ENVELOPE"):
        pair(raw(detail="", **extra))


@pytest.mark.parametrize("change,reason", [("pagination", "PAGINATED"), ("count", "COUNT_MISMATCH"),
    ("count_bool", "COUNT_MISMATCH"), ("duplicate", "DUPLICATE_STOCK"), ("wrong_date", "WRONG_TRADE_DATE"),
    ("fields", "INVALID_FIELDS"), ("row_shape", "INVALID_ROW_SHAPE"), ("nested", "UNSAFE_NESTED"),
    ("unknown_data", "INVALID_DATA_TABLE"), ("truncated", "POSSIBLE_TRUNCATION")])
def test_full_table_structural_guards_unchanged(change, reason):
    data = table()
    if change == "pagination":
        data["has_more"] = True
    elif change == "count":
        data["count"] = 2
    elif change == "count_bool":
        data["count"] = True
    elif change == "duplicate":
        data["items"] *= 2
    elif change == "wrong_date":
        data["items"][0][1] = "20260814"
    elif change == "fields":
        data["fields"][2] = "unexpected"
    elif change == "row_shape":
        data["items"][0].pop()
    elif change == "nested":
        data["items"][0][2] = {"price": 10}
    elif change == "unknown_data":
        data["next_page"] = ""
    else:
        data["items"] *= 8000
    with pytest.raises(codec.AuctionSourceError, match=reason):
        pair(raw(data, detail=""))


@pytest.mark.parametrize("response", [b'{"code":0,"code":0,"detail":"","data":null}',
    b'{"code":0,"detail":"","detail":"more","data":null}', b'{"code":0,"detail":"","data":NaN}',
    b'{"code":0,"detail":"","data":1e400}', b'{"code":true,"detail":"","data":null}',
    b'{"code":0.0,"detail":"","data":null}', b'[]', b'not json', b'\xff'])
def test_strict_original_json_and_integer_api_code_required(response):
    with pytest.raises(codec.AuctionSourceError):
        pair(response)


@pytest.mark.parametrize("where", ["row", "msg", "request_id", "escaped_msg"])
def test_known_token_anywhere_in_original_response_is_rejected(where):
    token = "synthetic_access_value_abc123"
    if where == "row":
        response = raw(table([row(amount=token)]), detail="")
    elif where == "request_id":
        response = raw(detail="", request_id=token)
    else:
        response = raw(detail="", msg=token)
        if where == "escaped_msg":
            response = response.replace(token.encode(), ("\\u0073" + token[1:]).encode())
    with pytest.raises(codec.AuctionSourceError, match="CREDENTIAL"):
        pair(response, token=token)


@pytest.mark.parametrize("value", ["a17cb690df473201ac709efd631852bd", "d73a" * 16, "secret-value", "mPt6/Qr+4X=" * 5])
def test_unrelated_row_credential_guard_still_applies_without_token(value):
    with pytest.raises(codec.AuctionSourceError, match="CREDENTIAL"):
        pair(raw(table([row(), row(code=OTHER, price=value)]), detail=""))


@pytest.mark.parametrize("message,reason", [("rate limit", "OPERATIONAL"), ("token invalid", "OPERATIONAL"),
                                            ("unknown failure", "UNKNOWN_API")])
def test_error_responses_are_not_imputed_as_source_unavailability(message, reason):
    response = json.dumps({"code": 2002, "msg": message, "data": None, "detail": ""}).encode()
    with pytest.raises(codec.AuctionSourceError, match=reason):
        pair(response)


def test_explicit_entitlement_semantics_unchanged_but_nonempty_detail_still_rejected():
    payload = {"code": 2002, "msg": "没有权限", "data": None, "detail": ""}
    body, metadata = pair(json.dumps(payload).encode())
    assert json.loads(body) is None and json.loads(metadata)["status"] == "ENTITLEMENT_DENIED"
    for changed in ({"data": table()}, {"detail": "unknown extra failure context"}):
        with pytest.raises(codec.AuctionSourceError):
            pair(json.dumps({**payload, **changed}).encode())


@pytest.mark.parametrize("value", [None, False, 1])
def test_real_network_flag_is_still_required(value):
    with pytest.raises(codec.AuctionSourceError, match="REAL_NETWORK"):
        pair(raw(detail=""), network_request_performed=value)


def test_wrong_request_and_oversized_http_still_rejected():
    request = codec.request_contract(DAY)
    request["params"]["trade_date"] = "20260814"
    with pytest.raises(codec.AuctionSourceError):
        pair(raw(detail=""), request=request)
    with pytest.raises(codec.AuctionSourceError, match="INVALID_HTTP_RESPONSE_BYTES"):
        pair(b" " * (codec.MAX_BYTES + 1))


def test_frozen_v3_sha_checked_before_parsing(monkeypatch):
    monkeypatch.setattr(adapter, "CODEC_SHA256", "0" * 64)
    with pytest.raises(codec.AuctionSourceError, match="PINNED_V3_CODEC"):
        pair(raw(detail=""))


def test_frozen_v3_sha_checked_again_before_return(monkeypatch):
    original = codec._metadata_expected
    def changed(*args):
        monkeypatch.setattr(adapter, "CODEC_SHA256", "0" * 64)
        return original(*args)
    monkeypatch.setattr(codec, "_metadata_expected", changed)
    with pytest.raises(codec.AuctionSourceError, match="PINNED_V3_CODEC"):
        pair(raw(detail=""))


def test_codec_symlink_rejected(tmp_path, monkeypatch):
    linked = tmp_path / "codec.py"
    linked.symlink_to(adapter.CODEC_PATH)
    monkeypatch.setattr(adapter, "CODEC_PATH", linked)
    with pytest.raises(codec.AuctionSourceError, match="PINNED_V3_CODEC"):
        pair(raw(detail=""))


def test_request_and_original_bytes_not_mutated_and_numeric_capacity_rules_unchanged():
    data = table([row(price="9.99999", amount=1001), row(code=OTHER, price=None)])
    response, request = raw(data, detail=""), codec.request_contract(DAY)
    before = copy.deepcopy(request)
    body, metadata = pair(response, request=request)
    assert request == before and json.loads(body) == data
    assert json.loads(metadata)["http_response_sha256"] == hashlib.sha256(response).hexdigest()
    candidates = codec.table_rows(json.loads(body), DAY)
    qualified = codec.qualify_row(candidates[CODE], DAY, CODE, 10)
    assert qualified["price"] == "9.99999" and qualified["price_qualified"] is True
    assert qualified["capacity_proxy_verified"] is False and qualified["amount"] is None
    assert codec.qualify_row(candidates[OTHER], DAY, OTHER, 10)["status"] == "PENDING_CANONICAL_INVALID_PRICE"
