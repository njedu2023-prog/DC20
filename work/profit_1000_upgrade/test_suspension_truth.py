"""Offline event-codec tests. Real immutable ZIP is opt-in, never downloaded."""
import hashlib
import io
import json
import os
from pathlib import Path
import zipfile

import pytest

from work.profit_1000_upgrade import suspension_truth as source

DAY, CODE = "20260723", "002036.SZ"
FETCHED = "2026-09-12T04:13:50+00:00"
TOKEN = "test-secret-never-persisted"


def response(rows=None, *, update=None, envelope=None):
    rows = [[CODE, DAY, None, "S"]] if rows is None else rows
    data = {"fields": list(source.FIELDS), "items": rows, "count": 0, "has_more": False}
    data.update(update or {})
    return json.dumps({"code": 0, "data": data, **(envelope or {})}).encode()


def encode(raw=None, **kwargs):
    args = {"request_params": source.request_parameters(DAY, CODE),
            "fetched_at_utc": FETCHED, "network_request_performed": True}
    args.update(kwargs)
    return source.source_bytes(response() if raw is None else raw, DAY, CODE, **args)


def put_pair(root, pair=None):
    paths = source.source_paths(root, DAY, CODE)
    for path, raw in zip(paths, encode() if pair is None else pair):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return paths


def test_raw_source_preserves_values_but_not_http_messages(tmp_path):
    payload = response(envelope={"msg": TOKEN, "other": TOKEN})
    pair = encode(payload, token=TOKEN)
    assert json.loads(pair[0]) == json.loads(payload)["data"]
    meta = json.loads(pair[1])
    assert meta["http_response_sha256"] == hashlib.sha256(payload).hexdigest()
    assert meta["source_evidence_kind"] == "RAW_HTTP_TABLE"
    assert meta["raw_http_supplied_to_codec"] is True
    assert meta["raw_http_retained"] is False
    assert meta["diagnostic_import"] is None
    assert TOKEN.encode() not in b"".join(pair)
    put_pair(tmp_path, pair)
    loaded = source.load(tmp_path, DAY, CODE)
    event = source.session_evidence(loaded, DAY, CODE)
    assert event["status"] == "FULL_SESSION_SUSPENSION_EVENT"
    for key in ("can_advance_holding_day", "market_absence_verified", "settlement_allowed", "production_activation_allowed"):
        assert event[key] is False
    assert not {"open", "high", "low", "close", "net_return", "return", "settled", "proxy_fill"} & set(event)
    with pytest.raises(TypeError):
        loaded.rows[DAY]["suspend_type"] = "R"
    with pytest.raises(TypeError):
        source.session_evidence(loaded, DAY, CODE, verified_daily_absence=True)


@pytest.mark.parametrize("timing,kind,status", [
    (None, "S", "FULL_SESSION_SUSPENSION_EVENT"),
    ("", "S", "FULL_SESSION_SUSPENSION_EVENT"),
    ("09:30-10:00", "S", "INTRADAY_SUSPENSION_EVENT_NOT_FULL_SESSION"),
    ("09:30-10:00,13:00-14:00", "S", "INTRADAY_SUSPENSION_EVENT_NOT_FULL_SESSION"),
    (None, "R", "RESUMPTION_EVENT_NOT_SUSPENSION"),
])
def test_exact_session_semantics_do_not_imply_daily_absence(tmp_path, timing, kind, status):
    put_pair(tmp_path, encode(response([[CODE, DAY, timing, kind]])))
    event = source.session_evidence(source.load(tmp_path, DAY, CODE), DAY, CODE)
    assert event["status"] == status
    assert event["market_absence_verified"] is event["can_advance_holding_day"] is False


def test_single_start_event_is_not_extended_across_query_range(tmp_path):
    # Response covers the requested interval but has only a prior S record.
    params = {"ts_code": CODE, "start_date": "20260722", "end_date": "20260730"}
    pair = encode(response([[CODE, "20260722", None, "S"]]), request_params=params)
    put_pair(tmp_path, pair)
    assert source.session_evidence(source.load(tmp_path, DAY, CODE), DAY, CODE)["status"] == "EVENT_NOT_PROVEN"


def test_empty_table_or_missing_pair_does_not_prove_trading_or_suspension(tmp_path):
    assert source.load(tmp_path, DAY, CODE) is None
    assert source.session_evidence(None, DAY, CODE)["status"] == "EVENT_NOT_PROVEN"
    put_pair(tmp_path, encode(response([])))
    assert source.session_evidence(source.load(tmp_path, DAY, CODE), DAY, CODE)["status"] == "EVENT_NOT_PROVEN"


@pytest.mark.parametrize("count", [0, 1, None])
def test_provider_zero_sentinel_missing_or_exact_count_are_allowed(count):
    payload = json.loads(response())
    if count is None:
        del payload["data"]["count"]
    else:
        payload["data"]["count"] = count
    encode(json.dumps(payload).encode())


# More than 15 independent adversarial cases; none may become no-event truth.
@pytest.mark.parametrize("update", [
    {"count": 2}, {"count": -1}, {"count": True}, {"count": 0.0},
    {"has_more": True}, {"has_more": 0}, {"has_more": "false"},
    {"fields": ["ts_code", "trade_date", "suspend_type", "suspend_type"]},
    {"items": "not-a-table"}, {"items": [[CODE, DAY, None]]},
    {"items": [[CODE, "20260724", None, "S"]]},
    {"items": [["600234.SH", DAY, None, "S"]]},
    {"items": [[CODE, DAY, None, "UNKNOWN"]]},
    {"items": [[CODE, DAY, False, "S"]]},
    {"items": [[CODE, DAY, " ", "S"]]},
    {"items": [[CODE, DAY, "09:30-99:00", "S"]]},
    {"items": [[CODE, DAY, "14:00-10:00", "S"]]},
    {"items": [[CODE, DAY, "all day", "S"]]},
    {"items": [[CODE, DAY, None, "S"], [CODE, DAY, None, "S"]]},
    {"items": [[CODE, DAY, None, "S"], [CODE, DAY, None, "R"]]},
    {"items": [[CODE, DAY, None, "S"], [CODE, DAY, "09:30-10:00", "S"]]},
    {"unexpected": "field"},
])
def test_malformed_or_ambiguous_source_rejected(update):
    raw = response(update=update)
    before = bytes(raw)
    with pytest.raises(source.SuspensionSourceError):
        encode(raw)
    assert raw == before


@pytest.mark.parametrize("raw", [
    b'{"code":0,"code":0,"data":null}',
    b'{"code":0,"data":NaN}',
    b'{"code":0,"data":Infinity}',
    b'{"code":0,"data":1e9999}',
    response().replace(b'"code": 0', b'"code": 0, "unused": 1e9999'),
    b'{"code":true,"data":null}', b"not json", b"\xff",
    response(envelope={"code": -1, "msg": "permission denied"}),
    response(envelope={"code": -1, "msg": "token invalid"}),
    response(envelope={"code": -1, "msg": "rate limit"}),
])
def test_json_and_api_errors_never_become_absence(raw):
    with pytest.raises(source.SuspensionSourceError):
        encode(raw)


@pytest.mark.parametrize("kwargs", [
    {"network_request_performed": False}, {"network_request_performed": 1},
    {"fetched_at_utc": "2026-07-23T07:59:59+00:00"},
    {"fetched_at_utc": "2026-07-24T16:00:00"},
    {"fetched_at_utc": "2026-07-24T16:00:00+08:00"},
    {"request_params": {"ts_code": CODE, "trade_date": "20260724"}},
    {"request_params": {"ts_code": CODE, "trade_date": DAY, "suspend_type": "S"}},
    {"request_params": {"ts_code": CODE, "start_date": "20260724", "end_date": "20260730"}},
    {"request_params": {"ts_code": CODE, "start_date": "20240723", "end_date": DAY}},
    {"token": None},
])
def test_missing_wrong_incomplete_or_filtered_request_receipt_rejected(kwargs):
    with pytest.raises(source.SuspensionSourceError):
        encode(**kwargs)


@pytest.mark.parametrize("field,value", [
    ("can_advance_holding_day", True), ("market_absence_verified", True),
    ("settlement_allowed", True), ("production_activation_allowed", True),
    ("production_integrated", True), ("source_values_modified", True),
    ("raw_http_retained", True), ("raw_http_supplied_to_codec", False),
    ("source_policy_id", "different"), ("data_sha256", "0" * 64),
    ("rows", 0), ("trade_date", "20260724"), ("ts_code", "600234.SH"),
    ("source_evidence_kind", "DIAGNOSTIC_TABLE_IMPORT"),
    ("diagnostic_import", {"archive_path": "wrong"}),
    ("http_response_sha256", "invalid"), ("http_response_bytes", True),
])
def test_loader_rejects_changed_policy_flags_sha_and_origin(tmp_path, field, value):
    raw, meta_raw = encode()
    meta = json.loads(meta_raw)
    meta[field] = value
    put_pair(tmp_path, (raw, source._json(meta)))
    with pytest.raises(source.SuspensionSourceError):
        source.load(tmp_path, DAY, CODE)


def test_truncated_pair_oversized_source_and_alias_are_not_absence(tmp_path):
    data, meta = put_pair(tmp_path)
    meta.unlink()
    with pytest.raises(source.SuspensionSourceError):
        source.load(tmp_path, DAY, CODE)
    put_pair(tmp_path)
    data.write_bytes(b" " * (source.MAX_BYTES + 1))
    with pytest.raises(source.SuspensionSourceError):
        source.load(tmp_path, DAY, CODE)
    data.unlink()
    data.symlink_to(tmp_path / "absent")
    with pytest.raises(source.SuspensionSourceError):
        source.load(tmp_path, DAY, CODE)


def test_preload_cannot_be_forged_and_cannot_switch_identity(tmp_path):
    with pytest.raises(source.SuspensionSourceError):
        source.LoadedSource(DAY, CODE, {}, "RAW_HTTP_TABLE", [])
    put_pair(tmp_path)
    loaded = source.load(tmp_path, DAY, CODE)
    for day, code in (("20260724", CODE), (DAY, "600234.SH")):
        with pytest.raises(source.SuspensionSourceError):
            source.session_evidence(loaded, day, code)


def test_unpinned_diagnostic_archive_and_http_table_cannot_be_imported():
    for raw in (b"", b"PK fake", response(), encode()[0]):
        with pytest.raises(source.SuspensionSourceError, match="DIAGNOSTIC_ZIP_NOT_PINNED"):
            source.import_diagnostic_bytes(raw)


def test_metadata_cannot_hide_credentials_as_request_input():
    params = {"ts_code": CODE, "trade_date": DAY, "token": TOKEN}
    with pytest.raises(source.SuspensionSourceError):
        encode(request_params=params, token=TOKEN)


@pytest.mark.skipif(not os.environ.get("DC20_SUSPENSION_DIAGNOSTIC_ZIP"),
                    reason="real pinned offline artifact not supplied; no network permitted")
def test_real_pinned_zip_offline_preserves_22_events_and_all_original_flags(tmp_path):
    path = Path(os.environ["DC20_SUSPENSION_DIAGNOSTIC_ZIP"])
    original = path.read_bytes()
    assert hashlib.sha256(original).hexdigest() == source.DIAGNOSTIC_ZIP_SHA
    bundle = source.import_diagnostic_bytes(original)
    assert len(bundle) == 45
    assert bundle[source.ARCHIVE_PATH] == original
    with zipfile.ZipFile(io.BytesIO(original)) as archive:
        old_report = json.loads(archive.read("receipt.json"))
        assert old_report["settlement_performed"] is False
        assert all(case["settlement_allowed"] is False and case["full_day_suspension_automatically_confirmed"] is False
                   for case in old_report["cases"])
        for relative, body in bundle.items():
            target = tmp_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        statuses = []
        for relative, body in bundle.items():
            if not relative.endswith(".meta.json"):
                continue
            meta = json.loads(body)
            day, code = meta["trade_date"], meta["ts_code"]
            loaded = source.load(tmp_path, day, code)
            event = source.session_evidence(loaded, day, code)
            statuses.append(event["status"])
            assert event["can_advance_holding_day"] is event["market_absence_verified"] is False
            assert meta["source_evidence_kind"] == "DIAGNOSTIC_TABLE_IMPORT"
            assert meta["raw_http_supplied_to_codec"] is meta["raw_http_retained"] is False
            data_path, _ = source.source_paths(tmp_path, day, code)
            assert data_path.read_bytes() == archive.read(meta["diagnostic_import"]["data_member"])
            assert len(loaded.source_files) == 3
        assert statuses.count("FULL_SESSION_SUSPENSION_EVENT") == 17
        assert statuses.count("RESUMPTION_EVENT_NOT_SUSPENSION") == 5
    assert path.read_bytes() == original
    damaged = bytearray(original)
    damaged[-1] ^= 1
    with pytest.raises(source.SuspensionSourceError, match="DIAGNOSTIC_ZIP_NOT_PINNED"):
        source.import_diagnostic_bytes(bytes(damaged))
    archive_path = tmp_path / source.ARCHIVE_PATH
    archive_path.write_bytes(bytes(damaged))
    with pytest.raises(source.SuspensionSourceError, match="DIAGNOSTIC_ZIP_NOT_PINNED"):
        source.load(tmp_path, DAY, CODE)


def test_source_module_has_no_market_ledger_or_network_dependencies():
    text = Path(source.__file__).read_text()
    assert "import requests" not in text
    assert "urlopen(" not in text
    assert "import labels" not in text
    assert "resolve_exit_1000(" not in text
    assert "write_bytes(" not in text and 'open("w' not in text
