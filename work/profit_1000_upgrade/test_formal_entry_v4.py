"""Synthetic preparation fixtures only; never installed as market truth."""
from __future__ import annotations

import copy
import ast
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from work.profit_1000_upgrade import auction_truth_v3 as auction_truth, formal_entry_v4 as entry, policy_v3

BASE = Path(__file__).resolve().parents[2]
D, T, T1 = "20260914", "20260915", "20260916"
DATES = ["20260911", D, T, T1]
CODES = ["002724.SZ", "000001.SZ"]
RANKS = [{"ts_code": code, "stage_transition": "2->3" if rank == 1 else "3->4",
          "executable_profit_research_rank": rank, "research_joint_proxy_score": -rank,
          "shadow_max_price": 1.0} for rank, code in enumerate(CODES, 1)]

# These are synthetic market-boundary cases. Limits use native float addition,
# and nextafter checks the first representable value outside that exact bound.
MARKET_BOUNDARIES = {
    "upper_within_epsilon": ({"high": 7.460000001}, {}, False, 1),
    "upper_exact_epsilon": ({"high": 7.46 + 1e-8}, {}, False, 1),
    "upper_beyond_epsilon": ({"high": math.nextafter(7.46 + 1e-8, math.inf)}, {}, False, None),
    "lower_within_epsilon": ({"low": 6.1 - 1e-9}, {}, False, 1),
    "lower_exact_epsilon": ({"low": 6.1 - 1e-8}, {}, False, 1),
    "lower_beyond_epsilon": ({"low": math.nextafter(6.1 - 1e-8, -math.inf)}, {}, False, None),
    "both_within_epsilon": ({"high": 7.460000001, "low": 6.1 - 1e-9}, {}, False, 1),
    "low_above_open_tiny": ({"low": 6.98 + 1e-9}, {}, False, None),
    "high_below_close_tiny": ({"high": 7 - 1e-9}, {}, False, None),
    "equal_limits": ({}, {"down_limit": 7.46}, False, None),
    "reversed_limits": ({}, {"down_limit": 8}, False, None),
    "negative_daily_volume": ({"vol": -1}, {}, False, None),
    "zero_flat_at_cent": ({"vol": 0, "open": 6.98, "high": 6.984, "low": 6.976, "close": 6.98}, {}, True, 0),
    "zero_not_flat_at_cent": ({"vol": 0, "open": 6.98, "high": 6.985, "low": 6.976, "close": 6.98}, {}, True, None),
    "zero_flat_upper_epsilon": ({"vol": 0, "open": 7.46, "high": 7.46 + 1e-8, "low": 7.46, "close": 7.46}, {}, True, 0),
    "zero_flat_upper_beyond": ({"vol": 0, "open": 7.46, "high": math.nextafter(7.46 + 1e-8, math.inf), "low": 7.46, "close": 7.46}, {}, True, None),
    "zero_flat_positive_auction": ({"vol": 0, "open": 6.98, "high": 6.98, "low": 6.98, "close": 6.98}, {}, False, None),
}


class FormalEntryPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.daily = [{"ts_code": code, "trade_date": T, "open": 6.98, "high": 7.2,
                       "low": 6.5, "close": 7, "pre_close": 6.78, "vol": 100000}
                      for code in CODES]
        self.limits = [{"ts_code": code, "trade_date": T, "up_limit": 7.46,
                        "down_limit": 6.1} for code in CODES]
        self.canonical = [[code, T, 6.98, 2000000, 13960000, 6.78] for code in CODES]
        self.write_market()
        self.write_canonical()

    def binding(self, path):
        return {"path": path.relative_to(self.root).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def csv(self, name, rows):
        path = self.root / f"data/market/raw/2026/{T}/{name}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        path.write_text(buffer.getvalue())
        return self.binding(path)

    def write_market(self):
        self.daily_binding = self.csv("daily", self.daily)
        self.limit_binding = self.csv("stk_limit", self.limits)

    def write_canonical(self, *, rows=None, denied=False, requested_code=None):
        table = {"fields": list(auction_truth.FIELDS),
                 "items": self.canonical if rows is None else rows, "count": 0, "has_more": False}
        raw = json.dumps({"code": -1 if denied else 0, "msg": "权限不足" if denied else "",
                          "data": None if denied else table}).encode()
        pair = auction_truth.source_bytes(raw, T, request=auction_truth.request_contract(T, requested_code),
                                          fetched_at_utc="2026-09-15T08:00:00Z",
                                          network_request_performed=True)
        self.canonical_paths = auction_truth.source_paths(self.root, T)
        self.canonical_paths[0].parent.mkdir(parents=True, exist_ok=True)
        for path, body in zip(self.canonical_paths, pair):
            path.write_bytes(body)

    def preview(self, **kwargs):
        values = dict(signal_date=D, exec_date=T, exit_date=T1, open_dates=DATES,
                      ranked_rows=copy.deepcopy(RANKS), as_of_utc="2026-09-15T08:01:00Z",
                      daily_binding=self.daily_binding, limits_binding=self.limit_binding)
        values.update(kwargs)
        return entry.preview_top2(self.root, **values)

    def assert_pending(self, row):
        self.assertTrue(row["status"].startswith("PENDING_"), row)
        self.assertIsNone(row["proxy_fill"])
        self.assertIsNone(row["net_return"])
        self.assertIsNone(row["slot_net_return"])
        self.assertFalse(row["terminal_settlement"])
        self.assertFalse(row["production_ledger_eligible"])

    def test_valid_canonical_with_negative_scores_and_price_above_old_cap_keeps_both(self):
        out = self.preview()
        self.assertEqual([row["shadow_slot"] for row in out["rows"]], [1, 2])
        for row in out["rows"]:
            self.assertEqual(row["proxy_fill"], 1)
            self.assertEqual(row["entry_price"], 6.98)
            self.assertEqual(row["entry_price_source"], "TUSHARE_STK_AUCTION")
            self.assertTrue(row["capacity_proxy_verified"])
            self.assertFalse(row["actual_capacity_verified"])
            self.assertIsNone(row["slot_net_return"])
            self.assertFalse(row["terminal_settlement"])
        self.assertFalse(out["production_integrated"])
        self.assertFalse(out["production_activation_allowed"])
        self.assertFalse(out["natural_freeze_verified"])
        self.assertEqual(out["exit_policy_id"], "dc20_exit_1000_limit_hold_20260912_v1")
        self.assertEqual(out["round_trip_cost_rate"], 0.0045)

    def test_empty_or_denied_valid_receipt_falls_back_without_fake_capacity(self):
        for denied in (False, True):
            with self.subTest(denied=denied):
                self.write_canonical(rows=[], denied=denied)
                for row in self.preview()["rows"]:
                    self.assertEqual(row["proxy_fill"], 1)
                    self.assertEqual(row["entry_price_source"], "DAILY_OPEN_PROXY")
                    self.assertIsNone(row["entry_price_evidence"]["amount"])
                    self.assertFalse(row["capacity_proxy_verified"])

    def test_absent_receipt_never_falls_back_and_old_o_fields_are_not_read(self):
        for path in self.canonical_paths:
            path.unlink()
        old = self.root / f"data/market/raw/2026/{T}/stk_auction_o.csv"
        old.write_text("ts_code,trade_date,open,close,amount\n002724.SZ,20260915,6.98,6.98,99999999999\n")
        old.with_suffix(".meta.json").write_text("INVALID BUT IRRELEVANT OLD META")
        for row in self.preview()["rows"]:
            self.assertEqual(row["status"], "PENDING_CANONICAL_ATTEMPT_REQUIRED")
            self.assert_pending(row)
        self.write_canonical(rows=[])
        out = self.preview()
        self.assertTrue(all(row["proxy_fill"] == 1 for row in out["rows"]))
        self.assertNotIn("stk_auction_o", json.dumps(out))
        self.assertTrue(all(row["entry_price_evidence"]["amount"] is None for row in out["rows"]))

    def test_conflict_is_pending_not_no_fill_zero_and_other_slot_survives(self):
        self.canonical[0][2] = 6.81
        self.canonical[0][4] = 13620000
        self.write_canonical()
        rows = self.preview()["rows"]
        self.assertEqual(rows[0]["status"], "PENDING_ENTRY_PRICE_DAILY_OPEN_CONFLICT")
        self.assert_pending(rows[0])
        self.assertEqual(rows[1]["proxy_fill"], 1)

    def test_daily_zero_volume_conflicting_positive_auction_is_pending(self):
        self.daily[0]["vol"] = 0
        self.write_market()
        self.assert_pending(self.preview()["rows"][0])

    def test_canonical_observed_zero_is_known_no_auction_trade(self):
        self.canonical[0][2:5] = [None, 0, 0]
        self.write_canonical()
        row = self.preview()["rows"][0]
        self.assertEqual(row["status"], "NO_FILL_CANONICAL_AUCTION_ZERO_VOLUME")
        self.assertEqual(row["proxy_fill"], 0)
        self.assertEqual(row["slot_net_return"], 0)
        self.assertIsNone(row["net_return"])
        self.assertFalse(row["terminal_settlement"])

    def test_canonical_insufficient_capacity_is_proxy_no_fill_not_actual_claim(self):
        self.canonical[0][3:5] = [100, 698]
        self.write_canonical()
        row = self.preview()["rows"][0]
        self.assertEqual(row["status"], "NO_FILL_CAPACITY")
        self.assertEqual(row["slot_net_return"], 0)
        self.assertFalse(row["actual_capacity_verified"])

    def test_capacity_boundary_is_exact_one_percent(self):
        for amount, expected in ((9999999.9, 0), (10000000, 1)):
            # Price=5, share volume integral and amount are exact official units.
            with self.subTest(amount=amount):
                self.daily[0].update(open=5, low=4.8, high=5.2, close=5, pre_close=5)
                self.limits[0].update(up_limit=5.5, down_limit=4.5)
                self.write_market()
                self.canonical[0][2:6] = [5, 2000000, amount, 5]
                if expected == 0:
                    self.canonical[0][3:5] = [1999999, 9999995]
                self.write_canonical()
                self.assertEqual(self.preview()["rows"][0]["proxy_fill"], expected)

    def test_opening_limit_up_remains_explicit_proxy_conservative_no_fill(self):
        self.daily[0].update(open=7.46, high=7.46)
        self.canonical[0][2] = 7.46
        self.canonical[0][4] = 14920000
        self.write_market()
        self.write_canonical()
        row = self.preview()["rows"][0]
        self.assertEqual(row["status"], "NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED")
        self.assertEqual(row["slot_net_return"], 0)
        self.assertFalse(row["production_ledger_eligible"])

    def test_valid_missing_canonical_plus_zero_daily_volume_has_suspension_proxy(self):
        self.write_canonical(rows=[])
        self.daily[0].update(vol=0, open=6.98, high=6.98, low=6.98, close=6.98)
        self.write_market()
        row = self.preview()["rows"][0]
        self.assertEqual(row["status"], "NO_FILL_SUSPENDED")
        self.assertEqual(row["proxy_fill"], 0)

    def test_invalid_pair_or_metadata_or_wrong_endpoint_retains_two_pending_slots(self):
        original = [path.read_bytes() for path in self.canonical_paths]
        for kind in ("sha", "metadata", "endpoint", "orphan", "alias"):
            with self.subTest(kind=kind):
                for path, raw in zip(self.canonical_paths, original):
                    if path.is_symlink():
                        path.unlink()
                    path.write_bytes(raw)
                if kind == "sha":
                    self.canonical_paths[0].write_bytes(original[0] + b" ")
                elif kind == "metadata":
                    self.canonical_paths[1].write_text("{invalid}")
                elif kind == "endpoint":
                    self.canonical_paths[1].write_bytes(original[1].replace(b'tushare:stk_auction"', b'tushare:stk_auction_o"'))
                elif kind == "orphan":
                    self.canonical_paths[0].unlink()
                else:
                    self.canonical_paths[0].unlink()
                    self.canonical_paths[0].symlink_to(self.root / self.daily_binding["path"])
                for row in self.preview()["rows"]:
                    self.assert_pending(row)

    def test_wrong_code_receipt_does_not_cover_unrequested_second_slot(self):
        self.write_canonical(rows=[self.canonical[0]], requested_code=CODES[0])
        rows = self.preview()["rows"]
        self.assertEqual(rows[0]["proxy_fill"], 1)
        self.assert_pending(rows[1])

    def test_missing_or_invalid_market_row_cannot_be_zero(self):
        for kind in ("missing", "ohlc", "volume", "limits", "nan"):
            with self.subTest(kind=kind):
                daily, limits = copy.deepcopy(self.daily), copy.deepcopy(self.limits)
                if kind == "missing":
                    self.daily = daily[1:]
                elif kind == "ohlc":
                    self.daily[0]["high"] = 6
                elif kind == "volume":
                    self.daily[0]["vol"] = -1
                elif kind == "limits":
                    self.limits[0]["down_limit"] = 9
                else:
                    self.daily[0]["open"] = "nan"
                self.write_market()
                self.assert_pending(self.preview()["rows"][0])
                self.daily, self.limits = daily, limits

    def test_missing_or_changed_binding_blocks_all_affected_rows(self):
        for binding in (None, {**self.daily_binding, "sha256": "f" * 64},
                        {**self.daily_binding, "path": "../outside.csv"}):
            with self.subTest(binding=binding):
                for row in self.preview(daily_binding=binding)["rows"]:
                    self.assert_pending(row)

    def test_asof_before_t_close_reads_no_future_sources(self):
        with patch.object(auction_truth, "load", side_effect=AssertionError("future read")):
            out = self.preview(as_of_utc="2026-09-15T06:59:59Z")
        self.assertEqual(out["source_files"], [])
        for row in out["rows"]:
            self.assert_pending(row)

    def test_zero_or_one_candidate_keeps_explicit_two_slots_without_fabrication(self):
        for ranks in ([], RANKS[:1]):
            with self.subTest(count=len(ranks)):
                rows = self.preview(ranked_rows=ranks)["rows"]
                self.assertEqual([row["shadow_slot"] for row in rows], [1, 2])
                for row in rows[len(ranks):]:
                    self.assertEqual(row["status"], "NO_CANDIDATE_SLOT")
                    self.assertIsNone(row["ts_code"])
                    self.assertIsNone(row["slot_net_return"])

    def test_selection_rank_duplicates_non_target_stage_and_calendar_drift_fail(self):
        for kind in ("rank", "bool_rank", "duplicate", "stage", "code", "dates"):
            with self.subTest(kind=kind):
                ranks = copy.deepcopy(RANKS)
                kwargs = {"ranked_rows": ranks}
                if kind == "rank":
                    ranks[0]["executable_profit_research_rank"] = 2
                elif kind == "bool_rank":
                    ranks[0]["executable_profit_research_rank"] = True
                elif kind == "duplicate":
                    ranks[1]["ts_code"] = ranks[0]["ts_code"]
                elif kind == "stage":
                    ranks[0]["stage_transition"] = "4->5"
                elif kind == "code":
                    ranks[0]["ts_code"] = "invalid"
                else:
                    kwargs["exec_date"] = T1
                with self.assertRaises(entry.PreparationError):
                    self.preview(**kwargs)

    def test_read_only_preview_never_loads_or_changes_old_ledger(self):
        old = self.root / "data/decision_executable_profit/forward/settlements/settlement_20260910.json"
        old.parent.mkdir(parents=True)
        old.write_text("malformed legacy must not even be parsed")
        before = {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.root.rglob("*") if p.is_file()}
        self.preview()
        after = {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_mid_preview_source_change_revokes_no_fill_zero(self):
        self.canonical[0][3:5] = [100, 698]
        self.write_canonical()
        original = auction_truth.entry_price
        calls = 0
        def mutate(*args, **kwargs):
            nonlocal calls
            result = original(*args, **kwargs)
            calls += 1
            if calls == 2:
                self.canonical_paths[0].write_bytes(b"corrupt during preview")
            return result
        with patch.object(auction_truth, "entry_price", side_effect=mutate):
            for row in self.preview()["rows"]:
                self.assertEqual(row["status"], "PENDING_SOURCE_CHANGED_DURING_PREVIEW")
                self.assert_pending(row)

    def test_dormant_policy_cannot_be_activated_by_editing_flag_or_economics(self):
        raw = entry.POLICY_PATH.read_bytes()
        for key, value in (("production_integrated", True), ("status", "ACTIVE"),
                           ("effective_signal_date", D), ("round_trip_cost_rate", 0),
                           ("frozen_cap_required", 0), ("shadow_notional_cny", 200000)):
            with self.subTest(key=key):
                policy = json.loads(raw)
                policy[key] = value
                path = self.root / "altered_policy.json"
                path.write_text(json.dumps(policy))
                with patch.object(entry, "POLICY_PATH", path), self.assertRaises(entry.PreparationError):
                    entry.policy_binding()

    def test_activation_check_preserves_existing_dates_and_never_grants_activation(self):
        for day in ("20260910", "20260911", D):
            with self.subTest(day=day):
                result = entry.activation_check(signal_date=day, frozen_at_utc="2026-09-14T12:00:00Z",
                    existing_signal_dates=["20260910", "20260911"],
                    proposed_effective_signal_date=D, proposed_activation_at_utc="2026-09-12T12:00:00Z")
                self.assertFalse(result["can_activate"])
                self.assertIsNone(result["effective_signal_date"])
                if day != D:
                    self.assertIn("EXISTING_FROZEN_D_MUST_KEEP_ORIGINAL_POLICY", result["blockers"])
                self.assertIn("SOURCE_FREEZE_SUCCESSOR_REVIEW_NOT_INSTALLED", result["blockers"])

    def test_cutover_cannot_be_retroactive_or_same_day_after_rank_seen(self):
        result = entry.activation_check(signal_date=D, frozen_at_utc="2026-09-14T12:00:00Z",
            proposed_effective_signal_date=D, proposed_activation_at_utc="2026-09-14T13:00:00Z")
        self.assertIn("RETROSPECTIVE_OR_PRE_ACTIVATION_D_FORBIDDEN", result["blockers"])
        self.assertFalse(result["can_activate"])

    def test_policy_economics_agree_with_research_and_keep_existing_exit_definition(self):
        from work.profit_1000_upgrade import labels_v3 as labels
        from top10decision.decision import executable_profit_shadow_settlement as settlement
        from top10decision.decision.shadow_exit_1000 import EXIT_POLICY_ID
        self.assertEqual(float(entry.COST_RATE), labels.COST_RATE)
        self.assertEqual(float(entry.NOTIONAL), settlement.SHADOW_NOTIONAL_CNY)
        self.assertEqual(float(entry.PARTICIPATION), settlement.MAX_AUCTION_PARTICIPATION)
        self.assertEqual(entry.EXIT_POLICY_ID, EXIT_POLICY_ID)
        policy = json.loads(entry.POLICY_PATH.read_bytes())
        self.assertEqual(policy["research_economic_reference"], policy_v3.ENTRY_POLICY_ID)
        old_exit = json.loads((BASE / "models/decision_shadow_exit_policy_1000_v1.json").read_bytes())
        for key in ("policy_id", "ordinary_exit_decision_time", "limit_up_hold"):
            self.assertEqual(old_exit[key], policy["exit_policy_id" if key == "policy_id" else key])
        self.assertFalse(policy["provider_minute_timestamp_semantics_confirmed"])

    def test_native_research_v3_entry_paths_match_preparation_not_policy_identity(self):
        from work.profit_1000_upgrade import labels_v3 as labels
        calendar = self.root / "data/market/trade_cal_sse.csv"
        calendar.parent.mkdir(parents=True, exist_ok=True)
        # The existing pinned calendar is not an outcome source. All market
        # fixtures remain synthetic, with no actual future outcome file reads.
        shutil.copyfile(BASE / "data/market/trade_cal_sse.csv", calendar)
        features = self.root / "fixture_features.json"
        features.write_text('{"synthetic_test_only":true}')
        manifest = {"schema_version": labels.SCHEMA, "evidence_kind": "RETROSPECTIVE_D_ONLY_RECONSTRUCTION",
            "feature_columns": ["promotion_probability"], "source_bindings": [self.binding(features)],
            "rows": [{"signal_date": D, "ts_code": code, "stage": index + 2, "promotion_rank": index + 1,
                      "feature_as_of_date": D, "feature_available_at": "2026-09-14T23:00:00+08:00",
                      "features": {"promotion_probability": 0.6}, "shadow_max_price": None}
                     for index, code in enumerate(CODES)],
            "expected_candidate_codes": {D: CODES}, **policy_v3.source_policy_contract()}
        original_daily, original_auction = copy.deepcopy(self.daily), copy.deepcopy(self.canonical)
        original_limits = copy.deepcopy(self.limits)
        for kind in ("filled", "empty", "denied", "zero", "capacity", "conflict", "suspended",
                     "amount_mismatch", "amount_none", "amount_zero", "amount_negative",
                     "zero_conflict", "nonflat_suspended", "reported_string", "bad_volume", "cent_precision",
                     "capacity_below", "capacity_at", "capacity_near_epsilon") + tuple(MARKET_BOUNDARIES):
            with self.subTest(kind=kind):
                self.daily, self.canonical = copy.deepcopy(original_daily), copy.deepcopy(original_auction)
                self.limits = copy.deepcopy(original_limits)
                if kind in MARKET_BOUNDARIES:
                    daily_changes, limit_changes, _, _ = MARKET_BOUNDARIES[kind]
                    self.daily[0].update(daily_changes)
                    self.limits[0].update(limit_changes)
                elif kind == "zero":
                    self.canonical[0][2:5] = [None, 0, 0]
                elif kind == "capacity":
                    self.canonical[0][3:5] = [100, 698]
                elif kind == "conflict":
                    self.canonical[0][2], self.canonical[0][4] = 6.81, 13620000
                elif kind == "suspended":
                    self.daily[0].update(vol=0, open=6.98, high=6.98, low=6.98, close=6.98)
                elif kind == "amount_mismatch":
                    self.canonical[0][4] = 698
                elif kind == "amount_none":
                    self.canonical[0][4] = None
                elif kind == "amount_zero":
                    self.canonical[0][4] = 0
                elif kind == "amount_negative":
                    self.canonical[0][4] = -1
                elif kind == "zero_conflict":
                    self.canonical[0][3:5] = [0, 0]
                elif kind == "nonflat_suspended":
                    self.daily[0]["vol"] = 0
                elif kind == "reported_string":
                    self.canonical[0][2] = "6.9800"
                elif kind == "bad_volume":
                    self.canonical[0][3] = 1.5
                elif kind == "cent_precision":
                    self.canonical[0][2] = 6.984
                    self.canonical[0][4] = 13968000
                elif kind in ("capacity_below", "capacity_at", "capacity_near_epsilon"):
                    self.daily[0].update(open=5, low=4.8, high=5.2, close=5, pre_close=5)
                    self.limits[0].update(up_limit=5.5, down_limit=4.5)
                    self.canonical[0][2:6] = [5, 2000000, 10000000, 5]
                    if kind == "capacity_below":
                        self.canonical[0][3:5] = [1999999, 9999995]
                    elif kind == "capacity_near_epsilon":
                        self.canonical[0][4] = 9999999.99999995
                self.write_market()
                empty_source = (kind in ("empty", "denied", "suspended", "nonflat_suspended")
                                or kind in MARKET_BOUNDARIES and MARKET_BOUNDARIES[kind][2])
                self.write_canonical(rows=[] if empty_source else None,
                                     denied=kind == "denied")
                prepared = self.preview()["rows"]
                researched = labels.build_labels(self.root, manifest, as_of_date=T)["rows"]
                if kind in MARKET_BOUNDARIES:
                    expected_fill = MARKET_BOUNDARIES[kind][3]
                    self.assertEqual(prepared[0]["proxy_fill"], expected_fill, kind)
                    self.assertEqual(researched[0]["proxy_fill"], expected_fill, kind)
                    self.assertEqual(prepared[1]["proxy_fill"], 1, kind)
                    self.assertEqual(researched[1]["proxy_fill"], 1, kind)
                    if expected_fill is None:
                        self.assert_pending(prepared[0])
                    elif expected_fill == 1:
                        self.assertEqual(prepared[0]["entry_price"], 6.98, kind)
                for preview, research in zip(prepared, researched):
                    for key in ("entry_price", "entry_price_source", "proxy_fill", "slot_net_return", "capacity_proxy_verified",
                                "price_qualified", "capacity_amount", "capacity_reason", "capacity_evidence",
                                "reported_auction_amount", "amount_identity_delta", "daily_open_cent_match"):
                        self.assertEqual(preview[key], research[key], (kind, key, preview, research))
                    if preview["status"].startswith("NO_FILL_"):
                        self.assertEqual(preview["status"], research["label_status"])
                self.assertNotEqual(entry.POLICY_ID, researched[0]["entry_policy_id"])

    def test_price_qualified_bad_or_missing_amount_is_unknown_not_capacity_no_fill(self):
        for amount in (698, None, "invalid", 0, -1, True):
            with self.subTest(amount=amount):
                self.canonical[0][4] = amount
                self.write_canonical()
                before = [path.read_bytes() for path in self.canonical_paths]
                row = self.preview()["rows"][0]
                self.assertEqual(row["status"], "ENTRY_PROXY_OBSERVED_EXIT_PENDING")
                self.assertEqual(row["proxy_fill"], 1)
                self.assertEqual(row["entry_price"], 6.98)
                self.assertTrue(row["price_qualified"])
                self.assertFalse(row["capacity_proxy_verified"])
                self.assertIsNone(row["capacity_amount"])
                self.assertIsNone(row["entry_price_evidence"]["amount"])
                self.assertEqual(row["reported_auction_amount"], amount)
                self.assertEqual(row["capacity_evidence"], "UNKNOWN")
                self.assertIsNone(row["slot_net_return"])
                self.assertEqual([path.read_bytes() for path in self.canonical_paths], before)

    def test_market_range_matches_native_float_predicate_not_wider_ohlc_tolerance(self):
        from work.profit_1000_upgrade import labels_v3 as labels
        for name, (daily_changes, limit_changes, _, _) in MARKET_BOUNDARIES.items():
            with self.subTest(case=name):
                daily = {**self.daily[0], **daily_changes}
                limits = {**self.limits[0], **limit_changes}
                prices = {key: labels._finite(daily[key], positive=True)
                          for key in ("open", "high", "low", "close", "pre_close")}
                volume = labels._finite(daily["vol"])
                up, down = labels._finite(limits["up_limit"], positive=True), labels._finite(limits["down_limit"], positive=True)
                invalid = (volume < 0 or not prices["low"] <= min(prices["open"], prices["close"])
                           <= max(prices["open"], prices["close"]) <= prices["high"] or down >= up
                           or prices["high"] > up + 1e-8 or prices["low"] < down - 1e-8)
                flat = all(labels.settlement._same_rounded_price(prices["close"], prices[key])
                           for key in ("open", "high", "low"))
                if invalid or volume == 0 and not flat:
                    with self.assertRaises(entry.PreparationError):
                        entry._market_values(daily, limits)
                else:
                    self.assertEqual(entry._market_values(daily, limits), (prices, volume, up))

    def test_reported_subcent_price_and_numeric_string_are_not_rewritten(self):
        for price, amount in ((6.984, 13968000), ("6.9800", 13960000)):
            with self.subTest(price=price):
                self.canonical[0][2], self.canonical[0][4] = price, amount
                self.write_canonical()
                row = self.preview()["rows"][0]
                self.assertEqual(row["entry_price"], price)
                self.assertIs(type(row["entry_price"]), type(price))
                self.assertEqual(row["proxy_fill"], 1)
                self.assertTrue(row["daily_open_cent_match"])
                self.assertEqual(row["entry_price_evidence"]["reported_auction_price"], price)

    def test_native_pending_qualifications_cannot_fallback_or_become_zero(self):
        for field, value in (("vol", 1.5), ("vol", None), ("pre_close", 0),
                             ("price", None), ("price", 0), ("price", 6.81)):
            with self.subTest(field=field, value=value):
                self.canonical[0] = [CODES[0], T, 6.98, 2000000, 13960000, 6.78]
                self.canonical[0][auction_truth.FIELDS.index(field)] = value
                self.write_canonical()
                rows = self.preview()["rows"]
                self.assert_pending(rows[0])
                self.assertEqual(rows[0]["entry_price_source"], "TUSHARE_STK_AUCTION")
                self.assertIsNone(rows[0]["entry_price"])
                self.assertIsNone(rows[0]["capacity_amount"])
                self.assertEqual(rows[1]["proxy_fill"], 1)

    def test_positive_reported_price_with_zero_volume_is_conflict_not_no_trade(self):
        self.canonical[0][3:5] = [0, 0]
        self.write_canonical()
        row = self.preview()["rows"][0]
        self.assertEqual(row["status"], "PENDING_ENTRY_ZERO_VOLUME_DATA_CONFLICT")
        self.assert_pending(row)

    def test_nonflat_zero_daily_volume_cannot_be_suspension_zero(self):
        self.write_canonical(rows=[])
        self.daily[0]["vol"] = 0
        self.write_market()
        self.assert_pending(self.preview()["rows"][0])

    def test_unselected_numeric_bad_row_does_not_poison_native_candidate_prices(self):
        self.canonical.append(["600000.SH", T, None, 1.5, "invalid", 0])
        self.write_canonical()
        self.assertEqual([row["proxy_fill"] for row in self.preview()["rows"]], [1, 1])

    def test_v2_receipt_is_not_promoted_or_read_as_v3(self):
        from work.profit_1000_upgrade import auction_truth as old_source
        raw = json.dumps({"code": 0, "data": {"fields": list(old_source.FIELDS),
                         "items": self.canonical, "count": 0, "has_more": False}}).encode()
        pair = old_source.source_bytes(raw, T, request=old_source.request_contract(T),
            fetched_at_utc="2026-09-15T08:00:00Z", network_request_performed=True)
        paths = old_source.source_paths(self.root, T)
        paths[0].parent.mkdir(parents=True)
        for path, body in zip(paths, pair):
            path.write_bytes(body)
        for path in self.canonical_paths:
            path.unlink()
        with patch.object(old_source, "load", side_effect=AssertionError("old source read")):
            for row in self.preview()["rows"]:
                self.assertEqual(row["status"], "PENDING_CANONICAL_ATTEMPT_REQUIRED")
                self.assert_pending(row)
        self.assertEqual([path.read_bytes() for path in paths], list(pair))

    def test_all_rank_identities_checked_before_any_auction_source_read(self):
        ranks = copy.deepcopy(RANKS) + [{"ts_code": "600000.SH", "stage_transition": "2->3",
                                      "executable_profit_research_rank": 3.0}]
        with patch.object(auction_truth, "load", side_effect=AssertionError("early source read")):
            with self.assertRaises(entry.PreparationError):
                self.preview(ranked_rows=ranks)

    def test_unknown_native_status_is_fail_closed_not_proxy_fill(self):
        original = auction_truth.entry_price
        def unknown(*args, **kwargs):
            result = original(*args, **kwargs)
            result["status"] = "UNRECOGNIZED_QUALIFICATION"
            return result
        with patch.object(auction_truth, "entry_price", side_effect=unknown):
            for row in self.preview()["rows"]:
                self.assert_pending(row)

    def test_research_qualifications_never_issue_formal_authority(self):
        out = self.preview()
        self.assertTrue(out["research_only"])
        self.assertFalse(out["source_authority_issued"])
        self.assertFalse(out["known_before_0925"])
        self.assertFalse(out["price_reporting_precision_confirmed"])
        self.assertEqual(out["basis"], policy_v3.BASIS)
        self.assertTrue(out["reported_price_preserved"])
        for row in out["rows"]:
            evidence = row["entry_price_evidence"]
            self.assertEqual(evidence["source_policy_id"], policy_v3.AUCTION_SOURCE_POLICY_ID)
            self.assertTrue(evidence["research_only"])
            self.assertFalse(evidence["production_integrated"])
            self.assertFalse(evidence["actual_execution_claimed"])
            self.assertFalse(evidence["actual_capacity_verified"])
            self.assertFalse(row["terminal_settlement"])
            self.assertFalse(row["production_ledger_eligible"])

    def test_new_split_policy_fields_are_exact_not_runtime_switches(self):
        original = json.loads(entry.POLICY_PATH.read_bytes())
        for key in ("research_economic_reference", "research_source_reference", "price_qualification",
                    "unqualified_amount", "zero_daily_volume", "capacity_rule"):
            with self.subTest(key=key):
                policy = {**original, key: "UNREVIEWED"}
                path = self.root / "changed_policy.json"
                path.write_text(json.dumps(policy))
                with patch.object(entry, "POLICY_PATH", path), self.assertRaises(entry.PreparationError):
                    entry.policy_binding()

    def test_original_v3_and_native_research_bytes_remain_frozen(self):
        pinned = {
            "formal_entry_v3.py": "9ca51c6a759433969bef8b4bce13a8f0648b1961695122d1c123f9afdeb33c8e",
            "FORMAL_ENTRY_V3_POLICY.json": "f1163052b742d37bd5b80a31dd82c22526c7d687b6cb9405c6400ec3d9449237",
            "test_formal_entry_v3.py": "a8990bd84deb4aa83e1b9e72965224880b70e2ac02a3be3e7677806d31dd35d3",
            "FORMAL_ENTRY_V3_MIGRATION.md": "02fcfdc2887e93dac9988f60b7d7e95e5d80263ebd57f9eaf5deaf8a5fee28cd",
            "auction_truth_v3.py": "889765e435f2e44c6bedc21ef74789c5155081160da54624a147fdd3bca43665",
            "policy_v3.py": "a384365e7200643738c4f03e6f8236cd4ac8984eb879ff69752f5a4de0ad729b",
            "labels_v3.py": "cfe592fd7d90101514204d45d4e7f3a27e12f13bd8d3905fc77d89c9dee2fe9b",
        }
        for name, expected in pinned.items():
            self.assertEqual(hashlib.sha256(Path(entry.__file__).with_name(name).read_bytes()).hexdigest(), expected)

    def test_inherited_identity_source_helpers_and_activation_guard_ast_unchanged(self):
        old_text = Path(entry.__file__).with_name("formal_entry_v3.py").read_text()
        old_text = old_text.replace("RESEARCH_V2_FULL_REPLAY_AND_FORWARD_ACCEPTANCE_REQUIRED",
                                    "RESEARCH_V3_FULL_REPLAY_AND_FORWARD_ACCEPTANCE_REQUIRED")
        old = {node.name: ast.dump(node) for node in ast.parse(old_text).body if isinstance(node, ast.FunctionDef)}
        new = {node.name: ast.dump(node) for node in ast.parse(Path(entry.__file__).read_text()).body
               if isinstance(node, ast.FunctionDef)}
        for name in ("_require", "_date", "_stamp", "_number", "_cent", "_json", "_bound_table", "activation_check"):
            self.assertEqual(old[name], new[name], name)


if __name__ == "__main__":
    unittest.main()
