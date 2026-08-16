from __future__ import annotations

import math
import unittest

import pandas as pd

from top10decision.decision.canonical_fingerprint import (
    CanonicalSchemaError,
    canonical_float_token,
    canonical_frame_fingerprint,
    canonical_policy_fingerprint,
    canonical_value,
    compose_artifact_fingerprint,
    normalize_code,
    normalize_date,
    normalize_stage,
)
from scripts.diagnose_decision_fingerprint import (
    BASE_SCORE_COLUMNS,
    OOS_DISCRETE_BEHAVIOR_COLUMNS,
    SCORE_COLUMNS,
    TOP10_DISCRETE_BEHAVIOR_COLUMNS,
    _behavior_comparison,
    _score_comparison,
    _strict_frame_schema,
)


class CanonicalFingerprintTest(unittest.TestCase):
    def test_exact_text_preserves_execution_whitespace_and_unicode_form(self):
        base = canonical_value("NEUTRAL", decimals=8, kind="exact_text")
        self.assertEqual(base, "NEUTRAL")
        self.assertNotEqual(
            base,
            canonical_value(" NEUTRAL ", decimals=8, kind="exact_text"),
        )
        self.assertNotEqual(
            base,
            canonical_value("ＮＥＵＴＲＡＬ", decimals=8, kind="exact_text"),
        )

    def test_special_float_values_are_explicit_and_negative_zero_is_normalized(self):
        self.assertEqual(
            canonical_float_token(float("nan"), decimals=8),
            {"$special": "missing"},
        )
        self.assertEqual(
            canonical_float_token(None, decimals=8),
            {"$special": "missing"},
        )
        self.assertEqual(
            canonical_float_token(float("inf"), decimals=8),
            {"$special": "+inf"},
        )
        self.assertEqual(
            canonical_float_token(float("-inf"), decimals=8),
            {"$special": "-inf"},
        )
        self.assertEqual(
            canonical_float_token(-0.0, decimals=8),
            canonical_float_token(0.0, decimals=8),
        )
        self.assertEqual(
            canonical_float_token(-0.0, decimals=8),
            {"$float": "0.00000000"},
        )
        self.assertEqual(
            canonical_float_token("not-a-number", decimals=8),
            {"$special": "invalid"},
        )
        self.assertNotEqual(
            canonical_float_token("not-a-number", decimals=8),
            canonical_float_token(None, decimals=8),
        )
        self.assertEqual(
            canonical_float_token(1e308, decimals=8),
            {"$special": "invalid"},
        )

    def test_identifiers_are_normalized_without_exposing_float_dtypes(self):
        self.assertEqual(normalize_date("2026-08-05"), "20260805")
        self.assertEqual(normalize_date(20260805.0), "20260805")
        self.assertEqual(normalize_code("600001.sh"), "600001.SH")
        self.assertEqual(normalize_code(1), "000001.SZ")
        self.assertEqual(normalize_stage(" 2 -> 3 "), "2→3")
        self.assertEqual(normalize_stage("3进4"), "3→4")

    def test_frame_hash_is_order_dtype_and_tiny_float_drift_independent(self):
        columns = (
            "signal_date",
            "ts_code",
            "stage",
            "observation_rank",
            "market_fill",
            "score",
        )
        kinds = {"observation_rank": "integer", "market_fill": "integer"}
        left = pd.DataFrame(
            [
                {
                    "signal_date": "2026-08-05",
                    "ts_code": "600001.sh",
                    "stage": "2 -> 3",
                    "observation_rank": 1,
                    "market_fill": 1.0,
                    "score": 0.123456781,
                },
                {
                    "signal_date": "20260806",
                    "ts_code": "000002.SZ",
                    "stage": "3→4",
                    "observation_rank": 2,
                    "market_fill": 0,
                    "score": -0.0,
                },
            ]
        )
        right = pd.DataFrame(
            [
                {
                    "signal_date": "20260806.0",
                    "ts_code": "2",
                    "stage": "3进4",
                    "observation_rank": "2.0",
                    "market_fill": "0",
                    "score": "0.0",
                },
                {
                    "signal_date": 20260805,
                    "ts_code": "600001.SH",
                    "stage": "2-3",
                    "observation_rank": "1",
                    "market_fill": 1,
                    "score": 0.123456782,
                },
            ]
        )
        left_hash = canonical_frame_fingerprint(
            left,
            columns,
            decimals=8,
            kinds=kinds,
        )
        right_hash = canonical_frame_fingerprint(
            right,
            columns,
            decimals=8,
            kinds=kinds,
        )
        self.assertEqual(left_hash["sha256"], right_hash["sha256"])
        self.assertTrue(left_hash["row_order_independent"])

    def test_frame_hash_detects_key_row_and_material_value_changes(self):
        columns = ("signal_date", "ts_code", "score")
        base = pd.DataFrame(
            [{"signal_date": "20260805", "ts_code": "600001.SH", "score": 0.25}]
        )
        changed_score = base.copy()
        changed_score.loc[0, "score"] = 0.250001
        changed_key = base.copy()
        changed_key.loc[0, "ts_code"] = "600002.SH"
        fingerprint = canonical_frame_fingerprint(base, columns, decimals=8)["sha256"]
        self.assertNotEqual(
            fingerprint,
            canonical_frame_fingerprint(changed_score, columns, decimals=8)["sha256"],
        )
        self.assertNotEqual(
            fingerprint,
            canonical_frame_fingerprint(changed_key, columns, decimals=8)["sha256"],
        )

    def test_strict_frame_schema_rejects_missing_invalid_and_nonintegral(self):
        missing_column = pd.DataFrame(
            [{"signal_date": "20260805", "ts_code": "600001.SH"}]
        )
        invalid_float = pd.DataFrame(
            [
                {
                    "signal_date": "20260805",
                    "ts_code": "600001.SH",
                    "score": "bad-number",
                }
            ]
        )
        nonintegral = pd.DataFrame(
            [
                {
                    "signal_date": "20260805",
                    "ts_code": "600001.SH",
                    "rank": 1.5,
                }
            ]
        )
        with self.assertRaises(CanonicalSchemaError):
            canonical_frame_fingerprint(
                missing_column,
                ("signal_date", "ts_code", "score"),
                decimals=8,
            )
        with self.assertRaises(CanonicalSchemaError):
            canonical_frame_fingerprint(
                invalid_float,
                ("signal_date", "ts_code", "score"),
                decimals=8,
            )
        with self.assertRaises(CanonicalSchemaError):
            canonical_frame_fingerprint(
                nonintegral,
                ("signal_date", "ts_code", "rank"),
                decimals=8,
                kinds={"rank": "integer"},
            )
        report = canonical_frame_fingerprint(
            invalid_float,
            ("signal_date", "ts_code", "score"),
            decimals=8,
            strict=False,
        )
        self.assertFalse(report["valid"])
        self.assertEqual(report["invalid_cell_count"], 1)

    def test_non_identifier_text_requires_and_honors_an_explicit_kind(self):
        columns = ("signal_date", "ts_code", "regime_label")
        left = pd.DataFrame(
            [
                {
                    "signal_date": "20260805",
                    "ts_code": "600001.SH",
                    "regime_label": " 风险优先 ",
                }
            ]
        )
        right = left.copy()
        right.loc[0, "regime_label"] = "风险优先"
        kinds = {"regime_label": "text"}
        self.assertEqual(
            canonical_frame_fingerprint(
                left, columns, decimals=8, kinds=kinds
            )["sha256"],
            canonical_frame_fingerprint(
                right, columns, decimals=8, kinds=kinds
            )["sha256"],
        )
        right.loc[0, "regime_label"] = "风险偏好"
        self.assertNotEqual(
            canonical_frame_fingerprint(
                left, columns, decimals=8, kinds=kinds
            )["sha256"],
            canonical_frame_fingerprint(
                right, columns, decimals=8, kinds=kinds
            )["sha256"],
        )

    def test_executable_policy_hash_excludes_diagnostic_metrics(self):
        policy = {
            "version": "selector-v2",
            "ready": False,
            "reason": "failed_gate",
            "max_positions": 2,
            "tail_risk_weight": 0.25,
            "thresholds": {
                "min_trade_score": 0.123456781,
                "min_mean_return_lcb": -0.01,
                "min_fill_probability": 0.2,
                "max_big_loss_probability": 0.4,
            },
            "checks": {"profit": False},
            "metrics": {"mean": -0.002},
            "evaluated_policies": 48,
        }
        changed_diagnostics = {
            **policy,
            "checks": {"profit": True},
            "metrics": {"mean": 99.0},
            "evaluated_policies": 96,
        }
        changed_threshold = {
            **policy,
            "thresholds": {
                **policy["thresholds"],
                "min_trade_score": 0.1235,
            },
        }
        base = canonical_policy_fingerprint(policy, decimals=8)
        diagnostic = canonical_policy_fingerprint(changed_diagnostics, decimals=8)
        material = canonical_policy_fingerprint(changed_threshold, decimals=8)
        self.assertEqual(base["sha256"], diagnostic["sha256"])
        self.assertNotEqual(base["sha256"], material["sha256"])
        self.assertNotIn("metrics", base["projection"])

    def test_composed_hash_separates_provenance_semantics_and_policy(self):
        baseline = compose_artifact_fingerprint(
            artifact_kind="selector",
            provenance_sha256="a" * 64,
            semantic_sha256="b" * 64,
            policy_sha256="c" * 64,
            decimals=8,
        )
        self.assertEqual(len(baseline), 64)
        for field, value in (
            ("provenance_sha256", "d" * 64),
            ("semantic_sha256", "d" * 64),
            ("policy_sha256", "d" * 64),
        ):
            arguments = {
                "artifact_kind": "selector",
                "provenance_sha256": "a" * 64,
                "semantic_sha256": "b" * 64,
                "policy_sha256": "c" * 64,
                "decimals": 8,
            }
            arguments[field] = value
            self.assertNotEqual(baseline, compose_artifact_fingerprint(**arguments))

    def test_behavior_comparison_uses_unique_date_code_identity(self):
        row = {
            "signal_date": "2026-08-05",
            "ts_code": "600001.sh",
            "stage": "2 -> 3",
            "observation_rank": 1,
            "shadow_rank": 2,
            "shadow_selected": 1,
            "selected": 0,
            "model_reason": "selection_policy_not_ready",
            "gate_policy_ready": 0,
            "gate_stage_focus": 1,
            "gate_exit_probability": 1,
            "gate_fill_probability": 1,
            "gate_big_loss_probability": 1,
            "gate_mean_return_lcb": 1,
            "gate_conservative_ev": 1,
            "gate_selection_score": 0,
            "risk_gate_pass": 0,
        }
        reference = pd.DataFrame([row])
        fresh = pd.DataFrame([{**row, "signal_date": 20260805}])
        equal = _behavior_comparison(
            reference, fresh, TOP10_DISCRETE_BEHAVIOR_COLUMNS
        )
        self.assertEqual(equal["status"], "compared")
        self.assertTrue(equal["all_equal"])
        self.assertNotIn("common_keys", equal)

        changed = fresh.copy()
        changed.loc[0, "observation_rank"] = 2
        comparison = _behavior_comparison(
            reference, changed, TOP10_DISCRETE_BEHAVIOR_COLUMNS
        )
        self.assertEqual(comparison["changed_count"], 1)
        self.assertEqual(comparison["changed_by_column"]["observation_rank"], 1)
        self.assertFalse(comparison["all_equal"])

        changed_gate = fresh.copy()
        changed_gate.loc[0, "gate_policy_ready"] = 1
        gate_comparison = _behavior_comparison(
            reference, changed_gate, TOP10_DISCRETE_BEHAVIOR_COLUMNS
        )
        self.assertEqual(gate_comparison["changed_count"], 1)
        self.assertEqual(
            gate_comparison["changed_by_column"]["gate_policy_ready"], 1
        )

        invalid_gate = fresh.copy()
        invalid_gate["gate_policy_ready"] = invalid_gate[
            "gate_policy_ready"
        ].astype(object)
        invalid_gate.loc[0, "gate_policy_ready"] = "not-a-boolean"
        invalid_comparison = _behavior_comparison(
            reference, invalid_gate, TOP10_DISCRETE_BEHAVIOR_COLUMNS
        )
        self.assertEqual(invalid_comparison["status"], "schema_error")
        self.assertEqual(invalid_comparison["invalid_behavior_cell_count"], 1)

    def test_score_comparison_tracks_numeric12_and_missing_state(self):
        reference = pd.DataFrame(
            [
                {
                    "signal_date": "20260805",
                    "ts_code": "600001.SH",
                    "diagnostic_gap": None,
                    "recommended_max_gap": 0.1,
                }
            ]
        )
        fresh = pd.DataFrame(
            [
                {
                    "signal_date": "2026-08-05",
                    "ts_code": "600001.sh",
                    "diagnostic_gap": 0.0,
                    "recommended_max_gap": 0.100000000001,
                }
            ]
        )
        comparison = _score_comparison(reference, fresh)
        self.assertEqual(comparison["status"], "compared")
        self.assertEqual(
            comparison["numeric_delta"]["diagnostic_gap"][
                "missing_state_mismatches"
            ],
            1,
        )
        self.assertGreater(comparison["precision"]["12"]["changed_rows"], 0)
        self.assertIn("diagnostic_gap", BASE_SCORE_COLUMNS)
        self.assertIn("recommended_max_gap", BASE_SCORE_COLUMNS)
        self.assertIn(
            "trade_selector_policy_ready", OOS_DISCRETE_BEHAVIOR_COLUMNS
        )
        invalid_numeric = fresh.astype(object)
        invalid_numeric.loc[0, "diagnostic_gap"] = "bad-number"
        invalid_comparison = _score_comparison(reference, invalid_numeric)
        self.assertEqual(invalid_comparison["status"], "schema_error")
        self.assertIn(
            "diagnostic_gap", invalid_comparison["invalid_numeric_columns"]
        )

    def test_oos_schema_rejects_missing_diagnostic_gap(self):
        complete_row = {
            "signal_date": "20260805",
            "ts_code": "600001.SH",
            **{column: 0.0 for column in SCORE_COLUMNS},
        }
        row = dict(complete_row)
        row.pop("diagnostic_gap")
        report = _strict_frame_schema(
            pd.DataFrame([row]),
            required_behavior_columns=(),
            required_numeric_columns=SCORE_COLUMNS,
        )
        self.assertFalse(report["valid"])
        self.assertIn("diagnostic_gap", report["missing_columns"])
        self.assertNotIn("recommended_max_gap", report["missing_columns"])
        comparison = _score_comparison(
            pd.DataFrame([row]),
            pd.DataFrame([complete_row]),
            SCORE_COLUMNS,
        )
        self.assertEqual(comparison["status"], "schema_error")
        self.assertIn(
            "diagnostic_gap",
            comparison["missing_reference_numeric_columns"],
        )

    def test_behavior_comparison_refuses_duplicate_or_empty_identity(self):
        row = {
            "signal_date": "20260805",
            "ts_code": "600001.SH",
            "stage": "2→3",
            "observation_rank": 1,
            "shadow_rank": 2,
            "shadow_selected": 1,
            "selected": 0,
            "model_reason": "selection_policy_not_ready",
            "gate_policy_ready": 0,
            "gate_stage_focus": 1,
            "gate_exit_probability": 1,
            "gate_fill_probability": 1,
            "gate_big_loss_probability": 1,
            "gate_mean_return_lcb": 1,
            "gate_conservative_ev": 1,
            "gate_selection_score": 0,
            "risk_gate_pass": 0,
        }
        reference = pd.DataFrame([row, row])
        fresh = pd.DataFrame([row])
        duplicate = _behavior_comparison(
            reference, fresh, TOP10_DISCRETE_BEHAVIOR_COLUMNS
        )
        self.assertEqual(duplicate["status"], "ambiguous_duplicate_identity")
        self.assertFalse(duplicate["comparison_performed"])
        score_duplicate = _score_comparison(reference, fresh)
        self.assertEqual(
            score_duplicate["status"], "ambiguous_duplicate_identity"
        )
        self.assertFalse(score_duplicate["comparison_performed"])
        self.assertNotIn("common_keys", score_duplicate)

        empty = pd.DataFrame([{**row, "ts_code": ""}])
        invalid = _behavior_comparison(
            empty, fresh, TOP10_DISCRETE_BEHAVIOR_COLUMNS
        )
        self.assertEqual(invalid["status"], "invalid_identity")
        self.assertFalse(invalid["comparison_performed"])


if __name__ == "__main__":
    unittest.main()
