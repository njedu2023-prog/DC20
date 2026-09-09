"""Offline bundle adapter tests; fixture provenance is not a real acquisition.

Fixture CSV bytes are copied from the committed D908 replay input, outside the
guarded inference. Synthetic upstream metadata/commits only let the real bundle
reader exercise its contract without network access. No fixture is a NATURAL
admission or a claim of upstream publication. The separate CI rehearsal also
collects real immutable HTTPS inputs before invoking this adapter.
"""
import builtins
import copy
import csv
import hashlib
import io
import json
import os
import socket
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from forward import inputs, promotion
from forward.bundle import VerifiedInputBundle

ROOT = Path(__file__).resolve().parents[2]
D = "20260908"
NOW = datetime(2026, 9, 9, 0, tzinfo=timezone.utc)
GENERATED = NOW.isoformat()
PRED, MARKET = "1" * 40, "2" * 40


class ReplayFixtureSource:
    def __init__(self, *, optional=True):
        self.optional = optional
        self.cache = {}

    def _csv(self, date, table):
        key = date, table
        if key not in self.cache:
            path = (ROOT / f"data/pred/archive/pred_source_{date}.csv" if table == "candidate"
                    else ROOT / f"data/market/raw/{date[:4]}/{date}/{table}.csv")
            self.cache[key] = path.read_bytes()
        return self.cache[key]

    def _count(self, date, table):
        return sum(1 for _ in csv.DictReader(io.StringIO(self._csv(date, table).decode("utf-8-sig"))))

    def __call__(self, url):
        inputs._validate_url(url)
        if "/a-top10/" in url:
            return self._csv(D, "candidate")
        date, filename = url.split("/")[-2:]
        table = filename.split(".")[0]
        if table != "_meta":
            return self._csv(date, table)
        jobs = [{"key": name, "status": "ok", "error": None,
                 "rows": self._count(date, name),
                 "kwargs": {} if name == "stock_basic" else {"trade_date": date}}
                for name in inputs.REQUIRED_TABLES]
        # Test-only upstream metadata, not the legacy DC20 _sync_meta format.
        metadata = {"resolved_trade_date": date, "requested_trade_date": date,
                    "generated_at_bj": f"{date[:4]}-{date[4:6]}-{date[6:]} 22:00:00",
                    "jobs": jobs}
        if self.optional:
            metadata["derived"] = {"hot_board_tags": {
                "tagged": self._count(date, "limit_up_tags"),
                "hot_boards": self._count(date, "hot_boards")}}
            metadata["intraday_features"] = {"ok": True, "rows": self._count(date, "intraday_features")}
        return json.dumps(metadata, ensure_ascii=False).encode()


class BundlePromotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="dc20-bundle-promotion-tests.")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.output = Path(cls.temp.name).resolve() / "bundle"
        cls.legacy = promotion._import_p0(ROOT)
        cls.baseline = promotion.compute_promotion_bundle(ROOT, D, generated_at_utc=GENERATED)
        cls.manifest = inputs.collect_inputs(ROOT, cls.output, D, PRED, MARKET,
            fetch=ReplayFixtureSource(), now_utc=NOW)
        cls.manifest_sha = hashlib.sha256((cls.output / "manifest.json").read_bytes()).hexdigest()
        cls.opened = set()
        real_builtin, real_io = builtins.open, io.open

        def check(file, mode):
            if not isinstance(file, (str, bytes, os.PathLike)):
                return
            path = Path(os.fsdecode(file)).resolve()
            if path.is_relative_to(ROOT):
                relative = path.relative_to(ROOT).as_posix()
                if any(flag in mode for flag in ("w", "a", "x", "+")):
                    raise AssertionError(f"source write: {relative}")
                if relative.startswith(("data/market/raw/", "data/market/minute_1m/", "data/pred/",
                        "outputs/", "data/decision_executable_profit/", "data/auction_v3/predictions/",
                        "data/auction_v3/truth/", "data/auction_v3/metrics/", "data/auction_v3/verification/")):
                    raise AssertionError(f"old market/rank/truth read: {relative}")
                if "action_plan" in relative or "shadow" in relative.lower() or \
                        relative.endswith(("/profit.joblib", "/big_loss.joblib", ".pkl")):
                    raise AssertionError(f"secondary read: {relative}")
                cls.opened.add(relative)

        def guarded(actual):
            def run(file, mode="r", *args, **kwargs):
                check(file, mode)
                return actual(file, mode, *args, **kwargs)
            return run

        from sklearn.ensemble import HistGradientBoostingClassifier
        with ExitStack() as stack:
            stack.enter_context(patch("builtins.open", side_effect=guarded(real_builtin)))
            stack.enter_context(patch("io.open", side_effect=guarded(real_io)))
            stack.enter_context(patch.object(Path, "mkdir", side_effect=AssertionError("no inference writes")))
            stack.enter_context(patch.object(socket.socket, "connect", side_effect=AssertionError("no network")))
            stack.enter_context(patch.object(HistGradientBoostingClassifier, "fit", side_effect=AssertionError("no training")))
            for name in ("load_exact_market_package", "bind_committed_history_context",
                         "materialize_three_rank_artifacts", "_write_immutable_json", "_write_immutable_csv"):
                stack.enter_context(patch.object(cls.legacy, name, side_effect=AssertionError(f"legacy {name}")))
            stack.enter_context(patch.object(promotion, "_load_replay_candidate", side_effect=AssertionError("old candidate")))
            cls.result = promotion.compute_promotion_from_inputs(ROOT, cls.output,
                expected_manifest_sha256=cls.manifest_sha, generated_at_utc=GENERATED)

    def compute(self, **kwargs):
        args = dict(expected_manifest_sha256=self.manifest_sha, generated_at_utc=GENERATED)
        args.update(kwargs)
        return promotion.compute_promotion_from_inputs(ROOT, self.output, **args)

    def engine(self, verified=None):
        verified = verified or VerifiedInputBundle(ROOT, self.output, self.manifest_sha)
        verifier = self.legacy.GitHeadInputVerifier(ROOT)
        _, _, calendar = self.legacy.load_strict_sse_dates(ROOT, D, verifier=verifier)
        return promotion._bundle_engine(self.legacy, verified, ROOT, calendar, verifier)

    def test_same_dates_members_probabilities_and_paths_as_retained_replay(self):
        day = self.result["day"]
        self.assertEqual([day[key] for key in ("signal_date", "exec_date", "exit_date")],
                         [D, "20260909", "20260910"])
        self.assertEqual(self.result["receipt"]["promotion_pool_size"], 15)
        self.assertEqual(self.result["receipt"]["selected_count"], 10)
        self.assertEqual(day["rows"], self.baseline["day"]["rows"])
        self.assertEqual(self.result["feature_snapshot_sha256"], self.baseline["feature_snapshot_sha256"])

    def test_root_read_guard_and_distinct_upstream_code_provenance(self):
        source = self.result["day"]["source"]
        self.assertEqual(source["input_manifest_sha256"], self.manifest_sha)
        self.assertEqual(source["input_bundle_sha256"], self.manifest["bundle_sha256"])
        self.assertEqual(source["input_collector_commit"], self.manifest["source_commit"])
        self.assertEqual(source["source_commit"], self.legacy.GitHeadInputVerifier(ROOT).head)
        self.assertEqual(source["candidate"]["resolved_commit"], PRED)
        self.assertEqual(source["market"]["resolved_commit"], MARKET)
        self.assertFalse(source["root_market_or_candidate_read"])
        self.assertEqual(source["history"]["file_count"], 100)
        self.assertTrue(all(row["path"].startswith("market/") for row in source["consumed_market_files"]))
        self.assertFalse(any(path.startswith(("data/market/raw/", "data/pred/", "outputs/")) for path in self.opened))
        self.assertIn("models/decision_three_engines/promotion.joblib", self.opened)
        for key in ("training_performed", "production_enabled", "forward_ledger_eligible",
                    "legacy_ranking_read", "legacy_action_or_statistics_read", "secondary_models_loaded"):
            self.assertIs(source[key], False)

    def test_p1_bundle_contract_accepts_new_source_schema_without_reading_profit(self):
        from forward.profit import _validate_bundle
        day, codes, runtime_sha = _validate_bundle(self.result)
        self.assertEqual(day, self.result["day"])
        self.assertEqual(codes, [row["ts_code"] for row in day["rows"]])
        self.assertEqual(runtime_sha, self.result["runtime_sha256"])

    def test_bad_manifest_date_mode_and_time_fail_before_inference(self):
        cases = [{"expected_manifest_sha256": "0" * 64}, {"expected_manifest_sha256": None},
                 {"generation_mode": "NATURAL"}, {"generated_at_utc": "2026-09-08T23:59:59Z"},
                 {"generated_at_utc": "2099-01-01T00:00:00Z"},
                 {"generated_at_utc": "2026-09-09T00:00:00"}]
        with patch.object(self.legacy, "score_three_engine_snapshot", side_effect=AssertionError("no inference")):
            for case in cases:
                with self.subTest(case=case), self.assertRaises(promotion.PromotionError):
                    self.compute(**case)

    def test_required_missing_body_fails_not_root_fallback(self):
        original = VerifiedInputBundle.read_market_bytes
        def missing(reader, date, table):
            return None if table == "daily" and date == D else original(reader, date, table)
        with patch.object(VerifiedInputBundle, "read_market_bytes", missing), \
             patch.object(self.legacy, "score_three_engine_snapshot", side_effect=AssertionError("no inference")):
            with self.assertRaisesRegex(promotion.PromotionError, "required bundle table unavailable"):
                self.compute()

    def test_optional_missing_remains_empty_and_minute_has_no_disk_fallback(self):
        verified = VerifiedInputBundle(ROOT, self.output, self.manifest_sha)
        original = verified.read_market_bytes
        with patch.object(verified, "read_market_bytes", side_effect=lambda date, table:
                None if table == "limit_up_tags" else original(date, table)):
            engine = self.engine(verified)
            with patch.object(self.legacy.PrimaryDReadOnlyEngine, "_market_path", side_effect=AssertionError("old path")), \
                 patch.object(self.legacy.PrimaryDReadOnlyEngine, "minute_table", side_effect=AssertionError("old minute")):
                self.assertTrue(engine.market_table(D, "limit_up_tags").empty)
                self.assertTrue(engine.minute_table(D, "000759.SZ").empty)
            self.assertEqual(engine.consumed_bindings(), [])
        for call in (lambda: engine._market_path(D, "daily"), lambda: engine._minute_path(D, "000759.SZ"),
                     lambda: engine.market_table("20260909", "daily"),
                     lambda: engine.minute_table("20260810", "000759.SZ")):
            with self.assertRaises(promotion.PromotionError):
                call()

    def test_real_optional_absence_bundle_computes_without_padding_data(self):
        output = Path(self.temp.name).resolve() / "optional-absent"
        manifest = inputs.collect_inputs(ROOT, output, D, PRED, MARKET,
            fetch=ReplayFixtureSource(optional=False), now_utc=NOW)
        digest = hashlib.sha256((output / "manifest.json").read_bytes()).hexdigest()
        with patch.object(self.legacy, "load_exact_market_package", side_effect=AssertionError("no fallback")):
            result = promotion.compute_promotion_from_inputs(ROOT, output,
                expected_manifest_sha256=digest, generated_at_utc=GENERATED)
        source = result["day"]["source"]
        self.assertEqual(len(source["market"]["optional_gaps"]), 63)
        self.assertEqual(result["receipt"]["promotion_pool_size"], 15)
        self.assertEqual(result["receipt"]["selected_count"], 10)
        self.assertTrue(all(item["table"] in inputs.REQUIRED_TABLES
                            for item in source["consumed_market_files"]))
        self.assertEqual(source["market"]["optional_gaps"], manifest["optional_gaps"])

    def test_incomplete_bundle_disk_inventory_fails_without_inference(self):
        output = Path(self.temp.name).resolve() / "incomplete"
        output.mkdir()
        (output / "manifest.json").write_bytes((self.output / "manifest.json").read_bytes())
        with patch.object(self.legacy, "score_three_engine_snapshot", side_effect=AssertionError("no inference")):
            with self.assertRaisesRegex(promotion.PromotionError, "inventory"):
                promotion.compute_promotion_from_inputs(ROOT, output,
                    expected_manifest_sha256=self.manifest_sha, generated_at_utc=GENERATED)

    def test_missing_numeric_values_are_not_zero_filled(self):
        engine = self.engine()
        table = engine.market_table("20260811", "daily_basic")
        self.assertTrue(table["float_mv"].isna().all())
        self.assertGreater(table["volume_ratio"].isna().sum(), 0)
        self.assertTrue(all(row["binding_basis"] == "verified_input_manifest"
                            for row in engine.consumed_bindings()))

    def test_calendar_context_mismatch_rejected_before_model_load(self):
        original = self.legacy.load_strict_sse_dates
        def wrong(*args, **kwargs):
            t, t1, calendar = original(*args, **kwargs)
            calendar = copy.deepcopy(calendar)
            calendar["runtime_context_dates"] = calendar["runtime_context_dates"][1:]
            return t, t1, calendar
        with patch.object(self.legacy, "load_strict_sse_dates", side_effect=wrong), \
             patch.object(self.legacy, "load_promotion_only_artifacts", side_effect=AssertionError("no model load")):
            with self.assertRaisesRegex(promotion.PromotionError, "disagree"):
                self.compute()

    def test_no_hard_members_is_real_zero_not_padded(self):
        original = self.legacy.build_exact_primary_pool
        def empty(*args, **kwargs):
            frame, audit = original(*args, **kwargs)
            return frame.iloc[:0], dict(audit, hard_stage_rows=0)
        with patch.object(self.legacy, "build_exact_primary_pool", side_effect=empty):
            result = self.compute()
        self.assertEqual(result["day"]["rows"], [])
        self.assertEqual(result["runtime_rows"], [])
        self.assertEqual(result["receipt"]["selected_count"], 0)
        self.assertFalse(result["day"]["source"]["inference_performed"])

    def test_preimport_source_drift_rejected(self):
        original = Path.read_bytes
        def changed(path):
            body = original(path)
            return body + b"\n# drift" if path == ROOT / "scripts/publish_primary_three_rank.py" else body
        with patch.object(Path, "read_bytes", changed), \
             patch.object(promotion, "_import_p0", side_effect=AssertionError("cannot import drifted code")):
            with self.assertRaisesRegex(promotion.PromotionError, "before import"):
                self.compute()

    def test_static_prior_symlink_is_rejected_before_model_or_feature_reads(self):
        original = Path.is_symlink
        target = ROOT / self.legacy.PROMOTION_PRIOR_SOURCE_PATHS[0]
        with patch.object(Path, "is_symlink", lambda path: path == target or original(path)), \
             patch.object(self.legacy, "load_promotion_only_artifacts", side_effect=AssertionError("no model load")):
            with self.assertRaisesRegex(promotion.PromotionError, "symlink input forbidden"):
                self.compute()


if __name__ == "__main__":
    unittest.main()
