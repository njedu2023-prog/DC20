"""Synthetic candidate-source fixtures only; no network, batch scope or labels."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
from types import MappingProxyType

import pytest

from work.profit_1000_upgrade import auction_candidate_scope as candidate

DAY, CODE, OTHER = "20250319", "000612.SZ", "600001.SH"
STAMP = "2026-09-13T01:30:00+00:00"
TOKEN = "synthetic_candidate_credential_not_a_real_token"


def payload(day=DAY, code=CODE, *, empty=False):
    return {"code": 0, "detail": "...", "msg": "not persisted", "request_id": "e150594759d54616a0a2d36f02ef4a82",
            "data": {"fields": list(candidate.FIELDS), "items": [] if empty else [[code, day, "7.65999", 12345, 777.77, 7.1]],
                     "has_more": False, "count": 0}}


def raw(value=None):
    return json.dumps(payload() if value is None else value, ensure_ascii=False, allow_nan=False).encode()


def pair(value=None, *, day=DAY, code=CODE, token=TOKEN, **changes):
    options = {"request": candidate.request_contract(day, code), "fetched_at_utc": STAMP,
               "network_request_performed": True, "token": token, **changes}
    return candidate.source_bytes(raw(value), day, code, **options)


def save(root, bodies, day=DAY, code=CODE):
    paths = candidate.source_paths(root, day, code)
    paths[0].parent.mkdir(parents=True, exist_ok=True)
    for path, body in zip(paths, bodies):
        path.write_bytes(body)
    return paths


def edit_meta(root, change):
    paths = candidate.source_paths(root, DAY, CODE)
    value = json.loads(paths[1].read_bytes())
    change(value)
    paths[1].write_bytes(candidate.codec._json(value))


def forbidden(*args, **kwargs):
    raise AssertionError("old/STK source authority, price, labels or network must not be used")


def test_exact_scope_paths_policies_and_no_price_api(tmp_path):
    assert candidate.request_contract(DAY, CODE) == {"api_name": "stk_auction", "params": {"trade_date": DAY, "ts_code": CODE},
                                                    "fields": list(candidate.FIELDS)}
    assert candidate.ROOT_PATH == "data/research/candidate_auction_v1"
    assert candidate.SOURCE_POLICY_ID == "dc20_research_candidate_auction_source_20260913_v1"
    paths = candidate.source_paths(tmp_path, DAY, CODE)
    assert [p.relative_to(tmp_path).as_posix() for p in paths] == [
        "data/research/candidate_auction_v1/2025/20250319/000612.SZ/data.json",
        "data/research/candidate_auction_v1/2025/20250319/000612.SZ/meta.json"]
    assert candidate.MAX_BYTES == 4_000_000 and candidate.MAX_ROWS == 1
    assert not hasattr(candidate, "entry_price") and not hasattr(candidate, "official_call")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("detail", ["absent", "", "..."])
def test_original_data_preserved_no_stk_or_old_source_authority(tmp_path, monkeypatch, detail):
    from work.profit_1000_upgrade import auction_stocks_scope, auction_http_v3
    for module, names in ((candidate.codec, ["source_bytes", "load", "entry_price", "qualify_row", "LoadedSource"]),
                          (auction_stocks_scope, ["source_bytes", "load", "LoadedStockSource"]),
                          (auction_http_v3, ["source_bytes"])):
        for name in names: monkeypatch.setattr(module, name, forbidden)
    value = payload()
    if detail == "absent": del value["detail"]
    else: value["detail"] = detail
    body, meta_body = pair(value)
    assert not list(tmp_path.iterdir())
    assert body == candidate.codec._json(value["data"])
    meta = json.loads(meta_body)
    assert meta["http_response_sha256"] == hashlib.sha256(raw(value)).hexdigest()
    assert meta["http_response_bytes"] == len(raw(value))
    assert meta["data_sha256"] == hashlib.sha256(body).hexdigest()
    assert meta["request"] == candidate.request_contract(DAY, CODE)
    assert all(meta[k] == v for k, v in candidate.FLAGS.items())
    assert TOKEN not in (body + meta_body).decode() and "not persisted" not in meta_body.decode()
    assert "synthetic" not in meta_body.decode() and "STK" not in meta_body.decode()
    paths = save(tmp_path, (body, meta_body))
    source = candidate.load(tmp_path, DAY, CODE)
    assert source.trade_date == DAY and source.ts_code == CODE and source.status == "CANDIDATE_TABLE_PRESENT"
    assert source.rows[CODE]["price"] == "7.65999" and source.rows[CODE]["amount"] == 777.77
    assert [dict(item) for item in source.source_files] == [{"path": p.relative_to(tmp_path).as_posix(), "sha256": hashlib.sha256(b).hexdigest()}
                                                          for p, b in zip(paths, (body, meta_body))]


def test_complete_empty_source_is_not_price_fallback_or_zero(tmp_path):
    bodies = pair(payload(empty=True))
    save(tmp_path, bodies)
    source = candidate.load(tmp_path, DAY, CODE)
    assert source.status == "CANDIDATE_TABLE_EMPTY" and not source.rows
    meta = json.loads(bodies[1])
    assert meta["rows"] == 0 and not meta["fallback_generated"] and not meta["entry_source_eligible"]
    assert not any(key in meta for key in ("price", "net_return", "entry_price", "no_fill"))


@pytest.mark.parametrize("day,code", [("20241231", CODE), ("20250230", CODE), ("2025-03-19", CODE), (20250319, CODE),
    (None, CODE), ("２０２５０３１９", CODE), (DAY, "000612.BJ"), (DAY, "000612.sz"), (DAY, "612.SZ"),
    (DAY, "０００６１２.SZ"), (DAY, "../000612.SZ"), (DAY, None), (DAY, 612)])
def test_strict_postcoverage_date_and_ascii_sh_sz_identity(tmp_path, day, code):
    for action in (lambda: candidate.request_contract(day, code), lambda: candidate.source_paths(tmp_path, day, code),
                   lambda: candidate.load(tmp_path, day, code)):
        with pytest.raises(candidate.AuctionSourceError): action()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("change", ["STK", "offset", "limit", "missing_code", "date", "code", "endpoint", "fields", "tuple_fields", "params_mapping", "extra"])
def test_exact_request_never_relabels_stk_or_other_stock(change):
    request = candidate.request_contract(DAY, CODE)
    if change == "STK": request["params"]["ts_type"] = "STK"
    elif change in ("offset", "limit"): request["params"][change] = 1
    elif change == "missing_code": del request["params"]["ts_code"]
    elif change == "date": request["params"]["trade_date"] = "20250320"
    elif change == "code": request["params"]["ts_code"] = OTHER
    elif change == "endpoint": request["api_name"] = "daily"
    elif change == "fields": request["fields"].reverse()
    elif change == "tuple_fields": request["fields"] = tuple(request["fields"])
    elif change == "params_mapping": request["params"] = MappingProxyType(request["params"])
    else: request["synthetic"] = True
    with pytest.raises(candidate.AuctionSourceError, match="REQUEST_CONTRACT_CHANGED"):
        pair(request=request)


@pytest.mark.parametrize("value", [None, False, True, 0.0, "0", -1, 1, 2002])
def test_only_exact_success_code_no_entitlement_fallback(value):
    response = payload()
    response["code"] = value
    with pytest.raises(candidate.AuctionSourceError, match="SUCCESS_CODE_ZERO"):
        pair(response)


@pytest.mark.parametrize("value", [None, False, 0, [], {}, "…", "....", " ...", "... ", "．．．", "provider message"])
def test_only_absent_empty_or_literal_placeholder_detail(value):
    response = payload()
    response["detail"] = value
    with pytest.raises(candidate.AuctionSourceError, match="API_DETAIL"):
        pair(response)


@pytest.mark.parametrize("key,value", [("has_more", None), ("has_more", True), ("has_more", 0), ("has_more", "false"),
    ("count", None), ("count", True), ("count", False), ("count", 0.0), ("count", -1), ("count", 2), ("items", None), ("items", {})])
def test_explicit_completeness_exact_types(key, value):
    response = payload()
    response["data"][key] = value
    with pytest.raises(candidate.AuctionSourceError, match="EXPLICIT_COMPLETENESS"):
        pair(response)


@pytest.mark.parametrize("key", ["has_more", "count", "items"])
def test_missing_completeness_not_imputed(key):
    response = payload()
    del response["data"][key]
    with pytest.raises(candidate.AuctionSourceError, match="EXPLICIT_COMPLETENESS"):
        pair(response)


@pytest.mark.parametrize("kind", ["wrongday", "wrongcode", "two", "duplicate", "many", "fields", "shortrow", "nested", "unknown_data", "unknown_envelope"])
def test_same_day_same_code_max_one_and_complete_shape(kind):
    value = payload()
    table = value["data"]
    if kind == "wrongday": table["items"][0][1] = "20250320"
    elif kind == "wrongcode": table["items"][0][0] = OTHER
    elif kind in ("two", "duplicate", "many"):
        extra = list(table["items"][0])
        if kind == "two": extra[0] = OTHER
        table["items"].extend([extra] * (7999 if kind == "many" else 1))
    elif kind == "fields": table["fields"][2] = "vol"
    elif kind == "shortrow": table["items"][0].pop()
    elif kind == "nested": table["items"][0][2] = {"value": 1}
    elif kind == "unknown_data": table["offset"] = 1
    else: value["extra"] = "ignored?"
    with pytest.raises(candidate.AuctionSourceError): pair(value)


def test_original_reordered_fields_and_all_numeric_errors_are_only_structural(tmp_path):
    value = payload()
    value["data"]["items"][0][2:] = [None, -1, False, 0]
    value["data"]["items"][0].reverse()
    value["data"]["fields"].reverse()
    value["data"]["count"] = 1
    bodies = pair(value)
    assert json.loads(bodies[0]) == value["data"]
    save(tmp_path, bodies)
    loaded = candidate.load(tmp_path, DAY, CODE)
    assert loaded.rows[CODE]["price"] is None and loaded.rows[CODE]["vol"] == -1
    assert loaded.rows[CODE]["amount"] is False and loaded.rows[CODE]["pre_close"] == 0
    assert not json.loads(bodies[1])["capacity_qualification_performed"]


@pytest.mark.parametrize("original", [None, "raw", b"", b"x" * 4_000_001, b"notjson", b"\xff",
    b'{"code":0,"code":0}', b'{"data":{"count":NaN}}', b'{"data":{"count":1e400}}'])
def test_raw_bounded_strict_json_required(original):
    with pytest.raises(candidate.AuctionSourceError):
        candidate.source_bytes(original, DAY, CODE, request=candidate.request_contract(DAY, CODE),
                               fetched_at_utc=STAMP, network_request_performed=True)


@pytest.mark.parametrize("where", ["msg", "request_id", "detail", "row", "field", "key"])
def test_credential_in_envelope_or_data_never_persisted(where):
    value = payload()
    if where == "row": value["data"]["items"][0][2] = TOKEN
    elif where == "field": value["data"]["fields"][2] = TOKEN
    elif where == "key": value[TOKEN] = TOKEN
    else: value[where] = TOKEN
    with pytest.raises(candidate.AuctionSourceError): pair(value)


def test_known_escaped_credential_and_unknown_opaque_string_rejected():
    value = payload()
    value["msg"] = TOKEN
    escaped = "".join("\\u%04x" % ord(char) for char in TOKEN)
    original = raw(value).replace(TOKEN.encode(), escaped.encode())
    with pytest.raises(candidate.AuctionSourceError, match="CREDENTIAL"):
        candidate.source_bytes(original, DAY, CODE, request=candidate.request_contract(DAY, CODE),
                               fetched_at_utc=STAMP, network_request_performed=True, token=TOKEN)
    value = payload()
    value["data"]["items"][0][2] = "abcd1234" * 8
    with pytest.raises(candidate.AuctionSourceError, match="CREDENTIAL"):
        pair(value, token="")


@pytest.mark.parametrize("value", [False, None, 1, "true"])
def test_real_request_assertion_must_be_exact_true(value):
    with pytest.raises(candidate.AuctionSourceError, match="REAL_NETWORK"):
        pair(network_request_performed=value)


@pytest.mark.parametrize("stamp", [None, 1, "2025-03-19T00:00:00+00:00", "2026-09-13T09:30:00+08:00", "2026-09-13T01:30:00", "bad", "x" * 100])
def test_fetched_at_requires_utc_and_event_availability(stamp):
    with pytest.raises(candidate.AuctionSourceError, match="FETCH"):
        pair(fetched_at_utc=stamp)


@pytest.mark.parametrize("key,value", [("source_policy_id", "old"), ("schema_version", "old"), ("ts_code", OTHER), ("trade_date", "20250320"),
    ("api_code", False), ("api_code", 2002), ("rows", True), ("rows", 0), ("status", "CANDIDATE_TABLE_EMPTY"),
    ("http_response_bytes", True), ("http_response_bytes", 0), ("http_response_bytes", 4_000_001),
    ("http_response_sha256", "bad"), ("http_detail_kind", "PROVIDER_MESSAGE"), ("data_sha256", "0" * 64),
    ("actual_execution_claimed", True), ("entry_source_eligible", True), ("network_request_performed", 1),
    ("fetched_at_utc", "2025-03-19T00:00:00+00:00")])
def test_metadata_tamper_always_rejected(tmp_path, key, value):
    save(tmp_path, pair())
    edit_meta(tmp_path, lambda meta: meta.update({key: value}))
    with pytest.raises(candidate.AuctionSourceError): candidate.load(tmp_path, DAY, CODE)


@pytest.mark.parametrize("kind", ["request", "synthetic", "deps", "rawbody", "whitespace", "duplicate", "table_rehashed"])
def test_cross_policy_import_or_representation_and_body_tamper_rejected(tmp_path, kind):
    paths = save(tmp_path, pair())
    if kind == "request": edit_meta(tmp_path, lambda meta: meta["request"]["params"].update(ts_type="STK"))
    elif kind == "synthetic": edit_meta(tmp_path, lambda meta: meta.update(synthetic_network_request=True))
    elif kind == "deps": edit_meta(tmp_path, lambda meta: meta["frozen_dependencies"].update({"auction_truth.py": "0" * 64}))
    elif kind == "rawbody": paths[0].write_bytes(paths[0].read_bytes().replace(b"7.65999", b"8.65999"))
    elif kind == "whitespace": paths[1].write_bytes(paths[1].read_bytes() + b" ")
    elif kind == "duplicate": paths[1].write_bytes(b'{"api_code":0,"api_code":0}')
    else:
        data = json.loads(paths[0].read_bytes())
        data["items"][0][0] = OTHER
        paths[0].write_bytes(candidate.codec._json(data))
        edit_meta(tmp_path, lambda meta: meta.update(data_sha256=hashlib.sha256(paths[0].read_bytes()).hexdigest()))
    with pytest.raises(candidate.AuctionSourceError): candidate.load(tmp_path, DAY, CODE)


def test_loaded_snapshot_deep_immutable_and_bound_to_bytes(tmp_path):
    paths = save(tmp_path, pair())
    source = candidate.load(tmp_path, DAY, CODE)
    with pytest.raises(FrozenInstanceError): source.ts_code = OTHER
    for mutate in (lambda: source.rows.__setitem__(CODE, {}), lambda: source.rows[CODE].__setitem__("price", 0),
                   lambda: source.source_files[0].__setitem__("sha256", "0" * 64),
                   lambda: source.request["params"].__setitem__("ts_code", OTHER)):
        with pytest.raises((TypeError, AttributeError)): mutate()
    assert isinstance(source.request["fields"], tuple)
    old_sha = source.source_files[0]["sha256"]
    paths[0].write_bytes(b"{}")
    assert source.rows[CODE]["price"] == "7.65999" and source.source_files[0]["sha256"] == old_sha
    with pytest.raises(candidate.AuctionSourceError): candidate.load(tmp_path, DAY, CODE)


def test_no_public_forged_loaded_source():
    with pytest.raises(candidate.AuctionSourceError, match="CREATED_BY_LOAD"):
        candidate.LoadedCandidateSource(DAY, CODE, "CANDIDATE_TABLE_PRESENT", {}, (), candidate.request_contract(DAY, CODE))


def test_absent_and_partial_pair_never_empty_or_fallback(tmp_path):
    with pytest.raises(candidate.CandidateSourceMissing, match="NOT_ATTEMPTED"):
        candidate.load(tmp_path, DAY, CODE)
    assert not list(tmp_path.iterdir())
    path = candidate.source_paths(tmp_path, DAY, CODE)[0]
    path.parent.mkdir(parents=True)
    path.write_bytes(pair()[0])
    with pytest.raises(candidate.AuctionSourceError): candidate.load(tmp_path, DAY, CODE)


@pytest.mark.parametrize("which", ["root", "parent", "data", "meta"])
def test_symlink_root_and_source_paths_rejected(tmp_path, which):
    root = tmp_path / "root"
    root.mkdir()
    paths = save(root, pair())
    if which == "root":
        alias = tmp_path / "alias"
        alias.symlink_to(root, target_is_directory=True)
        root = alias
    elif which == "parent":
        directory = paths[0].parent
        target = tmp_path / "moved"
        directory.rename(target)
        directory.symlink_to(target, target_is_directory=True)
    else:
        path = paths[0 if which == "data" else 1]
        moved = tmp_path / "moved-file"
        path.rename(moved)
        path.symlink_to(moved)
    with pytest.raises(candidate.AuctionSourceError): candidate.load(root, DAY, CODE)


@pytest.mark.parametrize("dependency", list(candidate.PINNED_DEPENDENCIES))
def test_frozen_dependency_before_source_and_load(tmp_path, monkeypatch, dependency):
    save(tmp_path, pair())
    monkeypatch.setitem(candidate.PINNED_DEPENDENCIES, dependency, "0" * 64)
    with pytest.raises(candidate.AuctionSourceError, match="DEPENDENCY_CHANGED"): pair()
    with pytest.raises(candidate.AuctionSourceError, match="DEPENDENCY_CHANGED"): candidate.load(tmp_path, DAY, CODE)


def test_final_dependency_guard_rechecks_after_serialization(monkeypatch):
    original = candidate._metadata
    def changed(*args):
        result = original(*args)
        monkeypatch.setitem(candidate.PINNED_DEPENDENCIES, "auction_truth.py", "0" * 64)
        return result
    monkeypatch.setattr(candidate, "_metadata", changed)
    with pytest.raises(candidate.AuctionSourceError, match="DEPENDENCY_CHANGED"): pair()


@pytest.mark.parametrize("which", [0, 1])
def test_load_is_bounded_before_reading_large_file(tmp_path, which):
    paths = save(tmp_path, pair())
    paths[which].write_bytes(b"x" * ((candidate.MAX_BYTES if which == 0 else candidate.MAX_META_BYTES) + 1))
    with pytest.raises(candidate.AuctionSourceError, match="FILE_SIZE"):
        candidate.load(tmp_path, DAY, CODE)
