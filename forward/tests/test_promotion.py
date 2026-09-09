"""Real frozen-model inference replay, with legacy outputs inaccessible."""
import builtins
import copy
import csv
import io
import json
import math
import os
import socket
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from forward import promotion

ROOT = Path(__file__).resolve().parents[2]
DATE = "20260908"
GENERATED = "2026-09-09T00:00:00+00:00"


class PromotionComputeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        promotion._preflight_code(ROOT)
        cls.legacy = promotion._import_p0(ROOT)
        cls.opened = set()
        cls.loaded_models = []
        old_builtin_open, old_io_open = builtins.open, io.open
        import joblib
        from sklearn.ensemble import HistGradientBoostingClassifier
        actual_model_load = joblib.load

        def check_file(file, mode):
            if isinstance(file, (str, bytes, os.PathLike)):
                path = Path(os.fsdecode(file)).resolve()
                if path.is_relative_to(ROOT):
                    rel = path.relative_to(ROOT).as_posix()
                    if any(flag in mode for flag in ("w", "a", "x", "+")):
                        raise AssertionError(f"P0 tried to write source: {rel}")
                    if rel.startswith(("outputs/", "data/decision_executable_profit/",
                        "data/auction_v3/predictions/", "data/auction_v3/truth/",
                        "data/auction_v3/metrics/", "data/auction_v3/verification/")):
                        raise AssertionError(f"P0 tried to read old rank/truth/statistics: {rel}")
                    if "action_plan" in rel or rel.endswith(("/profit.joblib", "/big_loss.joblib", "/p_fill_shadow.joblib", ".pkl")):
                        raise AssertionError(f"P0 tried to read a secondary model or Action: {rel}")
                    if rel == "data/pred/_pred_source_meta.json":
                        raise AssertionError("historical replay must not depend on mutable latest metadata")
                    cls.opened.add(rel)

        def safe_builtin(file, mode="r", *args, **kwargs):
            check_file(file, mode)
            return old_builtin_open(file, mode, *args, **kwargs)

        def safe_io(file, mode="r", *args, **kwargs):
            check_file(file, mode)
            return old_io_open(file, mode, *args, **kwargs)

        def load_model(path, *args, **kwargs):
            cls.loaded_models.append(Path(path).relative_to(ROOT).as_posix())
            return actual_model_load(path, *args, **kwargs)

        # This is a genuine model prediction. Read spies make a borrowed legacy
        # result impossible; only the expected weight may be deserialized.
        with patch("builtins.open", side_effect=safe_builtin), patch("io.open", side_effect=safe_io), \
             patch.object(Path, "mkdir", side_effect=AssertionError("P0 must not create legacy output dirs")), \
             patch.object(socket.socket, "connect", side_effect=AssertionError("P0 may not access network")), \
             patch.object(HistGradientBoostingClassifier, "fit", side_effect=AssertionError("no training allowed")), \
             patch.object(joblib, "load", side_effect=load_model), \
             patch.object(cls.legacy, "materialize_three_rank_artifacts", side_effect=AssertionError("no legacy publisher")), \
             patch.object(cls.legacy, "_write_immutable_csv", side_effect=AssertionError("no legacy writer")), \
             patch.object(cls.legacy, "_write_immutable_json", side_effect=AssertionError("no legacy writer")):
            cls.bundle = promotion.compute_promotion_bundle(ROOT, DATE, generated_at_utc=GENERATED)
        # Expected frozen output is read ONLY after independent inference ends.
        cls.expected = json.loads((ROOT / f"outputs/decision/three_rank_top10_{DATE}.json").read_text())
        with (ROOT / f"outputs/decision/primary_d_runtime_features_{DATE}.csv").open(newline="") as handle:
            cls.expected_runtime = {r["ts_code"]: r for r in csv.DictReader(handle)}

    def test_real_d0908_inference_parity(self):
        day = self.bundle["day"]
        self.assertEqual([day[k] for k in ("signal_date", "exec_date", "exit_date")], ["20260908", "20260909", "20260910"])
        self.assertEqual(len(self.bundle["runtime_rows"]), 15)
        self.assertEqual(len(day["rows"]), 10)
        self.assertEqual(self.bundle["feature_snapshot_sha256"], self.expected["feature_snapshot_sha256"])
        for row, expected in zip(day["rows"], self.expected["rows"], strict=True):
            for key in ("ts_code", "name", "industry", "stage_transition", "promotion_rank"):
                self.assertEqual(row[key], expected[key])
            self.assertEqual(row["promotion_probability"], expected["predicted_promotion_probability"])
            self.assertEqual(row["path_label"], self.expected_runtime[row["ts_code"]]["path_label"])
            self.assertTrue(math.isclose(row["path_change_pct"], float(self.expected_runtime[row["ts_code"]]["path_strength_delta"]), rel_tol=0, abs_tol=1e-15))

    def test_no_secondary_model_or_result_dependency(self):
        self.assertEqual(self.loaded_models, ["models/decision_three_engines/promotion.joblib"])
        self.assertIn(f"data/pred/archive/pred_source_{DATE}.csv", self.opened)
        self.assertIn(f"data/market/raw/2026/{DATE}/daily.csv", self.opened)
        self.assertFalse(any(path.startswith("outputs/") for path in self.opened))
        for row in self.bundle["day"]["rows"]:
            self.assertEqual(set(row), {"ts_code", "name", "industry", "stage_transition", "promotion_rank",
                "promotion_probability", "path_label", "path_change_pct"})
        for column in self.bundle["runtime_columns"]:
            self.assertFalse(column.startswith(promotion.SECONDARY_PREFIXES), column)
            self.assertNotIn(column, promotion.SECONDARY_FIELDS)

    def test_replay_is_true_inference_not_production_or_old_stats(self):
        day = self.bundle["day"]
        self.assertEqual(day["generation_mode"], "REPLAY")
        source = day["source"]
        for key in ("training_performed", "legacy_ranking_read", "legacy_action_or_statistics_read", "secondary_models_loaded",
                    "production_enabled", "forward_ledger_eligible"):
            self.assertIs(source[key], False)
        self.assertIs(source["inference_performed"], True)
        self.assertEqual(self.bundle["receipt"]["day_sha256"], promotion.canonical_sha256(day))
        self.assertEqual(source["model_sha256"], promotion.FIXED_INPUTS["models/decision_three_engines/promotion.joblib"])

    def test_full_hard_pool_runtime_is_finite_json_and_hash_bound(self):
        b = self.bundle
        expected = promotion.canonical_sha256(dict(schema_version=promotion.RUNTIME_SCHEMA,
            signal_date=DATE, columns=b["runtime_columns"], rows=b["runtime_rows"]))
        self.assertEqual(expected, b["runtime_sha256"])
        self.assertEqual(expected, b["day"]["source"]["runtime_sha256"])
        json.dumps(b, allow_nan=False)
        selected = [r for r in b["runtime_rows"] if r["top10_selected"] == 1]
        self.assertEqual(len(selected), 10)
        self.assertEqual(sorted(r["promotion_rank"] for r in b["runtime_rows"]), list(range(1, 16)))
        self.assertTrue(all(r["signal_date"] == DATE for r in b["runtime_rows"]))

    def test_required_input_provenance_exact_window_and_git(self):
        s = self.bundle["day"]["source"]
        self.assertRegex(s["source_commit"], r"^[a-f0-9]{40}$")
        self.assertEqual(len(s["history"]["dates"]), 20)
        self.assertEqual(s["history"]["file_count"], 100)
        self.assertTrue(all(d < DATE for d in s["history"]["dates"]))
        self.assertTrue(all(f["trade_date"] <= DATE for f in s["consumed_market_files"]))
        self.assertEqual(s["candidate"]["source_repository"], "njedu2023-prog/a-top10")
        self.assertEqual(s["market"]["source_repository"], "njedu2023-prog/a-share-top3-data")
        self.assertEqual(s["candidate"]["meta_path"], "forward/replay_inputs/candidate_meta_20260908.json")
        self.assertEqual(s["candidate"]["metadata_archive_source"]["source_commit"], "76d1d779dd55f29f986d5c545cb80fd18fa64797")
        self.assertNotIn("data/pred/_pred_source_meta.json", self.opened)

    def test_pinned_meta_is_original_bytes_and_next_day_latest_is_ignored(self):
        spec = promotion.REPLAY_CANDIDATE_META[DATE]
        self.assertEqual(promotion._sha(ROOT / spec["path"]), spec["sha256"])
        original_read_json = self.legacy._read_json
        def next_day_latest(path, *, label):
            if Path(path) == ROOT / "data/pred/_pred_source_meta.json":
                return {"resolved_trade_date": "20260909", "sha256": "0" * 64}
            return original_read_json(path, label=label)
        with patch.object(self.legacy, "_read_json", side_effect=next_day_latest):
            frame, binding = promotion._load_replay_candidate(ROOT, DATE, "20260909", self.legacy)
        self.assertEqual(binding["sha256"], self.bundle["day"]["source"]["candidate"]["sha256"])
        self.assertEqual(len(frame), 71)

    def test_source_drift_is_rejected_before_module_import(self):
        actual_read_bytes = Path.read_bytes
        def changed(path):
            data = actual_read_bytes(path)
            return data + b"\n# changed" if path == ROOT / "scripts/publish_primary_three_rank.py" else data
        with patch.object(Path, "read_bytes", changed), patch.object(promotion, "_import_p0", side_effect=AssertionError("cannot execute changed code")):
            with self.assertRaisesRegex(promotion.PromotionError, "before import"):
                promotion.compute_promotion_day(ROOT, DATE, generated_at_utc=GENERATED)

    def test_preimport_closure_excludes_unused_profit_runtime(self):
        _, files = promotion._preflight_code(ROOT)
        paths = {row["path"] for row in files}
        self.assertIn("scripts/publish_primary_three_rank.py", paths)
        self.assertIn("src/top10decision/auction_v3/engine.py", paths)
        self.assertNotIn("scripts/publish_primary_profit_rankings.py", paths)
        self.assertNotIn("src/top10decision/decision/executable_profit_shadow.py", paths)

    def test_invalid_date_mode_or_timestamp_fails_before_inference(self):
        cases = [("../20260908", GENERATED, "REPLAY"), ("20260931", GENERATED, "REPLAY"),
                 (DATE, "2026-09-09T00:00:00", "REPLAY"), (DATE, "2099-01-01T00:00:00Z", "REPLAY"),
                 (DATE, GENERATED, "NATURAL")]
        for date, generated, mode in cases:
            with self.subTest(date=date, mode=mode), patch.object(self.legacy, "score_three_engine_snapshot", side_effect=AssertionError("must fail before inference")):
                with self.assertRaises(promotion.PromotionError):
                    promotion.compute_promotion_day(ROOT, date, generated_at_utc=generated, generation_mode=mode)

    def test_real_nontrading_or_mismatched_latest_meta_does_not_fallback(self):
        for date in ("20260906", "20260907"):
            with self.subTest(date=date), patch.object(self.legacy, "score_three_engine_snapshot", side_effect=AssertionError("must fail before inference")):
                with self.assertRaises(promotion.PromotionError):
                    promotion.compute_promotion_day(ROOT, date, generated_at_utc=GENERATED)

    def test_model_pin_mismatch_fails_before_deserialization(self):
        actual_sha = promotion._sha
        def changed(path):
            return "0" * 64 if str(path).endswith("/promotion.joblib") else actual_sha(path)
        with patch.object(promotion, "_sha", side_effect=changed), patch.object(self.legacy, "load_promotion_only_artifacts", side_effect=AssertionError("must not deserialize")):
            with self.assertRaisesRegex(promotion.PromotionError, "bytes changed"):
                promotion.compute_promotion_day(ROOT, DATE, generated_at_utc=GENERATED)

    def test_git_checkout_required_even_for_replay(self):
        with patch.object(self.legacy.GitHeadInputVerifier, "__init__", side_effect=self.legacy.PrimaryDGenerationError("Git checkout required")):
            with self.assertRaisesRegex(promotion.PromotionError, "Git checkout"):
                promotion.compute_promotion_day(ROOT, DATE, generated_at_utc=GENERATED)

    def test_secondary_modules_unavailable_do_not_affect_day_wrapper(self):
        # The real inference above already made secondary reads impossible.
        # The convenience wrapper itself returns P0 without invoking P1.
        with patch.object(promotion, "compute_promotion_bundle", return_value=copy.deepcopy(self.bundle)) as compute:
            day = promotion.compute_promotion_day(ROOT, DATE, generated_at_utc=GENERATED)
        self.assertEqual(day, self.bundle["day"])
        compute.assert_called_once()

    def test_empty_hard_range_is_zero_not_padded(self):
        original_pool = self.legacy.build_exact_primary_pool
        def empty(*args, **kwargs):
            pool, audit = original_pool(*args, **kwargs)
            audit = dict(audit, hard_stage_rows=0)
            return pool.iloc[:0].copy(), audit
        with patch.object(self.legacy, "build_exact_primary_pool", side_effect=empty):
            empty_bundle = promotion.compute_promotion_bundle(ROOT, DATE, generated_at_utc=GENERATED)
        self.assertEqual(empty_bundle["day"]["rows"], [])
        self.assertEqual(empty_bundle["runtime_rows"], [])
        self.assertEqual(empty_bundle["receipt"]["selected_count"], 0)
        self.assertFalse(empty_bundle["day"]["source"]["inference_performed"])
        self.assertTrue(empty_bundle["day"]["source"]["computation_performed"])


if __name__ == "__main__":
    unittest.main()
