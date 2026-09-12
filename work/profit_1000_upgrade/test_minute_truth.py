import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("research_minute_truth", Path(__file__).with_name("minute_truth.py"))
source = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source)
DAY, CODE = "20260106", "001299.SZ"
FETCHED = "2026-09-12T07:00:00+00:00"
TOKEN = "opaque-test-token-never-persist"


def rows_for(day=DAY, code=CODE):
    return [{"ts_code": code, "trade_time": stamp, "open": 10, "close": 10,
             "high": 10, "low": 10, "vol": 100, "amount": 1000}
            for stamp in source._minute.expected_bar_ends(day)]


def raw_response(rows=None, *, fields=None, extra=None, envelope=None):
    rows = rows_for() if rows is None else rows
    fields = list(fields or source.FIELDS)
    data = {"fields": fields, "items": [[r[k] for k in fields] for r in rows],
            "count": 0, "has_more": False, **(extra or {})}
    return json.dumps({"code": 0, "data": data, **(envelope or {})}).encode()


def encode(payload=None, **kwargs):
    return source.source_bytes(raw_response() if payload is None else payload, DAY, CODE,
                               request_params=source.request_parameters(DAY, CODE),
                               fetched_at_utc=FETCHED, **kwargs)


def put_pair(root, bodies=None):
    pair = source.paths(root, DAY, CODE)
    for path, raw in zip(pair, encode() if bodies is None else bodies):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return pair


class ResearchMinuteTruthTests(unittest.TestCase):
    def test_actual_request_is_exact_0931_not_old_0930(self):
        self.assertEqual(source.request_parameters(DAY, CODE), {"ts_code": CODE, "freq": "1min",
                         "start_date": "2026-01-06 09:31:00", "end_date": "2026-01-06 15:00:00"})
        for update in ({"start_date": "2026-01-06 09:30:00"}, {"end_date": "2026-01-06 14:59:00"},
                       {"ts_code": "600302.SH"}, {"freq": "5min"}, {"limit": 240}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                source.source_bytes(raw_response(), DAY, CODE,
                                    request_params={**source.request_parameters(DAY, CODE), **update}, fetched_at_utc=FETCHED)

    def test_source_preserves_table_values_order_and_original_http_sha(self):
        rows = rows_for()[::-1]
        rows[0]["open"] = "10.000"
        payload = raw_response(rows, fields=tuple(reversed(source.FIELDS)), envelope={"msg": TOKEN, "extra": TOKEN})
        original = copy.deepcopy(json.loads(payload)["data"])
        data_raw, meta_raw = encode(payload, token=TOKEN)
        self.assertEqual(json.loads(data_raw), original)
        meta = json.loads(meta_raw)
        self.assertEqual(meta["response_body_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(meta["data_sha256"], hashlib.sha256(data_raw).hexdigest())
        self.assertEqual(meta["request"]["start_date"], "2026-01-06 09:31:00")
        self.assertEqual(meta["time_semantics"], source.TIME_SEMANTICS)
        for key in ("source_values_modified", "provider_timestamp_semantics_confirmed", "production_activation_allowed", "actual_execution_claimed"):
            self.assertFalse(meta[key])
        self.assertNotIn(TOKEN.encode(), data_raw + meta_raw)

    def test_loader_returns_engine_compatible_rows_with_explicit_assumption(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            pair = put_pair(root)
            result = source.load(root, DAY, CODE)
            self.assertEqual(result["schema_version"], "dc20_exit_1000_minutes_v1")
            self.assertEqual(result["timestamp_semantics"], "BAR_END")
            self.assertEqual(result["time_semantics"], source.TIME_SEMANTICS)
            self.assertEqual([r["bar_end"] for r in result["rows"]], source._minute.expected_bar_ends(DAY))
            self.assertEqual(len(result["rows"]), 240)
            self.assertFalse(result["provider_timestamp_semantics_confirmed"])
            self.assertFalse(result["production_activation_allowed"])
            self.assertTrue(result["research_only"])
            for binding, path in zip(result["source_files"], pair):
                self.assertEqual(binding["path"], path.relative_to(root).as_posix())
                self.assertEqual(binding["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_count_zero_missing_count_and_exact_positive_count_allowed(self):
        for count in (0, 240, None):
            with self.subTest(count=count):
                raw = json.loads(raw_response())
                if count is None:
                    del raw["data"]["count"]
                else:
                    raw["data"]["count"] = count
                encode(json.dumps(raw).encode())

    def test_positive_count_conflict_and_has_more_rejected(self):
        for extra in ({"count": 1}, {"count": 241}, {"count": -1}, {"count": True},
                      {"count": 0.0}, {"has_more": True}, {"has_more": 0}, {"has_more": "false"}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                encode(raw_response(extra=extra))

    def test_0930_return_is_never_dropped_even_if_flat(self):
        for high in (10, 11):
            rows = rows_for()
            rows.insert(0, dict(rows[0], trade_time="2026-01-06 09:30:00", high=high))
            with self.subTest(high=high), self.assertRaises(ValueError):
                encode(raw_response(rows))
        rows = rows_for()
        rows[0]["trade_time"] = "2026-01-06 09:30:00"
        with self.assertRaises(ValueError):
            encode(raw_response(rows))

    def test_missing_duplicated_extra_and_lunch_rows_never_repaired(self):
        cases = []
        rows = rows_for(); rows.pop(30); cases.append(rows)
        rows = rows_for(); rows[31] = dict(rows[30]); cases.append(rows)
        rows = rows_for(); rows.append(dict(rows[-1])); cases.append(rows)
        rows = rows_for(); rows[120]["trade_time"] = "2026-01-06 13:00:00"; cases.append(rows)
        for rows in cases:
            original = copy.deepcopy(rows)
            with self.subTest(length=len(rows)), self.assertRaises(ValueError):
                encode(raw_response(rows))
            self.assertEqual(rows, original)

    def test_wrong_code_date_and_identity_parameters_rejected(self):
        for update in ({"ts_code": "600302.SH"}, {"trade_time": "2026-01-05 09:31:00"}):
            rows = rows_for(); rows[0].update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                encode(raw_response(rows))
        for day, code in (("20260230", CODE), ("../20260106", CODE), (DAY, "../../bad"), (DAY, "001299.BJ")):
            with self.subTest(day=day, code=code), self.assertRaises(ValueError):
                source.request_parameters(day, code)

    def test_existing_zero_volume_price_and_numeric_constraints_preserved(self):
        for update in ({"close": 0}, {"close": True}, {"close": "not-number"}, {"close": 12},
                       {"vol": -1}, {"vol": 0, "high": 11},
                       {"open": 11, "high": 11, "low": 11, "close": 11, "vol": 0, "amount": 0}):
            rows = rows_for(); rows[1].update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                encode(raw_response(rows))

    def test_nonzero_api_code_or_nonbytes_cannot_be_truth(self):
        for payload in (b'{"code":1,"msg":"not entitled"}', b'{"code":false,"data":{}}',
                        b"{}", b"invalid", {}, bytearray(raw_response()), b"x" * (source.MAX_RESPONSE_BYTES + 1)):
            with self.subTest(kind=type(payload).__name__), self.assertRaises(ValueError):
                encode(payload)

    def test_malformed_fields_rows_table_and_nonfinite_json_rejected(self):
        changes = []
        raw = json.loads(raw_response()); raw["data"]["fields"][0] = "other"; changes.append(raw)
        raw = json.loads(raw_response()); raw["data"]["fields"][0] = raw["data"]["fields"][1]; changes.append(raw)
        raw = json.loads(raw_response()); raw["data"]["items"][0].pop(); changes.append(raw)
        raw = json.loads(raw_response()); raw["data"]["items"][0][2] = float("nan"); changes.append(raw)
        raw = json.loads(raw_response()); raw["data"]["unexpected"] = "extra"; changes.append(raw)
        for raw in changes:
            with self.subTest(raw=str(raw)[:40]), self.assertRaises(ValueError):
                encode(json.dumps(raw).encode())
        payload = raw_response().replace(b'"code": 0', b'"code": 0, "code": 0')
        with self.assertRaises(ValueError):
            encode(payload)

    def test_credential_guard_checks_even_valid_numeric_looking_echo(self):
        numeric_token = "12345678901234567890"
        rows = rows_for()
        rows[0]["amount"] = numeric_token
        with self.assertRaisesRegex(ValueError, "credential-like"):
            encode(raw_response(rows), token=numeric_token)

    def test_utc_timestamp_required(self):
        for fetched in (None, "bad-date", "2026-09-12T07:00:00", "2026-09-12T15:00:00+08:00"):
            with self.subTest(fetched=fetched), self.assertRaises(ValueError):
                source.source_bytes(raw_response(), DAY, CODE, request_params=source.request_parameters(DAY, CODE), fetched_at_utc=fetched)

    def test_complete_grid_cannot_be_fetched_before_1500_shanghai(self):
        for fetched in ("2026-01-05T23:59:59+00:00", "2026-01-06T06:59:59Z", "2026-01-06T00:00:00+00:00"):
            with self.subTest(fetched=fetched), self.assertRaisesRegex(ValueError, "before session close"):
                source.source_bytes(raw_response(), DAY, CODE, request_params=source.request_parameters(DAY, CODE), fetched_at_utc=fetched)
            with self.subTest(load_fetched=fetched), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve()
                data, meta = encode(); obj = json.loads(meta); obj["fetched_at_utc"] = fetched
                put_pair(root, (data, source._json(obj)))
                with self.assertRaisesRegex(ValueError, "before session close"):
                    source.load(root, DAY, CODE)
        for fetched in ("2026-01-06T07:00:00Z", "2026-01-06T07:00:01+00:00"):
            with self.subTest(fetched=fetched):
                source.source_bytes(raw_response(), DAY, CODE, request_params=source.request_parameters(DAY, CODE), fetched_at_utc=fetched)

    def test_absent_pair_returns_none_but_either_missing_half_raises(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            self.assertIsNone(source.load(root, DAY, CODE))
            data_path, meta_path = source.paths(root, DAY, CODE)
            data_path.parent.mkdir(parents=True)
            data_path.write_bytes(encode()[0])
            with self.assertRaises(ValueError):
                source.load(root, DAY, CODE)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            _, meta_path = source.paths(root, DAY, CODE)
            meta_path.parent.mkdir(parents=True)
            meta_path.write_bytes(encode()[1])
            with self.assertRaises(ValueError):
                source.load(root, DAY, CODE)

    def test_corrupt_source_sha_and_wrong_metadata_fail_closed(self):
        for update in ({"endpoint": "daily"}, {"source": "unknown"}, {"trade_date": "20260105"},
                       {"ts_code": "600302.SH"}, {"data_sha256": "0" * 64}, {"immutable": False},
                       {"time_semantics": "PROVIDER_CONFIRMED"}, {"provider_timestamp_semantics_confirmed": True},
                       {"production_activation_allowed": True}, {"continuous_rows": 240.0},
                       {"normalizer_sha256": "0" * 64}, {"response_body_sha256": "invalid"},
                       {"request": source._minute.request_parameters(DAY, CODE)}):
            with self.subTest(update=update), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve()
                data, meta = encode(); obj = json.loads(meta); obj.update(update)
                put_pair(root, (data, source._json(obj)))
                with self.assertRaises(ValueError):
                    source.load(root, DAY, CODE)

    def test_changed_bytes_noncanonical_and_duplicate_metadata_rejected(self):
        for mode in ("sha", "noncanonical", "duplicate"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve()
                data, meta = encode()
                if mode == "sha":
                    data = data.replace(b"1000", b"1001", 1)
                elif mode == "noncanonical":
                    data = data + b" "
                    obj = json.loads(meta); obj["data_sha256"] = hashlib.sha256(data).hexdigest(); meta = source._json(obj)
                else:
                    meta = meta.replace(b'"immutable": true', b'"immutable": true, "immutable": true')
                put_pair(root, (data, meta))
                with self.assertRaises(ValueError):
                    source.load(root, DAY, CODE)

    def test_raw_source_and_metadata_extra_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            data, meta = encode(); obj = json.loads(meta); obj["extra"] = "not allowed"
            put_pair(root, (data, source._json(obj)))
            with self.assertRaises(ValueError):
                source.load(root, DAY, CODE)

    def test_symlink_root_ancestor_source_and_directory_targets_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            real = base / "real"; real.mkdir()
            pair = put_pair(real)
            alias = base / "alias"; alias.symlink_to(real, target_is_directory=True)
            with self.assertRaises(ValueError):
                source.load(alias, DAY, CODE)
            nested = base / "nested"; nested.mkdir()
            (nested / "research_inputs").symlink_to(real / "research_inputs", target_is_directory=True)
            with self.assertRaises(ValueError):
                source.load(nested, DAY, CODE)
            other = base / "other"; other.mkdir()
            data_path, meta_path = source.paths(other, DAY, CODE)
            data_path.parent.mkdir(parents=True)
            data_path.symlink_to(pair[0])
            with self.assertRaises(ValueError):
                source.load(other, DAY, CODE)
            directories = base / "directories"; directories.mkdir()
            source.paths(directories, DAY, CODE)[0].mkdir(parents=True)
            with self.assertRaises(ValueError):
                source.load(directories, DAY, CODE)

    def test_production_minute_directory_is_not_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            raw, meta = source._minute.source_bytes(rows_for(), DAY, CODE, fetched_at_utc=FETCHED)
            for path, body in zip(source._minute.minute_paths(root, DAY, CODE), (raw, meta)):
                path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(body)
            self.assertIsNone(source.load(root, DAY, CODE))

    def test_normalizer_sha_change_blocks_source_and_loader(self):
        original = Path.read_bytes
        def changed(path):
            return original(path) + b"# changed" if path == source.NORMALIZER_PATH else original(path)
        with mock.patch.object(Path, "read_bytes", changed), self.assertRaises(ValueError):
            encode()

    def test_resolve_exit_engine_accepts_shape_for_conditional_research_only(self):
        spec = importlib.util.spec_from_file_location("minute_truth_test_engine", source.NORMALIZER_PATH.with_name("shadow_exit_1000.py"))
        engine = importlib.util.module_from_spec(spec); spec.loader.exec_module(engine)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); put_pair(root)
            daily = {"trade_date": DAY, "ts_code": CODE, "open": 10, "close": 10,
                     "high": 10, "low": 10, "pre_close": 10, "vol": 1000}
            limits = {"trade_date": DAY, "ts_code": CODE, "up_limit": 11, "down_limit": 9, "pre_close": 10}
            result, status = engine.resolve_exit_1000(["20260105", DAY], DAY, DAY, CODE, 10, 10,
                                                     lambda day: daily, lambda day: limits,
                                                     lambda day: source.load(root, day, CODE))
            self.assertEqual(status, "SETTLED_EXIT_1000_MINUTE_PROXY")
            self.assertIsNotNone(result)
            self.assertFalse(source.load(root, DAY, CODE)["production_activation_allowed"])


if __name__ == "__main__":
    unittest.main()
