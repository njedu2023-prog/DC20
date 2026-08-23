from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scripts.validate_three_engine_five_year_ledger import (
    EXPECTED_EVENT_SEED_COLUMNS,
    EXPECTED_RUNTIME_FEATURE_COLUMNS,
    EXPECTED_RUNTIME_FEATURE_VERSION,
    PROMOTION_BAR_CONTEXT_FEATURES,
    PROMOTION_STOCK_PRIOR_FEATURES,
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
        self.calendar_path = self.root / "trade_cal_sse.csv"
        self.event_seed_path = self.root / "five_year_event_features.csv.gz"
        self.thresholds = ValidationThresholds(
            min_signal_dates=2,
            min_rows=4,
            min_class_rows=1,
            min_price_coverage=0.98,
            min_context_coverage=0.98,
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
        date_binding = {
            "20240102": ("20240103", "20240104"),
            "20240103": ("20240104", "20240105"),
        }
        buy_date, target_exit_date = date_binding[signal_date]
        row = {
            "signal_date": signal_date,
            "buy_date": buy_date,
            "target_exit_date": target_exit_date,
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
        for position, column in enumerate(
            PROMOTION_BAR_CONTEXT_FEATURES, start=1
        ):
            row[column] = float(position) / 10.0
        # Every fixture stock appears once, so its strictly-lagged Beta(2,3)
        # prior is the untouched 2/5 prior with zero prior samples.
        row["five_year_stock_prior_rate"] = 2.0 / 5.0
        row["five_year_stock_prior_samples_log"] = 0.0
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
        calendar_rows = [
            ("SSE", "20231229", "1", "20231228"),
            ("SSE", "20231230", "0", "20231229"),
            ("SSE", "20231231", "0", "20231229"),
            ("SSE", "20240101", "0", "20231229"),
            ("SSE", "20240102", "1", "20231229"),
            ("SSE", "20240103", "1", "20240102"),
            ("SSE", "20240104", "1", "20240103"),
            ("SSE", "20240105", "1", "20240104"),
        ]
        pd.DataFrame(
            calendar_rows,
            columns=["exchange", "cal_date", "is_open", "pretrade_date"],
        ).to_csv(self.calendar_path, index=False, lineterminator="\n")

        seed_rows = [
            {
                "signal_date": row["signal_date"],
                "ts_code": row["ts_code"],
                "stage": row["stage"],
                "board": row["board"],
            }
            for row in self.ledger.to_dict("records")
        ]
        # This reproduces the audited legacy source defect: a fully
        # identityless row may be quarantined only when it carries bar context
        # and carries no stock-prior values.
        seed_rows.append({PROMOTION_BAR_CONTEXT_FEATURES[0]: 0.123})
        event_seed = pd.DataFrame(seed_rows)
        for column in EXPECTED_EVENT_SEED_COLUMNS:
            if column not in event_seed.columns:
                event_seed[column] = pd.NA
        event_seed = event_seed[list(EXPECTED_EVENT_SEED_COLUMNS)]
        self._write_gzip_csv(event_seed, self.event_seed_path)

        self._write_gzip_csv(self.ledger, self.ledger_path)
        self._write_gzip_csv(self.legacy, self.legacy_path)
        calendar_sha = self._sha256(self.calendar_path)
        seed_sha = self._sha256(self.event_seed_path)
        context_coverage = {
            column: float(
                pd.to_numeric(self.ledger[column], errors="coerce").notna().mean()
            )
            for column in PROMOTION_BAR_CONTEXT_FEATURES
        }
        stock_prior_coverage = {
            column: float(
                pd.to_numeric(self.ledger[column], errors="coerce").notna().mean()
            )
            for column in PROMOTION_STOCK_PRIOR_FEATURES
        }
        self.manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": "dc20_three_engine_five_year_ledger_v2",
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
                        "event_artifact": str(self.event_seed_path.resolve()),
                        "event_sha256": seed_sha,
                        "prior_grid_truth_cutoff_rule": "strictly_before_signal_date",
                        "event_source_inventory": {
                            "seed_path": str(self.event_seed_path.resolve()),
                            "seed_sha256": seed_sha,
                            "seed_raw_sha256": seed_sha,
                            "seed_raw_rows": len(event_seed),
                            "seed_identity_rows": len(self.ledger),
                            "seed_orphan_rows_quarantined": 1,
                            "seed_partial_identity_rows": 0,
                            "seed_invalid_identity_rows": 0,
                            "seed_duplicate_identity_rows": 0,
                            "seed_raw_columns": list(EXPECTED_EVENT_SEED_COLUMNS),
                            "seed_identity_columns": [
                                "signal_date",
                                "ts_code",
                                "stage",
                                "board",
                            ],
                            "seed_columns_used": [
                                "signal_date",
                                "ts_code",
                                "stage",
                                "board",
                            ],
                            "seed_context_source_used": False,
                            "seed_orphan_rows_with_bar_context": 1,
                            "seed_orphan_rows_with_stock_prior": 0,
                            "seed_orphan_policy": (
                                "quarantine_only_when_all_identity_columns_are_empty"
                            ),
                            "seed_end_signal_date": "20240103",
                            "canonical_prediction_files": [],
                            "canonical_prediction_file_count": 0,
                            "new_eligible_rows_discovered": 0,
                        },
                        "calendar": {
                            "path": str(self.calendar_path.resolve()),
                            "sha256": calendar_sha,
                            "source": "tushare:trade_cal:SSE",
                            "exchange": "SSE",
                            "strict": True,
                            "natural_day_rows": 8,
                            "open_sessions": 5,
                            "start_cal_date": "20231229",
                            "end_cal_date": "20240105",
                            "start_open_session": "20231229",
                            "end_open_session": "20240105",
                            "pretrade_chain_validated": True,
                        },
                        "calendar_open_session_cutoff": "20240105",
                        "calendar_open_sessions_used": 5,
                        "date_binding_rule": (
                            "D/T/T+1 are adjacent strict SSE open sessions"
                        ),
                        "context_source_used": False,
                        "bar_context_rebuild_columns": list(
                            PROMOTION_BAR_CONTEXT_FEATURES
                        ),
                        "context_missingness_policy": (
                            "preserve_nan_and_model_with_median_plus_missing_indicator"
                        ),
                        "stock_prior_rule": (
                            "strictly earlier D promotion truth; Beta(2,3); "
                            "log1p(samples)"
                        ),
                    },
                    "coverage": {
                        "rebuilt_bar_context": context_coverage,
                        "rebuilt_bar_context_minimum": min(
                            context_coverage.values()
                        ),
                        "rebuilt_bar_context_gate": 0.98,
                        "rebuilt_stock_prior": stock_prior_coverage,
                        "rebuilt_stock_prior_minimum_gate": 1.0,
                        "strict_sse_date_binding_rows": len(self.ledger),
                        "strict_sse_date_binding_violations": 0,
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
            calendar_path=self.calendar_path,
            event_seed_path=self.event_seed_path,
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

    def test_legacy_manifest_schema_cannot_pass_v2_validation(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["schema_version"] = "dc20_three_engine_five_year_ledger_v1"
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        report = self.validate()
        self.assertFalse(report["valid"])
        self.assertIn("manifest_schema_v2", report["failed_gates"])

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

    def test_strict_sse_calendar_hash_and_date_adjacency_fail_closed(self) -> None:
        # A byte change keeps the calendar semantically readable but breaks
        # the pinned calendar hash in the manifest.
        self.calendar_path.write_text(
            self.calendar_path.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        report = self.validate()
        self.assertFalse(report["valid"])
        self.assertIn(
            "strict_sse_calendar_contract", report["failed_gates"]
        )

        self._write_inputs()
        self.ledger.loc[0, "buy_date"] = "20240104"
        self._write_inputs()
        report = self.validate()
        self.assertFalse(report["valid"])
        self.assertIn(
            "strict_sse_d_t_tplus1_adjacency", report["failed_gates"]
        )

    def test_event_seed_inventory_and_orphan_count_are_independently_audited(
        self,
    ) -> None:
        event_seed = pd.read_csv(self.event_seed_path, low_memory=False)
        extra_orphan = {column: pd.NA for column in EXPECTED_EVENT_SEED_COLUMNS}
        extra_orphan[PROMOTION_BAR_CONTEXT_FEATURES[1]] = 0.456
        event_seed = pd.concat(
            [event_seed, pd.DataFrame([extra_orphan])], ignore_index=True
        )
        self._write_gzip_csv(event_seed, self.event_seed_path)
        report = self.validate()
        self.assertFalse(report["valid"])
        self.assertIn("owned_event_source_inventory", report["failed_gates"])

    def test_rebuilt_context_coverage_is_a_hard_gate(self) -> None:
        self.ledger.loc[:, PROMOTION_BAR_CONTEXT_FEATURES[0]] = float("nan")
        self._write_inputs()
        report = self.validate()
        self.assertFalse(report["valid"])
        self.assertIn(
            "rebuilt_promotion_context_contract", report["failed_gates"]
        )

    def test_stock_prior_cannot_include_same_day_or_future_truth(self) -> None:
        # The first observation has promotion_hit=1.  A leaky posterior that
        # consumes that same-row truth would be (2+1)/(5+1)=0.5, while the
        # strict point-in-time prior must remain 2/5=0.4.
        self.ledger.loc[0, "five_year_stock_prior_rate"] = 0.5
        self.ledger.loc[0, "five_year_stock_prior_samples_log"] = 1.0
        self._write_inputs()
        report = self.validate()
        self.assertFalse(report["valid"])
        self.assertIn(
            "stock_prior_is_strictly_lagged", report["failed_gates"]
        )


if __name__ == "__main__":
    unittest.main()
