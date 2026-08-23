from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scripts.validate_three_engine_five_year_ledger import (
    EXPECTED_RUNTIME_FEATURE_COLUMNS,
    EXPECTED_RUNTIME_FEATURE_VERSION,
    ValidationThresholds,
    _json_text,
    validate_three_engine_five_year_ledger,
)


class ThreeEngineFiveYearLedgerValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.ledger_path = self.root / "five_year_supervised_ledger.csv.gz"
        self.manifest_path = self.root / "five_year_ledger_manifest.json"
        self.legacy_path = self.root / "decision_v12_frozen_history.csv.gz"
        self.thresholds = ValidationThresholds(
            min_signal_dates=2,
            min_rows=4,
            min_class_rows=1,
            min_price_coverage=0.98,
            min_legacy_overlap_rows=4,
            min_promotion_agreement=0.99,
            min_return_label_agreement=0.95,
        )
        self.ledger = pd.DataFrame(
            [
                self._ledger_row("20240102", "000001.SZ", 2, 1, 0, 1, 1),
                self._ledger_row("20240102", "600001.SH", 3, 0, 1, 0, 1),
                self._ledger_row("20240103", "000002.SZ", 2, 1, 1, 0, 0),
                self._ledger_row("20240103", "600002.SH", 3, 0, 0, 1, 1),
            ]
        )
        self.legacy = pd.DataFrame(
            [
                self._legacy_row(row, legacy_fill=1 - int(row["market_fill"]))
                for row in self.ledger.to_dict("records")
            ]
        )
        self._write_inputs()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _ledger_row(
        signal_date: str,
        ts_code: str,
        stage: int,
        promotion: int,
        big_loss: int,
        profit: int,
        market_fill: int,
    ) -> dict:
        row = {
            "signal_date": signal_date,
            "ts_code": ts_code,
            "stage": stage,
            "board": "SH_MAIN" if ts_code.endswith(".SH") else "SZ_MAIN",
            "mechanism_limit_pct": 10.0,
            "promotion_hit": promotion,
            "big_loss_hit": big_loss,
            "profit_hit": profit,
            "market_fill": market_fill,
            "d_open": 10.0,
            "d_close": 11.0,
            "d_high": 11.0,
            "d_low": 9.9,
            "t_open": 11.1,
            "t_close": 11.5,
            "t_high": 12.1,
            "t_low": 11.0,
            "tplus1_open": 11.3,
        }
        for position, column in enumerate(EXPECTED_RUNTIME_FEATURE_COLUMNS, start=1):
            row[column] = float(position) / 100.0
        return row

    @staticmethod
    def _legacy_row(row: dict, *, legacy_fill: int) -> dict:
        return {
            "signal_date": row["signal_date"],
            "ts_code": row["ts_code"],
            "continuation_limit_up_hit": row["promotion_hit"],
            "big_loss_hit": row["big_loss_hit"],
            "profit_hit": row["profit_hit"],
            "market_fill": legacy_fill,
            "history_source": "tushare_compact_backfill",
            "actual_order_fill_observed": 0,
            "actual_order_fill": pd.NA,
        }

    @staticmethod
    def _write_gzip_csv(frame: pd.DataFrame, path: Path) -> None:
        payload = frame.to_csv(index=False, lineterminator="\n").encode("utf-8")
        with path.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
                compressed.write(payload)

    @staticmethod
    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _write_inputs(self) -> None:
        self._write_gzip_csv(self.ledger, self.ledger_path)
        self._write_gzip_csv(self.legacy, self.legacy_path)
        self.manifest_path.write_text(
            json.dumps(
                {
                    "owner": "njedu2023-prog/DC20",
                    "runtime_dependency_on_top10_decision": False,
                    "ledger_sha256": self._sha256(self.ledger_path),
                    "target_contract": {
                        "market_fill": "public market feasibility proxy"
                    },
                    "runtime_feature_contract": {
                        "version": EXPECTED_RUNTIME_FEATURE_VERSION,
                        "columns": list(EXPECTED_RUNTIME_FEATURE_COLUMNS),
                        "available_by_d_close": True,
                        "future_columns_used": [],
                    },
                    "source": {
                        "prior_grid_truth_cutoff_rule": "strictly_before_signal_date",
                        "event_source_inventory": {
                            "seed_path": "data/auction_v3/promotion_prior/five_year_event_features.csv.gz",
                            "seed_sha256": "1" * 64,
                            "seed_end_signal_date": "20240103",
                            "canonical_prediction_files": [],
                            "canonical_prediction_file_count": 0,
                            "new_eligible_rows_discovered": 0,
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

    def validate(self) -> dict:
        return validate_three_engine_five_year_ledger(
            self.ledger_path,
            self.manifest_path,
            self.legacy_path,
            thresholds=self.thresholds,
        )

    def test_passes_all_hard_gates_and_reports_fill_proxy_conflicts(self) -> None:
        report = self.validate()
        self.assertTrue(report["valid"])
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["failed_gates"], [])
        self.assertEqual(report["legacy_overlap"]["rows"], 4)
        self.assertEqual(
            report["legacy_overlap"]["agreements"]["promotion_hit"]["agreement"],
            1.0,
        )
        diagnostic = report["market_fill_diagnostic"]
        self.assertEqual(diagnostic["conflict_rows"], 4)
        self.assertFalse(diagnostic["conflict_is_hard_gate"])
        self.assertFalse(diagnostic["actual_order_claimed_by_this_report"])
        self.assertEqual(
            diagnostic["semantic"],
            "public_market_feasibility_proxy_not_actual_order_fill",
        )
        # The strict serializer rejects NaN and Infinity by contract.
        json.loads(_json_text(report))

    def test_tampered_ledger_sha_fails_closed(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["ledger_sha256"] = "0" * 64
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        report = self.validate()
        self.assertFalse(report["valid"])
        self.assertIn("ledger_sha256_matches_manifest", report["failed_gates"])

    def test_duplicate_identity_and_wrong_stage_are_hard_failures(self) -> None:
        duplicate = self.ledger.iloc[[0]].copy()
        duplicate.loc[:, "stage"] = 4
        self.ledger = pd.concat([self.ledger, duplicate], ignore_index=True)
        self._write_inputs()
        report = self.validate()
        self.assertFalse(report["valid"])
        self.assertIn("signal_date_code_identity_unique", report["failed_gates"])
        self.assertIn("exact_stage_2_and_3_universe", report["failed_gates"])

    def test_owner_and_runtime_independence_are_exact_contracts(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["owner"] = "njedu2023-prog/top10-decision"
        manifest["runtime_dependency_on_top10_decision"] = None
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        report = self.validate()
        self.assertFalse(report["valid"])
        self.assertIn("owner_is_dc20", report["failed_gates"])
        self.assertIn(
            "no_top10_decision_runtime_dependency", report["failed_gates"]
        )

    def test_runtime_feature_manifest_and_coverage_fail_closed(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["runtime_feature_contract"]["columns"] = ["returns_1d"]
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        report = self.validate()
        self.assertFalse(report["valid"])
        self.assertIn("runtime_feature_manifest_exact", report["failed_gates"])

        self._write_inputs()
        self.ledger.loc[:, "volatility_20d"] = float("nan")
        self._write_inputs()
        report = self.validate()
        self.assertFalse(report["valid"])
        self.assertIn("runtime_feature_coverage", report["failed_gates"])


if __name__ == "__main__":
    unittest.main()
