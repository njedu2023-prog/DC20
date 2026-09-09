"""Read-only real replay plus isolated synthetic mutations; no production ledger."""
import copy
import csv
import hashlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from forward import engine

ROOT = Path(__file__).resolve().parents[2]
DATE = "20260908"
P0 = f"outputs/decision/three_rank_top10_{DATE}.json"
P0CSV = P0.replace(".json", ".csv")
RECEIPT = f"outputs/decision/primary_d_receipt_{DATE}.json"
RUNTIME = f"outputs/decision/primary_d_runtime_features_{DATE}.csv"
P1 = f"outputs/decision/executable_profit_research/projection_{DATE}.json"
P1CSV = P1.replace(".json", ".csv")
INVENTORY = json.loads((ROOT / "forward/model_inventory.json").read_text())
SOURCE_FILES = [P0, P0CSV, RECEIPT, RUNTIME, P1, P1CSV] + [
    x["path"] for x in INVENTORY["assets"] if x["verify_for_replay"]]


class FrozenEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for path in SOURCE_FILES:
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / path, target)
        self.p0 = self.read(P0)
        self.p1 = self.read(P1)
        self.receipt = self.read(RECEIPT)
        with (self.root / RUNTIME).open(newline="") as handle:
            reader = csv.DictReader(handle)
            self.columns = reader.fieldnames
            self.runtime = list(reader)

    def tearDown(self):
        self.temp.cleanup()

    def read(self, path):
        return json.loads((self.root / path).read_text())

    def write(self, path, value):
        (self.root / path).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")

    def sha(self, path):
        return hashlib.sha256((self.root / path).read_bytes()).hexdigest()

    def rebind(self):
        """Re-sign synthetic fixture, to test semantic validation beyond file SHA."""
        buf = io.StringIO(newline="")
        writer = csv.DictWriter(buf, fieldnames=self.columns)
        writer.writeheader()
        writer.writerows(self.runtime)
        (self.root / RUNTIME).write_text(buf.getvalue())
        n = len(self.p0["rows"])
        self.p0["top10_count"] = n
        self.p0["promotion_pool_size"] = len(self.runtime)
        members = engine._digest({"schema": "dc20_three_rank_member_set_v1", "signal_date": DATE,
                                  "members": sorted(r["ts_code"] for r in self.p0["rows"])})
        self.p0["top10_members_sha256"] = members
        self.p0["bundle_sha256"] = engine._bundle(self.p0)
        self.write(P0, self.p0)
        outputs = self.receipt["outputs"]
        for key in ("top10_count", "promotion_pool_size", "top10_members_sha256", "bundle_sha256", "feature_snapshot_sha256"):
            outputs[key] = self.p0[key]
        outputs["json_sha256"] = self.sha(P0)
        outputs["runtime_features_sha256"] = self.sha(RUNTIME)
        outputs["runtime_feature_row_count"] = len(self.runtime)
        outputs["runtime_selected_count"] = n
        identities = [{"identity": r["identity"], "ts_code": r["ts_code"], "stage_transition": r["stage_transition"],
                       "top10_selected": int(float(r["top10_selected"])), "promotion_rank": int(float(r["promotion_rank"]))}
                      for r in sorted(self.runtime, key=lambda x: x["ts_code"])]
        outputs["runtime_identity_sha256"] = engine._digest({"schema": "dc20_primary_d_runtime_identity_v1", "signal_date": DATE, "rows": identities})
        self.write(RECEIPT, self.receipt)
        self.p1["candidate_count"] = n
        self.p1["top10_members_sha256"] = members
        self.p1["source_bundle_sha256"] = self.p0["bundle_sha256"]
        self.p1["source_feature_snapshot_sha256"] = self.p0["feature_snapshot_sha256"]
        b = self.p1["source_bindings"]
        b["primary_receipt"]["sha256"] = self.sha(RECEIPT)
        for key in b["three_rank"]:
            b["three_rank"][key] = outputs[key]
        b["runtime_features"].update(sha256=self.sha(RUNTIME), row_count=len(self.runtime), selected_count=n,
            identity_sha256=outputs["runtime_identity_sha256"], feature_snapshot_sha256=self.p0["feature_snapshot_sha256"])
        self.p1["downloads"]["row_count"] = n
        self.p1["snapshot_sha256"] = engine._projection_snapshot(self.p1)
        self.write(P1, self.p1)

    def load(self):
        return engine.load_frozen_day(self.root, DATE)

    def test_actual_d0908_replay_exact_values_and_no_writes(self):
        before = {p: self.sha(p) for p in SOURCE_FILES}
        day = self.load()
        self.assertEqual([day[k] for k in ("signal_date", "exec_date", "exit_date")], ["20260908", "20260909", "20260910"])
        self.assertEqual(day["generation_mode"], "REPLAY")
        self.assertEqual(len(day["rows"]), 10)
        self.assertEqual([r["ts_code"] for r in day["rows"]], [r["ts_code"] for r in self.p0["rows"]])
        p1 = {r["ts_code"]: r for r in self.p1["rows"]}
        runtime = {r["ts_code"]: r for r in self.runtime}
        for actual, frozen in zip(day["rows"], self.p0["rows"]):
            self.assertEqual(actual["promotion_probability"], frozen["predicted_promotion_probability"])
            self.assertEqual(actual["promotion_rank"], frozen["promotion_rank"])
            self.assertEqual(actual["profit_rank"], p1[actual["ts_code"]]["executable_profit_research_rank"])
            self.assertEqual(actual["profit_score"], p1[actual["ts_code"]]["research_joint_proxy_score"])
            self.assertEqual(actual["path_change_pct"], float(runtime[actual["ts_code"]]["path_strength_delta"]))
        self.assertEqual([r["ts_code"] for r in sorted(day["rows"], key=lambda r: r["profit_rank"])[:2]], ["002579.SZ", "002172.SZ"])
        self.assertFalse(day["source"]["inference_performed"])
        self.assertFalse(day["source"]["forward_ledger_eligible"])
        self.assertFalse(day["source"]["old_statistics_imported"])
        self.assertEqual(before, {p: self.sha(p) for p in SOURCE_FILES})
        self.assertEqual(set(p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file()), set(SOURCE_FILES))

    def test_no_natural_reclassification(self):
        with self.assertRaisesRegex(engine.FrozenDayError, "REPLAY only"):
            engine.load_frozen_day(self.root, DATE, generation_mode="NATURAL")

    def test_missing_day_cannot_borrow_latest(self):
        for date in ("20260907", "../20260908", "20260931"):
            with self.subTest(date=date), self.assertRaises(engine.FrozenDayError):
                engine.load_frozen_day(self.root, date)

    def test_missing_or_changed_source_and_models_fail_closed(self):
        for path in SOURCE_FILES:
            with self.subTest(path=path):
                original = (self.root / path).read_bytes()
                # P1 authenticates its canonical semantic snapshot, not JSON
                # whitespace; its complete byte digest is separately recorded.
                (self.root / path).write_bytes(b"!" + original if path == P1 else original + b" ")
                with self.assertRaises(engine.FrozenDayError):
                    self.load()
                (self.root / path).write_bytes(original)
                (self.root / path).unlink()
                with self.assertRaises(engine.FrozenDayError):
                    self.load()
                (self.root / path).write_bytes(original)

    def test_symlink_input_rejected(self):
        path = self.root / RUNTIME
        path.unlink()
        path.symlink_to(ROOT / RUNTIME)
        with self.assertRaisesRegex(engine.FrozenDayError, "symlink"):
            self.load()

    def test_unsafe_bound_path_rejected(self):
        self.receipt["outputs"]["runtime_features_path"] = "../../anything.csv"
        self.write(RECEIPT, self.receipt)
        with self.assertRaises(engine.FrozenDayError):
            self.load()

    def test_wrong_date_after_rehash_rejected(self):
        self.p1["exec_date"] = "20260910"
        self.rebind()
        with self.assertRaisesRegex(engine.FrozenDayError, "exact D/T/T"):
            self.load()

    def test_strict_calendar_rejects_nonadjacent_sessions(self):
        for payload in (self.p0, self.p1, self.receipt):
            payload["exec_date"] = "20260910"
            payload["exit_date"] = "20260911"
        self.rebind()
        with self.assertRaisesRegex(engine.FrozenDayError, "strict SSE"):
            self.load()

    def test_changed_promotion_value_after_rehash_rejected(self):
        self.p0["rows"][0]["predicted_promotion_probability"] += .01
        self.rebind()
        with self.assertRaisesRegex(engine.FrozenDayError, "promotion probability"):
            self.load()

    def test_changed_name_after_rehash_rejected(self):
        self.p1["rows"][0]["name"] = "not frozen name"
        self.rebind()
        with self.assertRaisesRegex(engine.FrozenDayError, "candidate identity"):
            self.load()

    def test_changed_profit_permutation_after_rehash_rejected(self):
        self.p1["rows"].reverse()
        self.rebind()
        with self.assertRaisesRegex(engine.FrozenDayError, "profit ordering"):
            self.load()

    def test_changed_profit_score_after_rehash_rejected(self):
        self.p1["rows"][0]["research_joint_proxy_score"] += .001
        self.rebind()
        with self.assertRaisesRegex(engine.FrozenDayError, "profit proxy score"):
            self.load()

    def test_path_missing_remains_unknown_not_zero(self):
        row = next(r for r in self.runtime if r["ts_code"] == self.p0["rows"][0]["ts_code"])
        row.update(path_label_code="INSUFFICIENT", path_label="路径数据不足", path_strength_delta="")
        self.rebind()
        day = self.load()
        self.assertIsNone(day["rows"][0]["path_change_pct"])
        self.assertEqual(day["rows"][0]["path_label"], "路径数据不足")

    def test_real_n_never_padded(self):
        original = (copy.deepcopy(self.p0), copy.deepcopy(self.p1), copy.deepcopy(self.receipt), copy.deepcopy(self.runtime))
        for n in (0, 1, 6, 10):
            with self.subTest(n=n):
                self.p0, self.p1, self.receipt, self.runtime = copy.deepcopy(original)
                self.p0["rows"] = self.p0["rows"][:n]
                codes = {r["ts_code"] for r in self.p0["rows"]}
                self.runtime = [r for r in self.runtime if r["ts_code"] in codes]
                self.p1["rows"] = [r for r in self.p1["rows"] if r["ts_code"] in codes]
                for rank, row in enumerate(self.p1["rows"], 1):
                    row["executable_profit_research_rank"] = rank
                self.rebind()
                actual = self.load()
                self.assertEqual(len(actual["rows"]), n)
                self.assertEqual([r["promotion_rank"] for r in actual["rows"]], list(range(1, n + 1)))
                self.assertEqual(sorted(r["profit_rank"] for r in actual["rows"]), list(range(1, n + 1)))

    def test_missing_p1_row_never_fallback_to_legacy_single(self):
        self.p1["rows"].pop()
        self.rebind()
        with self.assertRaisesRegex(engine.FrozenDayError, "count"):
            self.load()

    def test_inventory_preserves_declared_bytes(self):
        for item in INVENTORY["assets"]:
            data = (ROOT / item["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), item["sha256"], item["path"])
            self.assertEqual(len(data), item["bytes"], item["path"])


if __name__ == "__main__":
    unittest.main()
