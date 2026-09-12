import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("research_auction_truth", Path(__file__).with_name("auction_truth.py"))
auction = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = auction
SPEC.loader.exec_module(auction)

DAY, CODE = "20260817", "002724.SZ"
FETCHED = "2026-09-12T10:00:00+00:00"
DAILY = {"path": "data/market/raw/2026/20260817/daily.csv", "sha256": "a" * 64}
# Actual canonical values from read-only probe run 34674921350, artifact
# 10291882093; ZIP SHA 9032bac0de893fec2dee6c6034f59ac4f2a7f46920cd8984cfc50d9f3a945e44.
# This fixture is not an original HTTP envelope and is never imported as truth.
REAL_ROW = [CODE, DAY, 6.98, 5138800, 35868824.0, 6.78]


def table(rows=None):
    return {"fields": list(auction.FIELDS), "items": [list(REAL_ROW)] if rows is None else rows,
            "count": 0, "has_more": False}


def envelope(data=None, *, code=0, msg=""):
    return json.dumps({"code": code, "msg": msg, "data": table() if data is None and code == 0 else data}).encode()


def pair(raw=None, day=DAY, requested_code=None, **kwargs):
    return auction.source_bytes(raw if raw is not None else envelope(), day,
                                request=auction.request_contract(day, requested_code),
                                fetched_at_utc=FETCHED, network_request_performed=True, **kwargs)


class AuctionTruthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def save(self, bodies=None, day=DAY):
        paths = auction.source_paths(self.root, day)
        paths[0].parent.mkdir(parents=True, exist_ok=True)
        for path, raw in zip(paths, pair(day=day) if bodies is None else bodies):
            path.write_bytes(raw)
        return paths

    def result(self, source, day=DAY, code=CODE, opening=6.98):
        return auction.entry_price(source, day, code, opening, daily_source_binding=DAILY)

    def test_real_canonical_units_and_count_zero_are_accepted(self):
        self.save()
        source = auction.load(self.root, DAY)
        result = self.result(source)
        self.assertEqual(result["price"], 6.98)
        self.assertEqual(result["volume"], 5138800)
        self.assertEqual(result["amount"], 35868824)
        self.assertTrue(result["capacity_proxy_verified"])
        self.assertIsNone(result["fallback_reason"])
        self.assertFalse(result["actual_execution_claimed"])
        self.assertFalse(result["actual_capacity_verified"])
        self.assertFalse(result["production_integrated"])
        self.assertEqual(result["source_policy_id"], auction.SOURCE_POLICY_ID)

    def test_original_data_order_types_and_sha_preserved_not_envelope(self):
        data = table()
        data["items"][0][2] = "6.980"
        data["fields"].reverse()
        data["items"][0].reverse()
        raw = envelope(data, msg="opaque_secret_never_persist")
        bodies = pair(raw)
        self.assertEqual(json.loads(bodies[0]), data)
        meta = json.loads(bodies[1])
        self.assertEqual(meta["http_response_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(meta["data_sha256"], hashlib.sha256(bodies[0]).hexdigest())
        self.assertNotIn(b"opaque_secret_never_persist", b"".join(bodies))
        self.save(bodies)
        self.assertEqual(self.result(auction.load(self.root, DAY))["price"], 6.98)

    def test_all_old_auction_o_files_are_ineligible_even_if_prices_agree(self):
        for suffix in ("stk_auction_o.csv", "stk_auction_o.meta.json"):
            path = self.root / "data/market/raw/2026/20260817" / suffix
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("old source price 6.98 amount 999999999")
        with self.assertRaises(auction.AuctionSourceMissing):
            auction.load(self.root, DAY)
        with self.assertRaisesRegex(auction.AuctionSourceError, "ATTEMPT_REQUIRED"):
            self.result(None)
        self.save(pair(envelope(table([]))))
        result = self.result(auction.load(self.root, DAY))
        self.assertEqual(result["entry_price_source"], "DAILY_OPEN_PROXY")
        self.assertIsNone(result["amount"])
        self.assertFalse(result["capacity_proxy_verified"])
        self.assertNotIn("stk_auction_o", json.dumps(result))

    def test_frozen_precoverage_declaration_needs_no_fake_network_request(self):
        with self.assertRaises(auction.AuctionSourceMissing):
            auction.load(self.root, "20221114")
        result = self.result(None, day="20221114", code="002467.SZ", opening=6.02)
        self.assertEqual(result["fallback_reason"], "HISTORY_BEFORE_CANONICAL_COVERAGE")
        self.assertEqual(result["source_files"], [])
        self.assertIsNone(result["amount"])

    def test_complete_empty_response_allows_explicit_daily_proxy_only(self):
        self.save(pair(envelope(table([]))))
        source = auction.load(self.root, DAY)
        self.assertEqual(source.status, "CANONICAL_TABLE_EMPTY")
        result = self.result(source)
        self.assertEqual(result["fallback_reason"], "CANONICAL_ROW_ABSENT_AFTER_VALID_REQUEST")
        self.assertEqual(result["capacity_evidence"], "UNKNOWN")
        self.assertIsNone(result["auction_trade_observed"])

    def test_full_market_missing_code_is_valid_attempt_but_not_capacity_evidence(self):
        data = table()
        data["items"][0][0] = "000001.SZ"
        self.save(pair(envelope(data)))
        result = self.result(auction.load(self.root, DAY))
        self.assertEqual(result["entry_price_source"], "DAILY_OPEN_PROXY")
        self.assertIsNone(result["amount"])

    def test_permission_denied_receipt_sanitized_and_can_fallback(self):
        bodies = pair(envelope(code=-1, msg="没有权限 opaque_secret_never_persist"))
        self.assertNotIn(b"opaque_secret_never_persist", b"".join(bodies))
        self.assertIsNone(json.loads(bodies[0]))
        self.save(bodies)
        result = self.result(auction.load(self.root, DAY))
        self.assertEqual(result["fallback_reason"], "CANONICAL_ENTITLEMENT_DENIED")
        self.assertIsNone(result["amount"])

    def test_operational_or_unknown_errors_are_not_unavailability_receipts(self):
        for message in ("token无效", "每分钟限频", "network error", "没有权限 token无效"):
            with self.subTest(message=message), self.assertRaises(auction.AuctionSourceError):
                pair(envelope(code=-1, msg=message))

    def test_failure_with_nonnull_data_is_conflicting_not_unavailable(self):
        with self.assertRaisesRegex(auction.AuctionSourceError, "FAILURE_WITH_DATA"):
            pair(envelope(table([]), code=-1, msg="没有权限"))

    def test_missing_pair_and_corrupt_json_cannot_downgrade(self):
        paths = self.save()
        paths[1].unlink()
        with self.assertRaisesRegex(auction.AuctionSourceError, "INCOMPLETE_SOURCE_PAIR"):
            auction.load(self.root, DAY)
        paths[1].write_bytes(b"{broken")
        with self.assertRaisesRegex(auction.AuctionSourceError, "INVALID_SOURCE_JSON"):
            auction.load(self.root, DAY)

    def test_present_corrupt_precoverage_source_is_not_treated_as_absent(self):
        paths = auction.source_paths(self.root, "20221114")
        paths[0].parent.mkdir(parents=True)
        paths[0].write_text("broken")
        with self.assertRaises(auction.AuctionSourceError) as caught:
            auction.load(self.root, "20221114")
        self.assertNotIsInstance(caught.exception, auction.AuctionSourceMissing)

    def test_data_or_metadata_sha_tampering_rejected(self):
        for mutation in ("data", "sha", "endpoint", "policy", "volume_unit", "request"):
            with self.subTest(mutation=mutation):
                bodies = list(pair())
                meta = json.loads(bodies[1])
                if mutation == "data":
                    bodies[0] += b" "
                elif mutation == "sha": meta["data_sha256"] = "0" * 64
                elif mutation == "endpoint": meta["source"] = "tushare:stk_auction_o"
                elif mutation == "policy": meta["source_policy_id"] = "old"
                elif mutation == "volume_unit": meta["volume_unit"] = "HANDS"
                elif mutation == "request": meta["network_request_performed"] = False
                bodies[1] = auction._json(meta)
                self.save(bodies)
                with self.assertRaises(auction.AuctionSourceError): auction.load(self.root, DAY)

    def test_wrong_request_date_code_endpoint_fields_and_network_flag_rejected(self):
        for mutation in (lambda r: r.update(api_name="stk_auction_o"),
                         lambda r: r["params"].update(trade_date="20260818"),
                         lambda r: r["params"].update(ts_code="600191.SH"),
                         lambda r: r["fields"].reverse()):
            request = auction.request_contract(DAY)
            mutation(request)
            with self.subTest(request=request), self.assertRaises(auction.AuctionSourceError):
                auction.source_bytes(envelope(), DAY, request=request, fetched_at_utc=FETCHED, network_request_performed=True)
        for observed in (False, 1, None):
            with self.subTest(observed=observed), self.assertRaises(auction.AuctionSourceError):
                auction.source_bytes(envelope(), DAY, request=auction.request_contract(DAY), fetched_at_utc=FETCHED, network_request_performed=observed)

    def test_source_wrong_date_code_or_duplicate_is_rejected(self):
        for mutate, code in ((lambda d: d["items"][0].__setitem__(1, "20260818"), None),
                             (lambda d: d["items"][0].__setitem__(0, "600191.SH"), CODE),
                             (lambda d: d["items"].append(list(d["items"][0])), None)):
            data = table()
            mutate(data)
            with self.assertRaises(auction.AuctionSourceError): pair(envelope(data), requested_code=code)

    def test_positive_count_conflict_and_pagination_rejected_zero_sentinel_kept(self):
        for changes in ({"count": 2}, {"count": -1}, {"count": True}, {"has_more": True}, {"has_more": 0}):
            data = table()
            data.update(changes)
            with self.subTest(changes=changes), self.assertRaises(auction.AuctionSourceError): pair(envelope(data))
        for count in (0, 1):
            data = table(); data["count"] = count
            self.assertEqual(json.loads(pair(envelope(data))[0]), data)

    def test_unknown_fields_duplicate_headers_and_null_numeric_rejected(self):
        for mutate in (lambda d: d["fields"].__setitem__(0, "unknown"),
                       lambda d: d["fields"].__setitem__(0, "trade_date"),
                       lambda d: d["items"][0].__setitem__(2, None),
                       lambda d: d["items"][0].__setitem__(3, True),
                       lambda d: d["items"][0].__setitem__(4, "bad"),
                       lambda d: d["items"][0].__setitem__(5, None)):
            data = table(); mutate(data)
            with self.assertRaises(auction.AuctionSourceError): pair(envelope(data))

    def test_zero_volume_is_observed_no_auction_trade_not_daily_fallback(self):
        for price in (0, 6.98):
            data = table(); data["items"][0][2:5] = [price, 0, 0]
            self.save(pair(envelope(data)))
            result = self.result(auction.load(self.root, DAY))
            self.assertEqual(result["status"], "OBSERVED_NO_AUCTION_TRADE")
            self.assertIsNone(result["price"])
            self.assertEqual(result["amount"], 0)
            self.assertFalse(result["auction_trade_observed"])
            self.assertIsNone(result["fallback_reason"])

    def test_zero_volume_nonzero_amount_and_positive_volume_zero_price_rejected(self):
        for values in ([6.98, 0, 10], [0, 100, 10], [6.98, 100, 0], [6.98, -100, 698]):
            data = table(); data["items"][0][2:5] = values
            with self.assertRaises(auction.AuctionSourceError): pair(envelope(data))

    def test_shares_and_cny_units_are_not_reinterpreted(self):
        for volume, amount in ((51388, 35868824), (5138800, 35868.824), (5138800.5, 35868827.49)):
            data = table(); data["items"][0][3:5] = [volume, amount]
            with self.assertRaises(auction.AuctionSourceError): pair(envelope(data))

    def test_canonical_price_conflict_stays_blocked_not_fallback(self):
        self.save()
        source = auction.load(self.root, DAY)
        with self.assertRaisesRegex(auction.AuctionSourceError, "PRICE_DAILY_OPEN_CONFLICT"):
            self.result(source, opening=6.81)
        self.assertEqual(self.result(source, opening=6.980000000000001)["price"], 6.98)

    def test_targeted_empty_receipt_cannot_cover_another_stock(self):
        self.save(pair(envelope(table([])), requested_code=CODE))
        with self.assertRaisesRegex(auction.AuctionSourceError, "OUTSIDE_RECORDED_REQUEST"):
            self.result(auction.load(self.root, DAY), code="600191.SH")

    def test_full_market_bj_rows_preserved_without_allowing_bj_entry_target(self):
        data = table(); data["items"].append(["832000.BJ", DAY, 10, 100, 1000, 9])
        self.save(pair(envelope(data)))
        self.assertIn("832000.BJ", auction.load(self.root, DAY).rows)
        with self.assertRaises(auction.AuctionSourceError): self.result(auction.load(self.root, DAY), code="832000.BJ")

    def test_datetime_identity_and_coverage_boundary(self):
        for date in ("20260230", "2026-08-17", None):
            with self.assertRaises(auction.AuctionSourceError): auction.source_paths(self.root, date)
        data = table(); data["items"][0][1] = "20241231"
        with self.assertRaisesRegex(auction.AuctionSourceError, "OUTSIDE_DOCUMENTED_COVERAGE"):
            pair(envelope(data), day="20241231")
        with self.assertRaises(auction.AuctionSourceError): self.result(None, day="20250101")

    def test_fetched_timestamp_missing_timezone_or_before_event_rejected(self):
        for stamp in (None, "2026-09-12T10:00:00", "2026-08-17T01:25:59Z"):
            with self.assertRaises(auction.AuctionSourceError):
                auction.source_bytes(envelope(), DAY, request=auction.request_contract(DAY), fetched_at_utc=stamp, network_request_performed=True)

    def test_symlink_root_or_source_not_followed(self):
        paths = self.save()
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(auction.AuctionSourceError): auction.load(alias, DAY)
        paths[0].unlink(); paths[0].symlink_to(paths[1])
        with self.assertRaises(auction.AuctionSourceError): auction.load(self.root, DAY)

    def test_daily_source_binding_required_and_preloaded_source_date_checked(self):
        self.save(); source = auction.load(self.root, DAY)
        with self.assertRaises(auction.AuctionSourceError): self.result(source, day="20260818")
        for binding in ({}, {"path": "../daily.csv", "sha256": "a" * 64}, {"path": "daily.csv", "sha256": "bad"}):
            with self.assertRaises(auction.AuctionSourceError):
                auction.entry_price(source, DAY, CODE, 6.98, daily_source_binding=binding)

    def test_duplicate_json_keys_and_nonfinite_constants_rejected(self):
        for raw in (b'{"code":0,"code":-1,"data":null}',
                    b'{"code":0,"data":NaN}',
                    b'{"code":0,"data":Infinity}',
                    b'{"code":0,"data":{"fields":[],"items":[],"items":[]}}'):
            with self.subTest(raw=raw), self.assertRaises(auction.AuctionSourceError): pair(raw)
        for duplicate in ("data", "meta"):
            paths = self.save()
            index = 0 if duplicate == "data" else 1
            raw = paths[index].read_bytes()
            paths[index].write_bytes(raw.replace(b"{", b'{"extra":null,"extra":null,', 1))
            with self.assertRaises(auction.AuctionSourceError): auction.load(self.root, DAY)

    def test_source_and_metadata_must_retain_canonical_representation(self):
        bodies = list(pair())
        bodies[0] = json.dumps(json.loads(bodies[0])).encode()
        meta = json.loads(bodies[1]); meta["data_sha256"] = hashlib.sha256(bodies[0]).hexdigest()
        self.save((bodies[0], auction._json(meta)))
        with self.assertRaisesRegex(auction.AuctionSourceError, "NONCANONICAL"):
            auction.load(self.root, DAY)

    def test_credential_inside_safe_numeric_data_is_not_persisted(self):
        # Also catch accidental numeric/identity token strings, not only messages.
        with self.assertRaisesRegex(auction.AuctionSourceError, "CREDENTIAL_LIKE"):
            pair(token="35868824.0")
        secret = "opaque_secret_never_persist"
        bodies = pair(envelope(msg=secret), token=secret)
        self.assertNotIn(secret.encode(), b"".join(bodies))

    def test_internal_preload_cannot_be_constructed_or_mutated(self):
        with self.assertRaisesRegex(auction.AuctionSourceError, "CREATED_BY_LOAD"):
            auction.LoadedSource(DAY, None, "CANONICAL_TABLE_EMPTY", {}, ())
        self.save(); loaded = auction.load(self.root, DAY)
        with self.assertRaises((AttributeError, TypeError)):
            loaded.status = "ENTITLEMENT_DENIED"
        with self.assertRaises(TypeError): loaded.rows[CODE]["amount"] = 10
        with self.assertRaises(TypeError): loaded.source_files[0]["sha256"] = "0" * 64

    def test_non_candidate_zero_preclose_preserved_but_candidate_cannot_use_it(self):
        data = table(); data["items"].append(["600000.SH", DAY, 10, 100, 1000, 0])
        self.save(pair(envelope(data)))
        loaded = auction.load(self.root, DAY)
        self.assertEqual(self.result(loaded)["price"], 6.98)
        with self.assertRaisesRegex(auction.AuctionSourceError, "CANDIDATE_PRE_CLOSE_INVALID"):
            self.result(loaded, code="600000.SH", opening=10)


if __name__ == "__main__":
    unittest.main()
