from __future__ import annotations

import csv
import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import top10decision.decision.model_freeze as freeze_module
from scripts import diagnose_decision_fingerprint as diagnose

from top10decision.decision.canonical_fingerprint import (
    CANONICAL_FINGERPRINT_SCHEMA,
    canonical_frame_fingerprint,
    canonical_mapping_sha256,
    canonical_policy_fingerprint,
    compose_artifact_fingerprint,
)
from top10decision.decision.model_freeze import (
    ACTION_WATCHLIST_COLUMNS,
    DecisionModelFreezeError,
    IDENTITY_COLUMNS,
    KNOWN_REFERENCE_EVIDENCE,
    OOS_DISCRETE_BEHAVIOR_COLUMNS,
    OOS_SCORE_COLUMNS,
    REQUIRED_ACTIVE_PIN_PATHS,
    TOP10_DISCRETE_BEHAVIOR_COLUMNS,
    TOP10_SCORE_COLUMNS,
    compute_action_watchlist_fingerprint,
    compute_behavior_fingerprints,
    frame_columns_sha256,
    load_frozen_history_snapshot,
    load_model_freeze,
    load_verified_frozen_history_snapshot,
    validate_action_plan_artifact,
    validate_behavior_artifacts,
    validate_pinned_files,
    validate_runtime_artifacts,
)


MODEL_THRESHOLDS = {
    "max_big_loss_probability": 0.35,
    "min_mean_return_lcb": -0.01,
    "min_fill_probability": 0.2,
    "min_exit_probability": 0.3,
    "min_conservative_ev": -0.02,
    "min_selection_score": 0.15,
}
SELECTOR_THRESHOLDS = {
    "min_trade_score": 0.1,
    "min_mean_return_lcb": -0.01,
    "min_fill_probability": 0.2,
    "max_big_loss_probability": 0.35,
}
REVIEWED_ACTIVATION_SOURCE_FILES = {
    "src/top10decision/auction_v3/engine.py": (
        "68b39867e22761809c5636c1274013040e71555149193c0856ad44a51ec0d5d8"
    ),
    "src/top10decision/decision/trade_selector.py": (
        "7934daec17dd50b5e702878fbe7e9ac35f894eff20daef0bedbe29712b182858"
    ),
    "src/top10decision/decision/canonical_fingerprint.py": (
        "61024fb51f54eb1a90af59baf6330eee16dbe9678167e96fcf5940b51a4cc58b"
    ),
    "src/top10decision/decision/action_plan.py": (
        "f692ca531a704300baf9c75126900211fc1956ec09ad342a4f9d4a9d8133b751"
    ),
    "scripts/publish_decision_action.py": (
        "1b0118ce8fddc52dbb0d6fef92a92501dc658e958f6a9731c7f89d033b6ed275"
    ),
    "scripts/replay_frozen_canonical_v2.py": (
        "36b44f1b00e1c611c8036a257b872a1a7a5ae14b80fce9d3105fd12ff63cb3b5"
    ),
}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _public_evidence_shape_fixture() -> dict:
    model = _layer("model", "model-canonical-v2", "a")
    selector = _layer("trade_selector", "selector-canonical-v2", "c")
    # This fixture represents the already reviewed activation run, not the
    # current worktree.  Keeping its exact source hashes here ensures a later
    # source-only repair cannot silently rewrite historical CI evidence.
    reviewed_source_files = dict(REVIEWED_ACTIVATION_SOURCE_FILES)
    assert tuple(reviewed_source_files) == diagnose.ACTIVATION_SOURCE_PATHS
    assert (
        diagnose._activation_source6_sha256(reviewed_source_files)
        == diagnose.EXPECTED_ACTIVATION_SOURCE6_SHA256
    )

    def prediction_projection(layer: dict) -> dict:
        contract = layer["canonical_contract"]
        return {
            "canonical_v2_version": layer["canonical_v2_version"],
            "artifact_v2_sha256": layer["artifact_v2_sha256"],
            "canonical_schema": contract["schema"],
            "canonical_decimals": contract["decimals"],
            "execution_numeric_mode": contract["execution_mode"],
            "raw_execution_preserved": contract["raw_execution_preserved"],
        }

    def ledger(path: str, rows: int, dates: int, discrete, scores) -> dict:
        return {
            "path": path,
            "rows": rows,
            "signal_dates": dates,
            "score_decimals": 8,
            "identity_columns": list(IDENTITY_COLUMNS),
            "discrete_columns": list(discrete),
            "score_columns": list(scores),
            "identity_sha256": "1" * 64,
            "date_counts_sha256": "2" * 64,
            "discrete_sha256": "3" * 64,
            "scores_sha256": "4" * 64,
        }
    reference = dict(KNOWN_REFERENCE_EVIDENCE)
    prediction_rows = 92
    observation_domain_rows = 10
    outside_domain_rows = prediction_rows - observation_domain_rows
    persisted_counts = copy.deepcopy(diagnose.EXPECTED_FROZEN_BEHAVIOR_COUNTS)
    persisted_counts["action_watchlist"] = {
        "rows": observation_domain_rows,
        "shadow_only_rows": 2,
        "reject_rows": observation_domain_rows - 2,
        "formal_buy_count": 0,
    }
    return {
        "schema_version": diagnose.ACTIVATION_EVIDENCE_SCHEMA,
        "system": "DC2.0",
        "read_only": True,
        "ci": {
            "github_actions": True,
            "candidate_sha": "5" * 40,
            "github_run_id": "31961734392",
            "github_run_attempt": "1",
            "runner_os": "Linux",
            "event_name": "workflow_dispatch",
            "repository": "njedu2023-prog/DC20",
            "ref": "refs/heads/main",
        },
        "candidate_source": {
            "schema": diagnose.ACTIVATION_SOURCE6_SCHEMA,
            "candidate_commit": "5" * 40,
            "paths": list(diagnose.ACTIVATION_SOURCE_PATHS),
            "file_sha256": reviewed_source_files,
            "sha256": diagnose.EXPECTED_ACTIVATION_SOURCE6_SHA256,
        },
        "history_snapshot": {
            "manifest_schema_version": "decision_model_freeze_v1",
            "manifest_active_on_disk": False,
            "manifest_content_sha256": diagnose.LEGACY_DIAGNOSTIC_MANIFEST_SHA256,
            **diagnose.EXPECTED_HISTORY_EVIDENCE,
            "rows": freeze_module.KNOWN_HISTORY_ROWS,
            "source": "legacy_v1_exact_diagnostic_bootstrap",
            "loader_contract": "one_time_exact_v1_no_live_fallback",
            "path": freeze_module.KNOWN_HISTORY_PATH,
            "sha256": freeze_module.KNOWN_HISTORY_SHA256,
            "forced_frozen_replay": True,
            "manifest_mutated_on_disk": False,
            "live_history_fallback": False,
            "pinned_files": None,
        },
        "reference_evidence": reference,
        "canonical_v2": {
            "schema_version": freeze_module.CANONICAL_RUNTIME_SCHEMA_VERSION,
            "model": model,
            "trade_selector": selector,
            "surface_consistency": {
                "model_meta_backtest_exact": True,
                "selector_meta_backtest_exact": True,
                "action_model_exact": True,
                "action_selector_exact": True,
                "prediction_model": prediction_projection(model),
                "prediction_trade_selector": prediction_projection(selector),
                "prediction_trade_selector_domain": {
                    "observation_domain_rows": observation_domain_rows,
                    "outside_domain_rows": outside_domain_rows,
                    "global_selector_v2_declarations_match": True,
                    "domain_v2_artifact_manifest_match": True,
                    "domain_v1_artifact_same_run_match": True,
                    "domain_v1_artifact_sha256": "9" * 64,
                    "outside_selector_artifacts_empty": True,
                    "outside_trade_semantics_valid": True,
                    "formal_trade_selected_count": 0,
                    "trade_selector_promoted_count": 0,
                    "shadow_selected_count": 2,
                },
                "prediction_fill_relationships": {
                    "rows": prediction_rows,
                    "public_fill_equals_fill": True,
                    "trade_public_fill_equals_trade_fill": True,
                    "trade_fill_observation_domain_rows": observation_domain_rows,
                    "trade_fill_outside_domain_rows": outside_domain_rows,
                    "actual_fill_available_rows": 0,
                    "actual_fill_missing_rows": prediction_rows,
                },
            },
        },
        "behavior_contract": {
            "schema_version": freeze_module.BEHAVIOR_SCHEMA_VERSION,
            "canonical_schema": CANONICAL_FINGERPRINT_SCHEMA,
            "top10": ledger(
                diagnose.TOP10_EVIDENCE_PATH,
                freeze_module.KNOWN_TOP10_ROWS,
                freeze_module.KNOWN_TOP10_DATES,
                TOP10_DISCRETE_BEHAVIOR_COLUMNS,
                TOP10_SCORE_COLUMNS,
            ),
            "trade_selector_oos": ledger(
                diagnose.OOS_EVIDENCE_PATH,
                freeze_module.KNOWN_OOS_ROWS,
                freeze_module.KNOWN_OOS_DATES,
                OOS_DISCRETE_BEHAVIOR_COLUMNS,
                OOS_SCORE_COLUMNS,
            ),
            "action_watchlist": {
                "path": "outputs/decision/action_plan_latest.json",
                "rows": observation_domain_rows,
                "columns": list(ACTION_WATCHLIST_COLUMNS),
                "sha256": "a" * 64,
                "unique_codes": True,
                "shadow_only_rows": 2,
            },
            "reference_evidence": reference,
            "nested_oos_research": {
                "all_candidates_path": "trade_selector.formal_policy_oos.all_candidates",
                "signals": 158,
                "signal_dates": 119,
                "filled_trades": 158,
                "market_buyable_path": "trade_selector.formal_policy_oos.market_buyable_only",
                "market_buyable_filled_trades": 25,
            },
            "decision": {
                "status_code": "NO_TRADE_MODEL_NOT_PROMOTED",
                "formal_buy_count": 0,
                "top10_selected_count": 0,
                "selector_globally_promoted_count": 0,
                "nested_oos_trade_selected_count": 158,
                "nested_oos_trade_selector_promoted_count": 3097,
                "production_backtest_signals": 0,
                "production_backtest_signal_dates": 0,
                "production_backtest_fills": 0,
                "reason_values": ["selection_policy_not_ready"],
            },
            "persisted_counts": persisted_counts,
        },
        "canonical_precision": {
            precision: {
                "gate": "hard" if precision == "8" else "audit_only",
                "top10_equal": True,
                "top10_reference_sha256": "b" * 64,
                "top10_candidate_sha256": "b" * 64,
                "selector_oos_equal": True,
                "selector_oos_reference_sha256": "c" * 64,
                "selector_oos_candidate_sha256": "c" * 64,
            }
            for precision in ("6", "8", "10", "12")
        },
    }


def _layer(layer: str, version: str, seed: str) -> dict:
    contract = {
        "schema": CANONICAL_FINGERPRINT_SCHEMA,
        "layer": layer,
        "decimals": 8,
        "rounding": "decimal_string_half_even",
        "execution_mode": "raw_float64",
        "raw_execution_preserved": True,
    }
    if layer == "model":
        raw_projection = {
            "version": "model-policy-v2",
            "ready": False,
            "reason": "selection_policy_not_ready",
            "max_positions": 2,
            "thresholds": MODEL_THRESHOLDS,
        }
        artifact_kind = "decision_model_canonical_runtime_v2"
    else:
        raw_projection = {
            "version": "selector-policy-v2",
            "ready": False,
            "reason": "selection_policy_not_ready",
            "max_positions": 2,
            "tail_risk_weight": 0.25,
            "thresholds": SELECTOR_THRESHOLDS,
        }
        artifact_kind = "decision_trade_selector_canonical_runtime_v2"
    projection = raw_projection
    provenance = seed * 64
    semantic = chr(ord(seed) + 1) * 64
    policy_sha = (
        canonical_mapping_sha256(
            {
                "schema": CANONICAL_FINGERPRINT_SCHEMA,
                "artifact_kind": "decision_model_executable_policy",
                "projection": projection,
            },
            decimals=8,
            exact_strings=True,
        )
        if layer == "model"
        else canonical_policy_fingerprint(projection, decimals=8)["sha256"]
    )
    artifact = compose_artifact_fingerprint(
        artifact_kind=artifact_kind,
        provenance_sha256=provenance,
        semantic_sha256=semantic,
        policy_sha256=policy_sha,
        decimals=8,
    )
    fingerprint = {
        "schema": CANONICAL_FINGERPRINT_SCHEMA,
        "canonical_version": version,
        "canonical_contract": contract,
        "provenance_sha256": provenance,
        "semantic_sha256": semantic,
        "policy_sha256": policy_sha,
        "policy_projection": projection,
        "artifact_sha256": artifact,
        "schema_valid": True,
        "missing_columns": [],
        "invalid_cell_count": 0,
    }
    return {
        "canonical_v2_version": version,
        "artifact_v2_sha256": artifact,
        "fingerprint_v2": fingerprint,
        "canonical_contract": contract,
    }


def _base_row(signal_date: str, code: str, rank: int) -> dict:
    row = {
        "signal_date": signal_date,
        "ts_code": code,
        "stage": "2→3",
        "stage_focus": 1,
        "policy_max_positions": 2,
        "observation_rank": rank,
        "observation_selected": 1,
        "observation_risk_tier": 1,
        "observation_risk_label": "低风险观察",
        "shadow_rank": rank,
        "shadow_selected": 1,
        "selected": 0,
        "model_reason": "selection_policy_not_ready",
        "selection_policy_version": "model-policy-v2",
        "gate_policy_ready": 0,
        "gate_stage_focus": 1,
        "gate_exit_probability": 1,
        "gate_fill_probability": 1,
        "gate_big_loss_probability": 1,
        "gate_mean_return_lcb": 1,
        "gate_conservative_ev": 1,
        "gate_selection_score": 1,
        "risk_gate_pass": 0,
        "recommended_max_gap": None,
        "diagnostic_gap": 0.03 + rank / 1000,
    }
    for index, name in enumerate(TOP10_SCORE_COLUMNS):
        if name not in row:
            row[name] = 0.1 + rank / 100 + index / 10000
    for name, value in MODEL_THRESHOLDS.items():
        row[f"policy_{name}"] = value
    return row


def _top10_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            _base_row("20260804", "600001.SH", 1),
            _base_row("20260805", "000002.SZ", 2),
        ]
    )


def _oos_frame() -> pd.DataFrame:
    rows = []
    for index, (date, code) in enumerate(
        (("20260804", "600001.SH"), ("20260805", "000002.SZ")), start=1
    ):
        row = _base_row(date, code, index)
        row.update(
            {
                "promotion_rank": index,
                "trade_rank": index,
                "trade_gate_pass": 1,
                "trade_selected": int(index == 1),
                "trade_shadow_selected": int(index == 1),
                "trade_model_reason": "selector_not_promoted",
                "trade_selector_promoted": 1,
                "trade_selector_globally_promoted": 0,
                "trade_selector_policy_ready": 0,
            }
        )
        for score_index, name in enumerate(OOS_SCORE_COLUMNS):
            if name not in row:
                row[name] = 0.2 + index / 100 + score_index / 10000
        rows.append(row)
    return pd.DataFrame(rows)


class FreezeV2Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.model_layer = _layer("model", "model-canonical-v2", "a")
        self.selector_layer = _layer(
            "trade_selector", "selector-canonical-v2", "c"
        )
        self.top10 = _top10_frame()
        self.oos = _oos_frame()
        self.top10_path = (
            self.root / "outputs/auction_v3/metrics/backtest_top10_latest.csv"
        )
        self.oos_path = (
            self.root
            / "outputs/auction_v3/metrics/backtest_trade_selector_oos_latest.csv"
        )
        self.top10_path.parent.mkdir(parents=True, exist_ok=True)
        self.top10.to_csv(self.top10_path, index=False)
        self.oos.to_csv(self.oos_path, index=False)

        self.history = pd.DataFrame(
            {
                "signal_date": ["20260804", "20260805"],
                "ts_code": ["600001.SH", "000002.SZ"],
                "net_return": [0.01, -0.02],
            }
        )
        self.history_path = self.root / "models/frozen_history.csv.gz"
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.history.to_csv(
            self.history_path,
            index=False,
            compression={"method": "gzip", "compresslevel": 9, "mtime": 0},
        )
        history_sha = hashlib.sha256(self.history_path.read_bytes()).hexdigest()

        top_contract = {
            "path": self.top10_path.relative_to(self.root).as_posix(),
            "rows": len(self.top10),
            "signal_dates": 2,
            "score_decimals": 8,
            "identity_columns": list(IDENTITY_COLUMNS),
            "discrete_columns": list(TOP10_DISCRETE_BEHAVIOR_COLUMNS),
            "score_columns": list(TOP10_SCORE_COLUMNS),
            "identity_sha256": "0" * 64,
            "date_counts_sha256": "0" * 64,
            "discrete_sha256": "0" * 64,
            "scores_sha256": "0" * 64,
        }
        top_contract.update(
            {
                key: value
                for key, value in compute_behavior_fingerprints(
                    self.top10, top_contract, context="fixture.top10"
                ).items()
                if key.endswith("_sha256")
            }
        )
        oos_contract = {
            "path": self.oos_path.relative_to(self.root).as_posix(),
            "rows": len(self.oos),
            "signal_dates": 2,
            "score_decimals": 8,
            "identity_columns": list(IDENTITY_COLUMNS),
            "discrete_columns": list(OOS_DISCRETE_BEHAVIOR_COLUMNS),
            "score_columns": list(OOS_SCORE_COLUMNS),
            "identity_sha256": "0" * 64,
            "date_counts_sha256": "0" * 64,
            "discrete_sha256": "0" * 64,
            "scores_sha256": "0" * 64,
        }
        oos_contract.update(
            {
                key: value
                for key, value in compute_behavior_fingerprints(
                    self.oos, oos_contract, context="fixture.oos"
                ).items()
                if key.endswith("_sha256")
            }
        )
        self.action = self._action_payload()
        watch_contract = {
            "path": "outputs/decision/action_plan_latest.json",
            "rows": len(self.action["stage_watchlist"]),
            "columns": list(ACTION_WATCHLIST_COLUMNS),
            "sha256": "0" * 64,
        }
        watch_contract.update(
            compute_action_watchlist_fingerprint(self.action, watch_contract)
        )
        self.manifest = {
            "schema_version": "decision_model_freeze_v2",
            "active": False,
            "freeze_id": "fixture-freeze-v2",
            "training_cutoff_signal_date": "20260805",
            "history_snapshot": {
                "path": self.history_path.relative_to(self.root).as_posix(),
                "sha256": history_sha,
                "rows": len(self.history),
                "bootstrap_mode": False,
                "schema": {
                    "required_columns": ["signal_date", "ts_code"],
                    "columns_sha256": frame_columns_sha256(self.history.columns),
                },
            },
            "production": {
                "model_version": "auction-v13",
                "promoted": False,
                "trade_selector_version": "selector-v2",
                "trade_selector_promoted": False,
                "formal_status": "NO_TRADE_MODEL_NOT_PROMOTED",
                "formal_buy_count": 0,
                "legacy_v1_audit": {
                    "enforcement": "audit_only",
                    "model_artifact_sha256": "e" * 64,
                    "trade_selector_artifact_sha256": "f" * 64,
                },
                "canonical_v2": {
                    "schema_version": "decision_runtime_canonical_contract_v2",
                    "enforcement": "hard",
                    "model": self.model_layer,
                    "trade_selector": self.selector_layer,
                    "precision_evidence": {
                        "baseline_commit": (
                            "c6de497aaab48c40e205aa7fe8401ad6ad9780ad"
                        ),
                        "candidate_commit": "1" * 40,
                        "github_run_ids": ["31951126704", "31951126705"],
                        "probes": [6, 8, 10, 12],
                        "identity_and_discrete_changed": 0,
                        "formal_no_trade_preserved": True,
                        "material_mutation_probe_passed": True,
                    },
                },
            },
            "behavior_contract": {
                "schema_version": "decision_frozen_behavior_v2",
                "canonical_schema": CANONICAL_FINGERPRINT_SCHEMA,
                "top10": top_contract,
                "trade_selector_oos": oos_contract,
                "action_watchlist": watch_contract,
                "reference_evidence": dict(KNOWN_REFERENCE_EVIDENCE),
                "nested_oos_research": {
                    "all_candidates_path": "trade_selector.formal_policy_oos.all_candidates",
                    "signals": 1,
                    "signal_dates": 1,
                    "filled_trades": 1,
                    "market_buyable_path": "trade_selector.formal_policy_oos.market_buyable_only",
                    "market_buyable_filled_trades": 1,
                },
                "decision": {
                    "status_code": "NO_TRADE_MODEL_NOT_PROMOTED",
                    "formal_buy_count": 0,
                    "top10_selected_count": 0,
                    "selector_globally_promoted_count": 0,
                    "nested_oos_trade_selected_count": 1,
                    "nested_oos_trade_selector_promoted_count": 2,
                    "production_backtest_signals": 0,
                    "production_backtest_signal_dates": 0,
                    "production_backtest_fills": 0,
                    "reason_values": ["selection_policy_not_ready"],
                },
            },
            "pinned_files": {},
        }
        pinned = {}
        for relative in REQUIRED_ACTIVE_PIN_PATHS:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(f"pin:{relative}".encode())
            pinned[relative] = hashlib.sha256(target.read_bytes()).hexdigest()
        self.manifest["pinned_files"] = pinned
        self._write_runtime()
        self._write_manifest()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_manifest(self) -> None:
        _write_json(self.root / "models/decision_model_freeze.json", self.manifest)

    def _known_contract_patches(self, manifest: dict | None = None) -> dict:
        candidate = manifest or self.manifest
        snapshot = candidate["history_snapshot"]
        behavior = candidate["behavior_contract"]
        top10 = behavior["top10"]
        oos = behavior["trade_selector_oos"]
        nested = behavior["nested_oos_research"]
        action = behavior["action_watchlist"]
        decision = behavior["decision"]
        return {
            # This fixture exercises the reviewed pre-three-rank canonical-V2
            # baseline.  Patch its local freeze id as that one compatibility
            # target instead of fabricating a production.three_rank overlay.
            "LEGACY_PRE_THREE_RANK_FREEZE_ID": candidate["freeze_id"],
            "KNOWN_HISTORY_PATH": snapshot["path"],
            "KNOWN_HISTORY_SHA256": snapshot["sha256"],
            "KNOWN_HISTORY_ROWS": snapshot["rows"],
            "KNOWN_TOP10_ROWS": top10["rows"],
            "KNOWN_TOP10_DATES": top10["signal_dates"],
            "KNOWN_OOS_ROWS": oos["rows"],
            "KNOWN_OOS_DATES": oos["signal_dates"],
            "KNOWN_NESTED_OOS_SIGNALS": nested["signals"],
            "KNOWN_NESTED_OOS_SIGNAL_DATES": nested["signal_dates"],
            "KNOWN_NESTED_OOS_FILLED_TRADES": nested["filled_trades"],
            "KNOWN_NESTED_OOS_MARKET_BUYABLE_FILLED_TRADES": nested[
                "market_buyable_filled_trades"
            ],
            "KNOWN_NESTED_OOS_TRADE_SELECTED": decision[
                "nested_oos_trade_selected_count"
            ],
            "KNOWN_ACTION_SHADOW_ROWS": action["shadow_only_rows"],
            "KNOWN_ACTION_REFERENCE_PATH": action["path"],
            "KNOWN_ACTION_REFERENCE_ROWS": action["rows"],
            "KNOWN_ACTION_REFERENCE_SHA256": action["sha256"],
        }

    def _load_forced(self, manifest: dict | None = None):
        candidate = manifest or self.manifest
        with patch.multiple(freeze_module, **self._known_contract_patches(candidate)):
            return load_verified_frozen_history_snapshot(self.root, candidate)

    def _validate_runtime(self):
        with patch.multiple(
            freeze_module, **self._known_contract_patches(self.manifest)
        ):
            return validate_runtime_artifacts(
                self.root, self.manifest, force_enforcement=True
            )

    def _validate_action(self):
        with patch.multiple(
            freeze_module, **self._known_contract_patches(self.manifest)
        ):
            return validate_action_plan_artifact(
                self.root, self.manifest, force_enforcement=True
            )

    def _model_runtime_fields(self) -> dict:
        return {
            "model_canonical_v2_version": self.model_layer[
                "canonical_v2_version"
            ],
            "model_artifact_v2_sha256": self.model_layer["artifact_v2_sha256"],
            "model_fingerprint_v2": self.model_layer["fingerprint_v2"],
            "model_canonical_contract": self.model_layer["canonical_contract"],
        }

    def _selector_runtime_fields(self) -> dict:
        return {
            "canonical_v2_version": self.selector_layer["canonical_v2_version"],
            "production_artifact_v2_sha256": self.selector_layer[
                "artifact_v2_sha256"
            ],
            "production_fingerprint_v2": self.selector_layer["fingerprint_v2"],
            "canonical_contract": self.selector_layer["canonical_contract"],
        }

    def _action_payload(self) -> dict:
        model_contract = self.model_layer["canonical_contract"]
        model_projection = self.model_layer["fingerprint_v2"]["policy_projection"]
        selector_contract = self.selector_layer["canonical_contract"]
        return {
            "signal_date": "20260814",
            "report_date": "20260817",
            "exec_date": "20260817",
            "exit_date": "20260818",
            "report_file": "decision_report_20260817.md",
            "status_code": "NO_TRADE_MODEL_NOT_PROMOTED",
            "formal_buy_count": 0,
            "shadow_count": 1,
            "stage_watch_count": 1,
            "stage_watch_eligible_count": 1,
            "stage_watch_display_limit": 10,
            "guidance_only": True,
            "broker_connected": False,
            "order_execution": "manual_only",
            "candidates": [
                {
                    "ts_code": "000001.SZ",
                    "rank": 1,
                    "trade_rank": 0,
                    "action": "REJECT",
                    "target_weight": 0.0,
                    "trade_gate_pass": 0,
                    "trade_shadow_selected": 0,
                    "trade_selected": 0,
                    "trade_selector_promoted": 0,
                    "market_order_allowed": 0,
                    "risk_gate_pass": 0,
                    "order_type": "LIMIT_ONLY_MANUAL",
                    "recommended_max_price": None,
                    "max_auction_change_pct": None,
                },
                {
                    "ts_code": "000002.SZ",
                    "rank": 2,
                    "trade_rank": 1,
                    "action": "SHADOW_ONLY",
                    "target_weight": 0.0,
                    "trade_gate_pass": 1,
                    "trade_shadow_selected": 1,
                    "trade_selected": 0,
                    "trade_selector_promoted": 0,
                    "market_order_allowed": 0,
                    "risk_gate_pass": 0,
                    "order_type": "LIMIT_ONLY_MANUAL",
                    "recommended_max_price": None,
                    "max_auction_change_pct": None,
                }
            ],
            "stage_watchlist": [
                {
                    "ts_code": "000002.SZ",
                    "action": "SHADOW_ONLY",
                    "stage_watch_rank": 1,
                    "trade_rank": 1,
                    "watch_label": "二筛影子",
                    "target_weight": 0.0,
                    "trade_gate_pass": 1,
                    "trade_shadow_selected": 1,
                    "trade_selected": 0,
                    "trade_selector_promoted": 0,
                    "market_order_allowed": 0,
                    "risk_gate_pass": 0,
                    "order_type": "LIMIT_ONLY_MANUAL",
                    "recommended_max_price": None,
                    "max_auction_change_pct": None,
                }
            ],
            "model": {
                "version": "auction-v13",
                "promoted": False,
                "prediction_matches_report": True,
                "selection_policy": copy.deepcopy(model_projection),
                "canonical_v2_version": self.model_layer[
                    "canonical_v2_version"
                ],
                "artifact_v2_sha256": self.model_layer["artifact_v2_sha256"],
                "fingerprint_v2": self.model_layer["fingerprint_v2"],
                "fingerprint_v2_valid": True,
                "artifact_v2_fingerprints_match": True,
                "canonical_v2_versions_match": True,
                "canonical_policy_ready": False,
                "canonical_contract": model_contract,
                "canonical_contracts_match": True,
                "canonical_decimals": 8,
                "canonical_decimals_match": True,
                "execution_numeric_mode": "raw_float64",
                "raw_execution_preserved": True,
                "trade_selector_canonical_v2_version": self.selector_layer[
                    "canonical_v2_version"
                ],
                "trade_selector_artifact_v2_sha256": self.selector_layer[
                    "artifact_v2_sha256"
                ],
                "trade_selector_fingerprint_v2": self.selector_layer[
                    "fingerprint_v2"
                ],
                "trade_selector_fingerprint_v2_valid": True,
                "trade_selector_artifacts_v2_match": True,
                "trade_selector_canonical_v2_versions_match": True,
                "trade_selector_canonical_policy_ready": False,
                "trade_selector_canonical_contract": selector_contract,
                "trade_selector_canonical_contracts_match": True,
                "trade_selector_canonical_decimals": 8,
                "trade_selector_canonical_decimals_match": True,
                "trade_selector_execution_numeric_mode": "raw_float64",
                "trade_selector_raw_execution_preserved": True,
                "trade_selector": {"version": "selector-v2", "promoted": False},
            },
        }

    def _write_runtime(self) -> None:
        model_projection = self.model_layer["fingerprint_v2"]["policy_projection"]
        selector_projection = self.selector_layer["fingerprint_v2"][
            "policy_projection"
        ]
        selector = {
            "version": "selector-v2",
            "promoted": False,
            "production_artifact_sha256": "f" * 64,
            "production_policy": copy.deepcopy(selector_projection),
            **self._selector_runtime_fields(),
            "formal_policy_oos": {
                "all_candidates": {
                    "signals": 1,
                    "signal_dates": 1,
                    "filled_trades": 1,
                },
                "market_buyable_only": {"filled_trades": 1},
            },
        }
        meta = {
            "model_version": "auction-v13",
            "model_artifact_sha256": "e" * 64,
            "promoted": False,
            "data_coverage": {"history_end": "20260805"},
            "selection_policy": copy.deepcopy(model_projection),
            **self._model_runtime_fields(),
            "trade_selector": selector,
        }
        backtest = {
            "model_version": "auction-v13",
            "model_artifact_sha256": "e" * 64,
            "promoted": False,
            "signals": 0,
            "signal_dates": 0,
            "filled_trades": 0,
            **self._model_runtime_fields(),
            "trade_selector": selector,
        }
        prediction_row = {
            "model_canonical_v2_version": self.model_layer[
                "canonical_v2_version"
            ],
            "model_artifact_v2_sha256": self.model_layer["artifact_v2_sha256"],
            "model_canonical_schema": CANONICAL_FINGERPRINT_SCHEMA,
            "model_canonical_decimals": 8,
            "model_execution_numeric_mode": "raw_float64",
            "model_raw_execution_preserved": True,
            "selection_policy_version": model_projection["version"],
            "signal_date": "20260814",
            "expected_buy_date": "20260817",
            "expected_exit_date": "20260818",
            "ts_code": "000001.SZ",
            "stage": "2→3",
            "stage_transition": "2→3",
            "mechanism_limit_pct": 20.0,
            "stage_focus": 1,
            "predicted_exit_probability": 0.9,
            "predicted_big_loss_probability": 0.1,
            "predicted_mean_return_lcb": 0.1,
            "predicted_return_lcb": 0.1,
            "conservative_ev": 0.1,
            "selection_score": 0.2,
            "source_rank": 1,
            "gate_policy_ready": int(model_projection["ready"]),
            "gate_stage_focus": 1,
            "gate_exit_probability": 1,
            "gate_fill_probability": 1,
            "gate_big_loss_probability": 1,
            "gate_mean_return_lcb": 1,
            "gate_conservative_ev": 1,
            "gate_selection_score": 1,
            "risk_gate_pass": 0,
            "model_reason": "selection_policy_not_ready",
            "shadow_rank": 1,
            "shadow_selected": 1,
            "first_layer_shadow_selected": 1,
            "first_layer_selected": 0,
            "selected": 0,
            "model_promoted": 0,
            "action": "REJECT",
            "recommended_max_price": None,
            "max_auction_change_pct": None,
            "estimated_up_limit": 12.0,
            "d_close": 10.0,
            "guidance_only": 1,
            "broker_connected": 0,
            "market_order_allowed": 0,
            "order_type": "LIMIT_ONLY_MANUAL",
            "policy_max_positions": model_projection["max_positions"],
            **{
                f"policy_{name}": value
                for name, value in model_projection["thresholds"].items()
            },
            "trade_selector_canonical_v2_version": self.selector_layer[
                "canonical_v2_version"
            ],
            "trade_selector_artifact_v2_sha256": None,
            "trade_selector_canonical_schema": CANONICAL_FINGERPRINT_SCHEMA,
            "trade_selector_canonical_decimals": 8,
            "trade_selector_execution_numeric_mode": "raw_float64",
            "trade_selector_raw_execution_preserved": True,
            "observation_selected": 0,
            "observation_rank": None,
            "observation_risk_tier": None,
            "observation_risk_label": None,
            "observation_pool_size": 1,
            "predicted_fill_probability": 0.25,
            "predicted_public_market_buyable_probability": 0.25,
            "trade_predicted_fill_probability": None,
            "trade_predicted_public_market_buyable_probability": None,
            "actual_order_fill_probability_available": 0,
            "predicted_actual_order_fill_probability": None,
            "promotion_rank": None,
            "promotion_rank_score": None,
            "predicted_promotion_probability": None,
            "trade_rank": None,
            "trade_score": None,
            "trade_predicted_conditional_net_return": None,
            "trade_predicted_mean_return_lcb": None,
            "trade_predicted_big_loss_probability": None,
            "trade_predicted_outcome_q10": None,
            "trade_tail_loss_proxy": None,
            "trade_base_score": None,
            "trade_tail_risk_weight": None,
            "trade_gate_pass": 0,
            "trade_shadow_selected": 0,
            "trade_selected": 0,
            "trade_selector_policy_ready": 0,
            "trade_selector_promoted": 0,
            "trade_selector_version": "selector-v2",
            "trade_selector_artifact_sha256": None,
            "trade_model_reason": "outside_observation_top10",
        }
        _write_json(
            self.root / "outputs/auction_v3/models/model_meta_latest.json", meta
        )
        _write_json(
            self.root / "outputs/auction_v3/metrics/backtest_latest.json", backtest
        )
        prediction_path = (
            self.root / "outputs/auction_v3/predictions/pred_latest.csv"
        )
        prediction_path.parent.mkdir(parents=True, exist_ok=True)
        second_prediction_row = dict(prediction_row)
        second_prediction_row.update(
            {
                "predicted_fill_probability": 0.30,
                "predicted_public_market_buyable_probability": 0.30,
                "observation_selected": 1,
                "ts_code": "000002.SZ",
                "mechanism_limit_pct": 10.0,
                "shadow_rank": 2,
                "source_rank": 2,
                "observation_rank": 1,
                "observation_risk_tier": 1,
                "observation_risk_label": "观察风险可控",
                "trade_selector_artifact_v2_sha256": self.selector_layer[
                    "artifact_v2_sha256"
                ],
                "trade_selector_artifact_sha256": "f" * 64,
                "trade_predicted_fill_probability": 0.40,
                "trade_predicted_public_market_buyable_probability": 0.40,
                "actual_order_fill_probability_available": 1,
                "predicted_actual_order_fill_probability": 0.45,
                "promotion_rank": 1,
                "promotion_rank_score": 0.7,
                "predicted_promotion_probability": 0.6,
                "trade_rank": 1,
                "trade_score": 0.1998,
                "trade_predicted_conditional_net_return": 0.03,
                "trade_predicted_mean_return_lcb": 0.5,
                "trade_predicted_big_loss_probability": 0.1,
                "trade_predicted_outcome_q10": -0.02,
                "trade_tail_loss_proxy": -0.002,
                "trade_base_score": 0.2,
                "trade_tail_risk_weight": 0.25,
                "trade_gate_pass": 1,
                "trade_shadow_selected": 1,
                "action": "SHADOW_ONLY",
                "trade_model_reason": "relative_best_two_only",
            }
        )
        pd.DataFrame([prediction_row, second_prediction_row]).to_csv(
            prediction_path, index=False
        )
        decision_root = self.root / "outputs/decision"
        decision_root.mkdir(parents=True, exist_ok=True)
        (decision_root / "decision_report_20260817.md").write_text(
            "# Decision Report (20260817)\n\n"
            "- signal_date: **20260814**\n"
            "- exec_date: **20260817**\n"
            "- exit_date: **20260818**\n",
            encoding="utf-8",
        )
        _write_json(
            decision_root / "eval_20260817.json",
            {
                "signal_date": "20260814",
                "exec_date": "20260817",
                "exit_date": "20260818",
            },
        )
        _write_json(
            decision_root / "action_plan_latest.json", self.action
        )


class DecisionModelFreezeV2SchemaTest(FreezeV2Fixture):
    def test_exact_string_hash_rotates_whitespace_and_nfkc_variants(self):
        self.assertEqual(
            canonical_mapping_sha256({"label": "Ａ"}, decimals=8),
            canonical_mapping_sha256({"label": "A"}, decimals=8),
        )
        self.assertNotEqual(
            canonical_mapping_sha256(
                {"label": "Ａ"}, decimals=8, exact_strings=True
            ),
            canonical_mapping_sha256(
                {"label": "A"}, decimals=8, exact_strings=True
            ),
        )
        left = pd.DataFrame([{"label": "观察"}])
        right = pd.DataFrame([{"label": "观察 "}])
        self.assertNotEqual(
            canonical_frame_fingerprint(
                left, ("label",), decimals=8, kinds={"label": "exact_text"}
            )["sha256"],
            canonical_frame_fingerprint(
                right, ("label",), decimals=8, kinds={"label": "exact_text"}
            )["sha256"],
        )

    def test_complete_inactive_candidate_parses_but_does_not_normally_enforce(self):
        loaded = load_model_freeze(self.root, required=True)
        self.assertFalse(loaded["active"])
        frame, audit = load_frozen_history_snapshot(self.root, loaded)
        self.assertIsNone(frame)
        self.assertEqual(audit["source"], "live_history")

        files_audit = validate_pinned_files(self.root, loaded)
        self.assertFalse(files_audit["enforced"])
        self.assertFalse(files_audit["forced_enforcement"])

    def test_inactive_candidate_may_carry_reviewed_precision_evidence(self):
        self.manifest["production"]["canonical_v2"]["precision_evidence"] = {
            "baseline_commit": "c6de497aaab48c40e205aa7fe8401ad6ad9780ad",
            "candidate_commit": "1" * 40,
            "github_run_ids": ["31951126704", "31951126705"],
            "probes": [6, 8, 10, 12],
            "identity_and_discrete_changed": 0,
            "formal_no_trade_preserved": True,
            "material_mutation_probe_passed": True,
        }
        self._write_manifest()
        self.assertFalse(load_model_freeze(self.root, required=True)["active"])

    def test_precision_evidence_requires_exactly_two_distinct_string_run_ids(self):
        valid = copy.deepcopy(
            self.manifest["production"]["canonical_v2"]["precision_evidence"]
        )
        self.assertEqual(
            valid["github_run_ids"], ["31951126704", "31951126705"]
        )
        invalid_values = (
            31951126704,
            [],
            ["31951126704"],
            ["31951126704", "31951126705", "31951126706"],
            ["31951126704", "31951126704"],
            ["31951126704", "run-two"],
            ["31951126704", 31951126705],
            ["31951126704", "３１９５１１２６７０５"],
            ["31951126704", "٣١٩٥١١٢٦٧٠٥"],
            ["31951126704", "031951126705"],
            ["31951126704", "0"],
            ["31951126704", ""],
        )
        for run_ids in invalid_values:
            manifest = copy.deepcopy(self.manifest)
            manifest["production"]["canonical_v2"]["precision_evidence"][
                "github_run_ids"
            ] = run_ids
            _write_json(self.root / "models/decision_model_freeze.json", manifest)
            with self.subTest(run_ids=run_ids):
                with self.assertRaisesRegex(
                    DecisionModelFreezeError, "github_run_ids"
                ):
                    load_model_freeze(self.root, required=True)

        direct = copy.deepcopy(valid)
        direct["github_run_ids"] = ("31951126704", "31951126705")
        with self.assertRaisesRegex(DecisionModelFreezeError, "native list"):
            freeze_module._validate_precision_evidence(direct)

        for mutation in ("missing", "legacy_singular"):
            manifest = copy.deepcopy(self.manifest)
            evidence = manifest["production"]["canonical_v2"][
                "precision_evidence"
            ]
            evidence.pop("github_run_ids")
            if mutation == "legacy_singular":
                evidence["github_run_id"] = "31951126704"
            _write_json(self.root / "models/decision_model_freeze.json", manifest)
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(
                    DecisionModelFreezeError, "precision_evidence.*keys drift"
                ):
                    load_model_freeze(self.root, required=True)

    def test_force_inactive_verifies_actual_pin_bytes_and_runtime_cannot_bypass(self):
        with patch.multiple(
            freeze_module, **self._known_contract_patches(self.manifest)
        ):
            files_audit = validate_pinned_files(
                self.root, self.manifest, force_enforcement=True
            )
            self.assertTrue(files_audit["enforced"])
            self.assertTrue(files_audit["forced_enforcement"])

        runtime_audit = self._validate_runtime()
        self.assertTrue(runtime_audit["pinned_files"]["enforced"])
        self.assertTrue(runtime_audit["pinned_files"]["forced_enforcement"])

        relative = sorted(REQUIRED_ACTIVE_PIN_PATHS)[0]
        (self.root / relative).write_bytes(b"forced-review-pin-drift")
        legacy_audit = validate_pinned_files(self.root, self.manifest)
        self.assertFalse(legacy_audit["enforced"])
        with patch.multiple(
            freeze_module, **self._known_contract_patches(self.manifest)
        ):
            with self.assertRaisesRegex(
                DecisionModelFreezeError, "frozen file drift"
            ):
                validate_pinned_files(
                    self.root, self.manifest, force_enforcement=True
                )
            with self.assertRaisesRegex(
                DecisionModelFreezeError, "frozen file drift"
            ):
                validate_runtime_artifacts(
                    self.root, self.manifest, force_enforcement=True
                )

    def test_force_inactive_requires_every_activation_contract(self):
        mutations: list[tuple[str, dict]] = []

        missing_precision = copy.deepcopy(self.manifest)
        missing_precision["production"]["canonical_v2"].pop(
            "precision_evidence"
        )
        mutations.append(("precision_evidence", missing_precision))

        nested_count = copy.deepcopy(self.manifest)
        nested_count["behavior_contract"]["nested_oos_research"]["signals"] += 1
        mutations.append(("nested_oos", nested_count))

        behavior_rows = copy.deepcopy(self.manifest)
        behavior_rows["behavior_contract"]["top10"]["rows"] += 1
        mutations.append(("behavior_rows", behavior_rows))

        shadow_rows = copy.deepcopy(self.manifest)
        shadow_rows["behavior_contract"]["action_watchlist"][
            "shadow_only_rows"
        ] += 1
        mutations.append(("shadow_rows", shadow_rows))

        decision_count = copy.deepcopy(self.manifest)
        decision_count["behavior_contract"]["decision"][
            "nested_oos_trade_selected_count"
        ] += 1
        mutations.append(("decision_count", decision_count))

        missing_pin = copy.deepcopy(self.manifest)
        missing_pin["pinned_files"].pop(sorted(REQUIRED_ACTIVE_PIN_PATHS)[0])
        mutations.append(("required_pin", missing_pin))

        expected = self._known_contract_patches(self.manifest)
        for name, candidate in mutations:
            with self.subTest(name=name):
                _write_json(
                    self.root / "models/decision_model_freeze.json", candidate
                )
                self.assertFalse(load_model_freeze(self.root, required=True)["active"])
                self.assertFalse(
                    validate_pinned_files(self.root, candidate)["enforced"]
                )
                with patch.multiple(freeze_module, **expected):
                    with self.assertRaises(DecisionModelFreezeError):
                        validate_pinned_files(
                            self.root, candidate, force_enforcement=True
                        )

    def test_force_inactive_rejects_synchronized_known_contract_tamper(self):
        candidate = copy.deepcopy(self.manifest)
        candidate["behavior_contract"]["nested_oos_research"]["signals"] += 1
        backtest_path = self.root / "outputs/auction_v3/metrics/backtest_latest.json"
        backtest = json.loads(backtest_path.read_text())
        backtest["trade_selector"]["formal_policy_oos"]["all_candidates"][
            "signals"
        ] += 1
        _write_json(backtest_path, backtest)
        _write_json(self.root / "models/decision_model_freeze.json", candidate)

        self.assertFalse(load_model_freeze(self.root, required=True)["active"])
        with patch.multiple(
            freeze_module, **self._known_contract_patches(self.manifest)
        ):
            with self.assertRaisesRegex(
                DecisionModelFreezeError, "158/119/158 and 25"
            ):
                validate_runtime_artifacts(
                    self.root, candidate, force_enforcement=True
                )

    def test_active_legacy_manifest_is_rejected(self):
        legacy = {
            "schema_version": "decision_model_freeze_v1",
            "active": True,
        }
        _write_json(self.root / "models/decision_model_freeze.json", legacy)
        with self.assertRaises(DecisionModelFreezeError):
            load_model_freeze(self.root, required=True)

    def test_unsafe_paths_symlink_malformed_sha_bootstrap_and_contract_drift_fail(self):
        mutations = []
        for value in ("../escape.csv.gz", "/tmp/escape.csv.gz", "C:\\escape.csv.gz"):
            manifest = copy.deepcopy(self.manifest)
            manifest["history_snapshot"]["path"] = value
            mutations.append(manifest)
        malformed = copy.deepcopy(self.manifest)
        malformed["history_snapshot"]["sha256"] = "not-a-sha"
        mutations.append(malformed)
        bootstrap = copy.deepcopy(self.manifest)
        bootstrap["history_snapshot"]["bootstrap_mode"] = True
        mutations.append(bootstrap)
        for key, value in (
            ("decimals", 10),
            ("rounding", "ROUND_HALF_EVEN"),
            ("execution_mode", "quantized_float64"),
            ("raw_execution_preserved", False),
        ):
            manifest = copy.deepcopy(self.manifest)
            manifest["production"]["canonical_v2"]["model"][
                "canonical_contract"
            ][key] = value
            mutations.append(manifest)
        reference_drift = copy.deepcopy(self.manifest)
        reference_drift["behavior_contract"]["reference_evidence"][
            "top10_blob_sha1"
        ] = "1" * 40
        mutations.append(reference_drift)
        unsafe_behavior = copy.deepcopy(self.manifest)
        unsafe_behavior["behavior_contract"]["top10"]["path"] = "../top10.csv"
        mutations.append(unsafe_behavior)
        unsafe_action = copy.deepcopy(self.manifest)
        unsafe_action["behavior_contract"]["action_watchlist"]["path"] = (
            "/tmp/action.json"
        )
        mutations.append(unsafe_action)
        unsafe_pin = copy.deepcopy(self.manifest)
        unsafe_pin["pinned_files"] = {"../escape.py": "0" * 64}
        mutations.append(unsafe_pin)
        for manifest in mutations:
            with self.subTest(snapshot=manifest.get("history_snapshot")):
                _write_json(
                    self.root / "models/decision_model_freeze.json", manifest
                )
                with self.assertRaises(DecisionModelFreezeError):
                    load_model_freeze(self.root, required=True)

        target = self.root / "models/real.csv.gz"
        target.write_bytes(b"x")
        link = self.root / "models/link.csv.gz"
        link.symlink_to(target)
        manifest = copy.deepcopy(self.manifest)
        manifest["history_snapshot"]["path"] = "models/link.csv.gz"
        _write_json(self.root / "models/decision_model_freeze.json", manifest)
        with self.assertRaises(DecisionModelFreezeError):
            load_model_freeze(self.root, required=True)

    def test_exact_columns_and_six_threshold_policy_projection_are_schema_hard(self):
        for mutation in ("columns", "projection"):
            manifest = copy.deepcopy(self.manifest)
            if mutation == "columns":
                manifest["behavior_contract"]["top10"]["score_columns"].pop()
            else:
                projection = manifest["production"]["canonical_v2"]["model"][
                    "fingerprint_v2"
                ]["policy_projection"]
                projection["thresholds"].pop("min_selection_score")
            _write_json(self.root / "models/decision_model_freeze.json", manifest)
            with self.subTest(mutation=mutation):
                with self.assertRaises(DecisionModelFreezeError):
                    load_model_freeze(self.root, required=True)

    def test_policy_projection_types_and_finite_numbers_are_schema_hard(self):
        mutations = []
        for layer, key, value in (
            ("model", "ready", 0),
            ("model", "max_positions", 1.5),
            ("trade_selector", "tail_risk_weight", float("inf")),
        ):
            manifest = copy.deepcopy(self.manifest)
            manifest["production"]["canonical_v2"][layer]["fingerprint_v2"][
                "policy_projection"
            ][key] = value
            mutations.append(manifest)
        threshold = copy.deepcopy(self.manifest)
        threshold["production"]["canonical_v2"]["model"]["fingerprint_v2"][
            "policy_projection"
        ]["thresholds"]["min_fill_probability"] = float("nan")
        mutations.append(threshold)
        numeric_string = copy.deepcopy(self.manifest)
        numeric_string["production"]["canonical_v2"]["trade_selector"][
            "fingerprint_v2"
        ]["policy_projection"]["thresholds"]["min_trade_score"] = "0.1"
        mutations.append(numeric_string)
        integer_model_threshold = copy.deepcopy(self.manifest)
        integer_model_threshold["production"]["canonical_v2"]["model"][
            "fingerprint_v2"
        ]["policy_projection"]["thresholds"]["min_mean_return_lcb"] = 0
        mutations.append(integer_model_threshold)
        integer_selector_threshold = copy.deepcopy(self.manifest)
        integer_selector_threshold["production"]["canonical_v2"][
            "trade_selector"
        ]["fingerprint_v2"]["policy_projection"]["thresholds"][
            "min_mean_return_lcb"
        ] = 0
        mutations.append(integer_selector_threshold)
        integer_selector_tail = copy.deepcopy(self.manifest)
        integer_selector_tail["production"]["canonical_v2"]["trade_selector"][
            "fingerprint_v2"
        ]["policy_projection"]["tail_risk_weight"] = 0
        mutations.append(integer_selector_tail)
        for manifest in mutations:
            _write_json(self.root / "models/decision_model_freeze.json", manifest)
            with self.assertRaises(DecisionModelFreezeError):
                load_model_freeze(self.root, required=True)

    def test_policy_projection_exact_strings_reject_whitespace_and_nfkc_drift(self):
        mutations = (
            ("model", "reason", "selection_policy_not_ready "),
            ("trade_selector", "version", "selector-policy-v２"),
        )
        for layer, field, value in mutations:
            manifest = copy.deepcopy(self.manifest)
            manifest["production"]["canonical_v2"][layer]["fingerprint_v2"][
                "policy_projection"
            ][field] = value
            _write_json(self.root / "models/decision_model_freeze.json", manifest)
            with self.subTest(layer=layer, field=field):
                with self.assertRaisesRegex(DecisionModelFreezeError, "policy_sha256"):
                    load_model_freeze(self.root, required=True)

    def test_active_release_shape_requires_evidence_known_counts_and_critical_pins(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["active"] = True
        manifest["production"]["canonical_v2"].pop("precision_evidence")
        manifest["behavior_contract"]["action_watchlist"][
            "shadow_only_rows"
        ] = 2
        manifest["history_snapshot"].update(
            {
                "path": "models/decision_v12_frozen_history_20260805.csv.gz",
                "sha256": "77e48be6732a08698a6abf4a0da74cb02b3129c57d14be66fb94679816a5337e",
                "rows": 40355,
            }
        )
        manifest["behavior_contract"]["top10"].update(
            {"rows": 4467, "signal_dates": 543}
        )
        manifest["behavior_contract"]["trade_selector_oos"].update(
            {"rows": 3097, "signal_dates": 363}
        )
        decision = manifest["behavior_contract"]["decision"]
        decision["nested_oos_trade_selected_count"] = 158
        decision["nested_oos_trade_selector_promoted_count"] = 3097
        manifest["pinned_files"] = {
            path: "0" * 64 for path in REQUIRED_ACTIVE_PIN_PATHS
        }
        _write_json(self.root / "models/decision_model_freeze.json", manifest)
        with self.assertRaisesRegex(DecisionModelFreezeError, "precision_evidence"):
            load_model_freeze(self.root, required=True)

    def test_active_required_pin_paths_exist_and_observation_drift_fails(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["active"] = True
        manifest["behavior_contract"]["action_watchlist"][
            "shadow_only_rows"
        ] = 2
        reviewed_history = self.root / freeze_module.KNOWN_HISTORY_PATH
        reviewed_history.parent.mkdir(parents=True, exist_ok=True)
        reviewed_history.write_bytes(self.history_path.read_bytes())
        reviewed_sha = hashlib.sha256(reviewed_history.read_bytes()).hexdigest()
        manifest["history_snapshot"].update(
            {
                "path": freeze_module.KNOWN_HISTORY_PATH,
                "sha256": reviewed_sha,
                "rows": len(self.history),
            }
        )
        manifest["behavior_contract"]["decision"].update(
            {
                "nested_oos_trade_selected_count": 158,
                "nested_oos_trade_selector_promoted_count": 3097,
            }
        )
        manifest["behavior_contract"]["top10"].update(
            {"rows": 4467, "signal_dates": 543}
        )
        manifest["behavior_contract"]["trade_selector_oos"].update(
            {"rows": 3097, "signal_dates": 363}
        )
        manifest["behavior_contract"]["nested_oos_research"].update(
            {
                "signals": 158,
                "signal_dates": 119,
                "filled_trades": 158,
                "market_buyable_filled_trades": 25,
            }
        )
        manifest["production"]["canonical_v2"]["precision_evidence"] = {
            "baseline_commit": "c6de497aaab48c40e205aa7fe8401ad6ad9780ad",
            "candidate_commit": "1" * 40,
            "github_run_ids": ["31951126704", "31951126705"],
            "probes": [6, 8, 10, 12],
            "identity_and_discrete_changed": 0,
            "formal_no_trade_preserved": True,
            "material_mutation_probe_passed": True,
        }
        pinned = {}
        for relative in REQUIRED_ACTIVE_PIN_PATHS:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(f"pin:{relative}".encode())
            pinned[relative] = hashlib.sha256(target.read_bytes()).hexdigest()
        manifest["pinned_files"] = pinned
        with patch.multiple(
            freeze_module,
            **self._known_contract_patches(manifest),
        ):
            audit = validate_pinned_files(self.root, manifest)
            self.assertTrue(audit["enforced"])
            observation = (
                self.root / "src/top10decision/decision/observation.py"
            )
            observation.write_bytes(b"drift")
            with self.assertRaises(DecisionModelFreezeError):
                validate_pinned_files(self.root, manifest)
        self.assertIn(
            ".github/workflows/backfill_decision_v11_history.yml",
            REQUIRED_ACTIVE_PIN_PATHS,
        )
        self.assertNotIn(
            ".github/workflows/backfill_decision_observations.yml",
            REQUIRED_ACTIVE_PIN_PATHS,
        )


class DecisionModelFreezeV2SnapshotTest(FreezeV2Fixture):
    def test_forced_inactive_reader_uses_only_verified_snapshot(self):
        with self.assertRaisesRegex(DecisionModelFreezeError, "40,355-row SHA77e"):
            load_verified_frozen_history_snapshot(self.root, self.manifest)
        frame, audit = self._load_forced()
        self.assertEqual(len(frame), 2)
        self.assertEqual(audit["source"], "forced_frozen_snapshot")
        self.assertFalse(audit["manifest_active"])
        self.assertEqual(audit["sha256"], self.manifest["history_snapshot"]["sha256"])

        missing = self.history_path.with_suffix(".missing")
        self.history_path.rename(missing)
        with self.assertRaises(DecisionModelFreezeError):
            self._load_forced()

    def test_snapshot_sha_rows_schema_and_cutoff_fail_closed(self):
        mutations = []
        wrong_sha = copy.deepcopy(self.manifest)
        wrong_sha["history_snapshot"]["sha256"] = "1" * 64
        mutations.append(wrong_sha)
        wrong_rows = copy.deepcopy(self.manifest)
        wrong_rows["history_snapshot"]["rows"] = 3
        mutations.append(wrong_rows)
        wrong_schema = copy.deepcopy(self.manifest)
        wrong_schema["history_snapshot"]["schema"]["columns_sha256"] = "2" * 64
        mutations.append(wrong_schema)
        for manifest in mutations:
            with self.subTest(manifest=manifest["history_snapshot"]):
                with self.assertRaises(DecisionModelFreezeError):
                    self._load_forced(manifest)

        late = self.history.copy()
        late.loc[1, "signal_date"] = "20260806"
        late.to_csv(
            self.history_path,
            index=False,
            compression={"method": "gzip", "compresslevel": 9, "mtime": 0},
        )
        manifest = copy.deepcopy(self.manifest)
        manifest["history_snapshot"]["sha256"] = hashlib.sha256(
            self.history_path.read_bytes()
        ).hexdigest()
        with self.assertRaises(DecisionModelFreezeError):
            self._load_forced(manifest)


class DecisionModelFreezeV2BehaviorTest(FreezeV2Fixture):
    def test_behavior_hashes_are_row_order_independent(self):
        self.top10.iloc[::-1].to_csv(self.top10_path, index=False)
        audit = validate_behavior_artifacts(self.root, self.manifest)
        self.assertTrue(audit["top10"]["validated"])
        self.assertEqual(
            audit["decision_frame_counts"]["nested_oos_trade_selected_count"], 1
        )

    def test_identity_date_discrete_score_and_missing_state_mutations_fail(self):
        variants = []
        duplicate = self.top10.copy()
        duplicate.loc[1, ["signal_date", "ts_code"]] = duplicate.loc[
            0, ["signal_date", "ts_code"]
        ].values
        variants.append(duplicate)
        date_change = self.top10.copy()
        date_change.loc[1, "signal_date"] = "20260804"
        variants.append(date_change)
        discrete = self.top10.copy()
        discrete.loc[0, "gate_fill_probability"] = 0
        variants.append(discrete)
        material_score = self.top10.copy()
        material_score.loc[0, "predicted_net_return"] += 1e-5
        variants.append(material_score)
        missing_to_zero = self.top10.copy()
        missing_to_zero.loc[0, "recommended_max_gap"] = 0.0
        variants.append(missing_to_zero)
        nfkc_equivalent = self.top10.copy()
        nfkc_equivalent.loc[0, "model_reason"] = "selection＿policy_not_ready"
        variants.append(nfkc_equivalent)
        whitespace_drift = self.top10.copy()
        whitespace_drift.loc[0, "observation_risk_label"] += " "
        variants.append(whitespace_drift)
        textual_boolean = self.top10.copy()
        textual_boolean["gate_policy_ready"] = textual_boolean[
            "gate_policy_ready"
        ].astype(object)
        textual_boolean.loc[0, "gate_policy_ready"] = "false"
        variants.append(textual_boolean)
        for column, value in (
            ("signal_date", "2026-08-04"),
            ("signal_date", " 20260804"),
            ("ts_code", "600001"),
            ("ts_code", "600001.sh"),
            ("ts_code", "600001.SH "),
            ("stage", "2->3"),
        ):
            identity_or_stage = self.top10.copy()
            identity_or_stage.loc[0, column] = value
            variants.append(identity_or_stage)
        for variant in variants:
            variant.to_csv(self.top10_path, index=False)
            with self.subTest(columns=list(variant.columns)):
                with self.assertRaises(DecisionModelFreezeError):
                    validate_behavior_artifacts(self.root, self.manifest)
            self.top10.to_csv(self.top10_path, index=False)

    def test_q8_allows_subunit_raw_drift_but_material_mutation_fails(self):
        tiny = self.top10.copy()
        tiny.loc[0, "predicted_net_return"] += 1e-10
        tiny.to_csv(self.top10_path, index=False)
        validate_behavior_artifacts(self.root, self.manifest)
        tiny.loc[0, "predicted_net_return"] += 1e-6
        tiny.to_csv(self.top10_path, index=False)
        with self.assertRaises(DecisionModelFreezeError):
            validate_behavior_artifacts(self.root, self.manifest)

    def test_oos_fold_tail_risk_weight_is_hashed_but_not_a_top10_requirement(self):
        self.assertIn("trade_tail_risk_weight", OOS_SCORE_COLUMNS)
        self.assertNotIn("trade_tail_risk_weight", TOP10_SCORE_COLUMNS)
        changed = self.oos.copy()
        changed.loc[0, "trade_tail_risk_weight"] += 0.01
        changed.to_csv(self.oos_path, index=False)
        with self.assertRaisesRegex(DecisionModelFreezeError, "scores_sha256"):
            validate_behavior_artifacts(self.root, self.manifest)

    def test_recommended_gap_presence_relation_is_schema_hard(self):
        contract = self.manifest["behavior_contract"]["top10"]
        mutations = []
        present_when_gate_zero = self.top10.copy()
        present_when_gate_zero.loc[0, "recommended_max_gap"] = 0.031
        mutations.append(present_when_gate_zero)
        missing_when_gate_one = self.top10.copy()
        missing_when_gate_one.loc[0, "risk_gate_pass"] = 1
        mutations.append(missing_when_gate_one)
        unequal_when_gate_one = self.top10.copy()
        unequal_when_gate_one.loc[0, "risk_gate_pass"] = 1
        unequal_when_gate_one.loc[0, "recommended_max_gap"] = 0.99
        mutations.append(unequal_when_gate_one)
        for frame in mutations:
            with self.assertRaises(DecisionModelFreezeError):
                compute_behavior_fingerprints(frame, contract, context="gap-test")
        valid = self.top10.copy()
        valid.loc[0, "risk_gate_pass"] = 1
        valid.loc[0, "recommended_max_gap"] = valid.loc[0, "diagnostic_gap"]
        result = compute_behavior_fingerprints(valid, contract, context="gap-valid")
        self.assertTrue(result["identity_unique_nonempty"])

    def test_historical_fold_policy_may_differ_from_final_but_hash_is_exact(self):
        changed_frames = {
            "top10": self.top10.copy(),
            "trade_selector_oos": self.oos.copy(),
        }
        changed_frames["top10"].loc[0, "policy_min_fill_probability"] += 0.01
        changed_frames["trade_selector_oos"].loc[
            1, "policy_max_big_loss_probability"
        ] += 0.02
        for name, frame in changed_frames.items():
            contract = copy.deepcopy(self.manifest["behavior_contract"][name])
            contract.update(
                {
                    key: value
                    for key, value in compute_behavior_fingerprints(
                        frame, contract, context=f"historical-fold.{name}"
                    ).items()
                    if key.endswith("_sha256")
                }
            )
            self.manifest["behavior_contract"][name] = contract
            path = self.top10_path if name == "top10" else self.oos_path
            frame.to_csv(path, index=False)

        # Historical fold policies are not the final production projection;
        # their reviewed values are protected by the behavior hashes instead.
        validate_behavior_artifacts(self.root, self.manifest)

        drifted = changed_frames["top10"].copy()
        drifted.loc[0, "policy_min_fill_probability"] += 0.01
        drifted.to_csv(self.top10_path, index=False)
        with self.assertRaisesRegex(DecisionModelFreezeError, "scores_sha256"):
            validate_behavior_artifacts(self.root, self.manifest)


class DecisionModelFreezeV2RuntimeTest(FreezeV2Fixture):
    def _write_three_rank_overlay(self) -> Path:
        self._write_runtime()
        action_path = self.root / "outputs/decision/action_plan_latest.json"
        action = json.loads(action_path.read_text(encoding="utf-8"))
        action["stage_watch_eligible_count"] = 2
        _write_json(action_path, action)
        path = self.root / "outputs/auction_v3/predictions/pred_latest.csv"
        prediction = pd.read_csv(path)
        for canonical, shadow in (
            freeze_module.THREE_RANK_CANONICAL_PREIMAGE_COLUMNS.items()
        ):
            if canonical not in prediction:
                prediction[canonical] = 0.2
            prediction[shadow] = prediction[canonical]
        prediction["three_rank_contract_version"] = (
            freeze_module.THREE_RANK_CONTRACT_VERSION
        )
        prediction["three_engine_runtime_status"] = (
            "PARTIAL_MODELS_NOT_READY"
        )
        prediction["three_engine_runtime_feature_gate_passed"] = 1
        prediction["three_engine_runtime_artifacts_hash_bound"] = 1
        prediction["three_engine_runtime_input_pool_complete"] = 1
        prediction["three_engine_runtime_failure"] = ""
        prediction["feature_snapshot_sha256"] = "a" * 64
        prediction["top10_members_sha256"] = "b" * 64
        prediction["promotion_pool_size"] = len(prediction)
        prediction["top10_selected"] = [0, 1]
        prediction["promotion_rank"] = [2, 1]
        prediction["promotion_rank_score"] = [0.91, 0.87]
        prediction["predicted_promotion_probability"] = [0.61, 0.57]
        prediction["promotion_model_status"] = "READY"
        prediction["big_loss_model_status"] = "NOT_READY_VALIDATION_GATE"
        prediction["profit_model_status"] = "NOT_READY_VALIDATION_GATE"
        prediction["big_loss_safety_rank"] = float("nan")
        prediction["big_loss_rank_score"] = float("nan")
        prediction["predicted_big_loss_probability"] = float("nan")
        prediction["profit_rank"] = float("nan")
        prediction["profit_rank_score"] = float("nan")
        prediction["predicted_profit_probability"] = float("nan")
        prediction.to_csv(path, index=False)
        return path

    def test_three_rank_overlay_validates_exact_canonical_preimage(self):
        path = self._write_three_rank_overlay()
        with patch(
            "top10decision.decision.three_rank."
            "_three_engine_projection_is_complete",
            return_value=True,
        ):
            audit = self._validate_runtime()

        preimage = audit["three_rank_canonical_preimage"]
        self.assertTrue(preimage["active"])
        self.assertTrue(preimage["canonical_preimage_validated"])
        self.assertEqual(preimage["rows"], 2)
        self.assertEqual(preimage["signal_date"], "20260814")
        persisted = pd.read_csv(path)
        self.assertTrue(
            persisted["predicted_big_loss_probability"].isna().all()
        )
        self.assertTrue(
            persisted["predicted_profit_probability"].isna().all()
        )

        with patch(
            "top10decision.decision.three_rank."
            "_three_engine_projection_is_complete",
            return_value=True,
        ):
            action_audit = self._validate_action()
        action_preimage = action_audit["action_plan"][
            "three_rank_canonical_preimage"
        ]
        self.assertTrue(action_preimage["active"])
        self.assertTrue(action_preimage["canonical_preimage_validated"])
        self.assertEqual(action_preimage["rows"], 2)

    def test_three_rank_overlay_preserves_round_trip_float_tokens(self):
        path = self._write_three_rank_overlay()
        exact_score = "0.30485097454095084"
        prediction_text = pd.read_csv(
            path,
            dtype=str,
            keep_default_na=False,
        )
        prediction_text.loc[0, "promotion_rank_score"] = exact_score
        prediction_text.to_csv(path, index=False)

        with patch(
            "top10decision.decision.three_rank."
            "_three_engine_projection_is_complete",
            return_value=True,
        ):
            audit = self._validate_runtime()

        self.assertTrue(
            audit["three_rank_canonical_preimage"][
                "canonical_preimage_validated"
            ]
        )
        parsed = freeze_module._read_csv(path, "round-trip prediction")
        exact = freeze_module._read_csv_exact_text(
            path,
            "round-trip prediction text",
        )
        self.assertEqual(
            float(parsed.loc[0, "promotion_rank_score"]),
            float(exact_score),
        )
        self.assertEqual(
            exact.loc[0, "promotion_rank_score"],
            exact_score,
        )

    def test_three_rank_overlay_preimage_is_strict_and_fail_closed(self):
        path = self._write_three_rank_overlay()
        baseline = pd.read_csv(path)
        cases = []

        partial = baseline.drop(
            columns=["legacy_shadow_predicted_big_loss_probability"]
        )
        cases.append(("incomplete", partial, "preimage is incomplete"))

        nonfinite = baseline.copy(deep=True)
        nonfinite.loc[
            0, "legacy_shadow_predicted_big_loss_probability"
        ] = float("nan")
        cases.append(("nonfinite", nonfinite, "exact finite decimal"))

        profit_nonfinite = baseline.copy(deep=True)
        profit_nonfinite.loc[
            0, "legacy_shadow_predicted_profit_probability"
        ] = float("nan")
        cases.append(
            ("profit_nonfinite", profit_nonfinite, "exact finite decimal")
        )

        promotion_probability_nonfinite = baseline.copy(deep=True)
        promotion_probability_nonfinite.loc[
            1, "legacy_shadow_predicted_promotion_probability"
        ] = float("nan")
        cases.append(
            (
                "promotion_probability_nonfinite",
                promotion_probability_nonfinite,
                "exact finite decimal",
            )
        )

        rank_drift = baseline.copy(deep=True)
        rank_drift.loc[1, "legacy_shadow_promotion_rank"] = 2
        cases.append(("rank_drift", rank_drift, "promotion_rank"))

        unbound = baseline.copy(deep=True)
        unbound["three_engine_runtime_artifacts_hash_bound"] = 0
        unbound["three_engine_runtime_failure"] = "not_hash_bound"
        cases.append(("unbound", unbound, "must remain one"))

        promotion_not_ready = baseline.copy(deep=True)
        promotion_not_ready["promotion_model_status"] = (
            "NOT_READY_VALIDATION_GATE"
        )
        promotion_not_ready["three_engine_runtime_status"] = (
            "NOT_READY_PROMOTION"
        )
        cases.append(
            (
                "promotion_not_ready",
                promotion_not_ready,
                "READY promotion head",
            )
        )

        unauthorized_big_loss = baseline.copy(deep=True)
        unauthorized_big_loss.loc[1, "big_loss_safety_rank"] = 1
        unauthorized_big_loss.loc[1, "big_loss_rank_score"] = 0.7
        unauthorized_big_loss.loc[
            1, "predicted_big_loss_probability"
        ] = 0.2
        cases.append(
            (
                "unauthorized_big_loss",
                unauthorized_big_loss,
                "unauthorized value",
            )
        )

        for name, candidate, error in cases:
            with self.subTest(name=name):
                candidate.to_csv(path, index=False)
                with patch(
                    "top10decision.decision.three_rank."
                    "_three_engine_projection_is_complete",
                    return_value=True,
                ):
                    with self.assertRaisesRegex(
                        DecisionModelFreezeError,
                        error,
                    ):
                        self._validate_runtime()

        self._write_runtime()
        native = self._validate_runtime()["three_rank_canonical_preimage"]
        self.assertFalse(native["active"])
        self.assertEqual(native["source"], "native_canonical_prediction")

    def test_three_rank_action_watchlist_binds_to_overlay_promotion_order(self):
        path = self._write_three_rank_overlay()
        prediction = pd.read_csv(path)
        prediction["top10_selected"] = 1
        prediction.to_csv(path, index=False)

        action_path = self.root / "outputs/decision/action_plan_latest.json"
        action = json.loads(action_path.read_text(encoding="utf-8"))
        candidates = {
            row["ts_code"]: row for row in action["candidates"]
        }
        watchlist = []
        for rank, code in enumerate(("000002.SZ", "000001.SZ"), start=1):
            row = copy.deepcopy(candidates[code])
            row["stage_watch_rank"] = rank
            row["watch_label"] = (
                "二筛影子"
                if row["trade_shadow_selected"] == 1
                else "仅观察"
            )
            watchlist.append(row)
        action["stage_watchlist"] = watchlist
        action["stage_watch_count"] = 2
        action["stage_watch_eligible_count"] = 2
        _write_json(action_path, action)

        with patch(
            "top10decision.decision.three_rank."
            "_three_engine_projection_is_complete",
            return_value=True,
        ):
            audit = self._validate_action()
        self.assertEqual(
            audit["action_plan"]["runtime_binding"][
                "watchlist_rank_source"
            ],
            "three_engine_promotion_rank",
        )

        action["stage_watchlist"].reverse()
        _write_json(action_path, action)
        with patch(
            "top10decision.decision.three_rank."
            "_three_engine_projection_is_complete",
            return_value=True,
        ):
            with self.assertRaisesRegex(
                DecisionModelFreezeError,
                "promotion ranks|rank order",
            ):
                self._validate_action()

    def test_runtime_action_is_dynamic_but_bound_to_same_run_prediction(self):
        reference_sha = self.manifest["behavior_contract"]["action_watchlist"][
            "sha256"
        ]
        self.manifest["behavior_contract"]["action_watchlist"]["sha256"] = (
            "9" * 64
        )
        self._write_manifest()

        audit = self._validate_action()

        self.assertTrue(audit["validated"])
        action_audit = audit["action_plan"]
        self.assertEqual(
            action_audit["activation_reference"]["sha256"], "9" * 64
        )
        self.assertFalse(
            action_audit["activation_reference"]["runtime_equality_required"]
        )
        self.assertNotEqual(action_audit["watchlist"]["sha256"], "9" * 64)
        self.assertNotEqual(reference_sha, "9" * 64)
        self.assertTrue(
            action_audit["runtime_binding"]["candidate_rows_exact"]
        )

    def test_runtime_action_rejects_candidate_reorder_and_mixed_prediction_dates(self):
        action_path = self.root / "outputs/decision/action_plan_latest.json"
        action = json.loads(action_path.read_text())
        action["candidates"] = list(reversed(action["candidates"]))
        _write_json(action_path, action)
        with self.assertRaisesRegex(DecisionModelFreezeError, "row order"):
            self._validate_action()

        self._write_runtime()
        prediction_path = self.root / "outputs/auction_v3/predictions/pred_latest.csv"
        prediction = pd.read_csv(prediction_path, dtype={"signal_date": "string"})
        prediction.loc[1, "expected_buy_date"] = 20260819
        prediction.to_csv(prediction_path, index=False)
        with self.assertRaisesRegex(DecisionModelFreezeError, "must be uniform"):
            self._validate_action()

    def test_runtime_action_rejects_eval_and_action_date_chain_drift(self):
        eval_path = self.root / "outputs/decision/eval_20260817.json"
        evaluation = json.loads(eval_path.read_text())
        evaluation["exit_date"] = "20260819"
        _write_json(eval_path, evaluation)
        with self.assertRaisesRegex(DecisionModelFreezeError, "evaluation exit_date"):
            self._validate_action()

        self._write_runtime()
        action_path = self.root / "outputs/decision/action_plan_latest.json"
        action = json.loads(action_path.read_text())
        action["model"]["prediction_matches_report"] = False
        _write_json(action_path, action)
        with self.assertRaisesRegex(
            DecisionModelFreezeError, "prediction_matches_report"
        ):
            self._validate_action()

    def test_runtime_action_report_binds_exact_date_chain(self):
        report_path = self.root / "outputs/decision/decision_report_20260817.md"
        cases = (
            (
                "header_report_date",
                lambda text: text.replace(
                    "# Decision Report (20260817)",
                    "# Decision Report (20260818)",
                ),
                "report report_date differs",
            ),
            (
                "signal_date",
                lambda text: text.replace(
                    "- signal_date: **20260814**",
                    "- signal_date: **20260813**",
                ),
                "report signal_date differs",
            ),
            (
                "exec_date_not_exact",
                lambda text: text.replace(
                    "- exec_date: **20260817**",
                    "- exec_date: **exec=20260817**",
                ),
                "report exec_date must be an exact YYYYMMDD",
            ),
            (
                "duplicate_exit_date",
                lambda text: text + "- exit_date: **20260818**\n",
                "exactly one exit_date line",
            ),
        )
        for name, mutate, error in cases:
            with self.subTest(name=name):
                self._write_runtime()
                report_path.write_text(
                    mutate(report_path.read_text(encoding="utf-8")),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(DecisionModelFreezeError, error):
                    self._validate_action()

    def test_runtime_action_eval_rejects_duplicate_keys_and_date_substrings(self):
        eval_path = self.root / "outputs/decision/eval_20260817.json"
        raw = eval_path.read_text(encoding="utf-8")
        eval_path.write_text(
            '{"exec_date":"ATTACK",' + raw[1:], encoding="utf-8"
        )
        with self.assertRaisesRegex(DecisionModelFreezeError, "duplicate JSON key"):
            self._validate_action()

        self._write_runtime()
        evaluation = json.loads(eval_path.read_text(encoding="utf-8"))
        evaluation["signal_date"] = "signal=20260814"
        _write_json(eval_path, evaluation)
        with self.assertRaisesRegex(
            DecisionModelFreezeError,
            "evaluation signal_date must be an exact YYYYMMDD",
        ):
            self._validate_action()

    def test_runtime_action_plan_rejects_duplicate_keys(self):
        action_path = self.root / "outputs/decision/action_plan_latest.json"
        raw = action_path.read_text(encoding="utf-8")
        action_path.write_text(
            '{"status_code":"ATTACK",' + raw[1:], encoding="utf-8"
        )
        with self.assertRaisesRegex(DecisionModelFreezeError, "duplicate JSON key"):
            self._validate_action()

    def test_action_policy_recompute_rejects_joint_rank_and_is_reused(self):
        original = freeze_module._validate_prediction_policy_gates
        with patch.object(
            freeze_module,
            "_validate_prediction_policy_gates",
            wraps=original,
        ) as validator:
            standalone_audit = self._validate_action()
        self.assertEqual(validator.call_count, 1)
        self.assertTrue(
            standalone_audit["action_plan"]["prediction_policy_gates"][
                "trade_rank_exact"
            ]
        )

        self._write_runtime()
        with patch.object(
            freeze_module,
            "_validate_prediction_policy_gates",
            wraps=original,
        ) as validator:
            runtime_audit = self._validate_runtime()
        self.assertEqual(validator.call_count, 1)
        self.assertEqual(
            runtime_audit["action_plan"]["prediction_policy_gates"],
            runtime_audit["prediction_policy_gates"],
        )

        self._write_runtime()
        prediction_path = self.root / "outputs/auction_v3/predictions/pred_latest.csv"
        with prediction_path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames
            rows = list(reader)
        self.assertIsNotNone(fieldnames)
        rows[1]["trade_rank"] = "2"
        with prediction_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        action_path = self.root / "outputs/decision/action_plan_latest.json"
        action = json.loads(action_path.read_text(encoding="utf-8"))
        action["candidates"][1]["trade_rank"] = 2
        action["stage_watchlist"][0]["trade_rank"] = 2
        _write_json(action_path, action)
        with self.assertRaisesRegex(
            DecisionModelFreezeError,
            "prediction trade_rank disagrees with raw selector ordering",
        ):
            self._validate_action()

    def test_production_shape_without_backtest_model_policy_passes(self):
        backtest_path = self.root / "outputs/auction_v3/metrics/backtest_latest.json"
        backtest = json.loads(backtest_path.read_text(encoding="utf-8"))
        self.assertNotIn("selection_policy", backtest)
        prediction_path = self.root / "outputs/auction_v3/predictions/pred_latest.csv"
        prediction = pd.read_csv(prediction_path)
        for intermediate in (
            "limit_times",
            "diagnostic_gap",
            "recommended_max_gap",
        ):
            self.assertNotIn(intermediate, prediction.columns)

        action_audit = self._validate_action()
        self.assertTrue(action_audit["action_plan"]["model_raw_policy_match"])
        runtime_audit = self._validate_runtime()
        self.assertTrue(
            runtime_audit["execution_policy_relation"][
                "model_raw_meta_authoritative"
            ]
        )
        self.assertTrue(runtime_audit["action_plan"]["model_raw_policy_match"])

    def test_standalone_action_rejects_meta_and_prediction_formula_drift(self):
        meta_path = self.root / "outputs/auction_v3/models/model_meta_latest.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["selection_policy"]["thresholds"]["min_selection_score"] = (
            0.15000000000000002
        )
        _write_json(meta_path, meta)
        with self.assertRaisesRegex(
            DecisionModelFreezeError,
            "selection_policy differs from authoritative model_meta",
        ):
            self._validate_action()

        self._write_runtime()
        prediction_path = self.root / "outputs/auction_v3/predictions/pred_latest.csv"
        with prediction_path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames
            rows = list(reader)
        self.assertIsNotNone(fieldnames)
        rows[1]["trade_score"] = "0.1999"
        with prediction_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        with self.assertRaisesRegex(
            DecisionModelFreezeError,
            "trade_score disagrees with raw selector formula",
        ):
            self._validate_action()

    def test_standalone_action_contract_rejects_shallow_no_trade_bypasses(self):
        audit = self._validate_action()
        self.assertTrue(audit["validated"])
        self.assertTrue(audit["enforced"])

        mutations = (
            ("empty_candidates", lambda value: value.__setitem__("candidates", [])),
            (
                "model_version",
                lambda value: value["model"].__setitem__("version", "ATTACK"),
            ),
            (
                "selector_promoted",
                lambda value: value["model"]["trade_selector"].__setitem__(
                    "promoted", True
                ),
            ),
            (
                "target_weight",
                lambda value: value["candidates"][0].__setitem__(
                    "target_weight", 1.0
                ),
            ),
            (
                "trade_selected",
                lambda value: value["candidates"][0].__setitem__(
                    "trade_selected", 1
                ),
            ),
            (
                "market_order",
                lambda value: value["candidates"][0].__setitem__(
                    "market_order_allowed", 1
                ),
            ),
            (
                "watchlist_risk_gate",
                lambda value: value["stage_watchlist"][0].__setitem__(
                    "risk_gate_pass", 1
                ),
            ),
            (
                "order_type",
                lambda value: value["candidates"][0].__setitem__(
                    "order_type", "MARKET"
                ),
            ),
            (
                "recommended_price",
                lambda value: value["candidates"][0].__setitem__(
                    "recommended_max_price", 99.0
                ),
            ),
            (
                "guidance_only",
                lambda value: value.__setitem__("guidance_only", False),
            ),
        )
        action_path = self.root / "outputs/decision/action_plan_latest.json"
        for name, mutate in mutations:
            with self.subTest(name=name):
                self._write_runtime()
                value = json.loads(action_path.read_text())
                mutate(value)
                _write_json(action_path, value)
                with self.assertRaises(DecisionModelFreezeError):
                    self._validate_action()

        self._write_runtime()
        symlink_target = action_path.with_name("action_plan_target.json")
        symlink_target.write_bytes(action_path.read_bytes())
        action_path.unlink()
        action_path.symlink_to(symlink_target)
        with self.assertRaisesRegex(DecisionModelFreezeError, "symlink"):
            self._validate_action()

    def test_four_surface_runtime_passes_and_nested_oos_is_not_formal_trade(self):
        with self.assertRaisesRegex(DecisionModelFreezeError, "40,355-row SHA77e"):
            validate_runtime_artifacts(
                self.root, self.manifest, force_enforcement=True
            )
        audit = self._validate_runtime()
        self.assertTrue(audit["canonical_v2_enforced"])
        self.assertFalse(audit["legacy_v1_enforced"])
        self.assertEqual(audit["snapshot"]["source"], "forced_frozen_snapshot")
        self.assertEqual(
            audit["production_backtest_zero_values"]["production_backtest_signals"],
            0,
        )
        self.assertEqual(
            audit["behavior"]["decision_frame_counts"][
                "nested_oos_trade_selected_count"
            ],
            1,
        )
        self.assertEqual(
            audit["trade_selector"]["prediction_domain"][
                "observation_domain_rows"
            ],
            1,
        )
        self.assertEqual(
            audit["trade_selector"]["prediction_domain"]["outside_domain_rows"],
            1,
        )

    def test_raw_policy_surfaces_canonical_relation_and_gates_are_fail_closed(self):
        meta_path = self.root / "outputs/auction_v3/models/model_meta_latest.json"
        backtest_path = self.root / "outputs/auction_v3/metrics/backtest_latest.json"
        prediction_path = self.root / "outputs/auction_v3/predictions/pred_latest.csv"
        action_path = self.root / "outputs/decision/action_plan_latest.json"

        self._write_runtime()
        meta = json.loads(meta_path.read_text())
        meta["selection_policy"]["thresholds"]["min_selection_score"] = (
            0.15000000000000002
        )
        _write_json(meta_path, meta)
        with self.assertRaisesRegex(DecisionModelFreezeError, "raw execution policy"):
            self._validate_runtime()

        self._write_runtime()
        prediction = pd.read_csv(prediction_path)
        prediction.loc[0, "policy_min_selection_score"] = 0.15000000000000002
        prediction.to_csv(prediction_path, index=False)
        with self.assertRaisesRegex(DecisionModelFreezeError, "raw execution policy"):
            self._validate_runtime()

        self._write_runtime()
        meta = json.loads(meta_path.read_text())
        meta["selection_policy"]["thresholds"]["min_selection_score"] = (
            0.1500000004
        )
        _write_json(meta_path, meta)
        action = json.loads(action_path.read_text())
        action["model"]["selection_policy"]["thresholds"][
            "min_selection_score"
        ] = 0.1500000004
        _write_json(action_path, action)
        prediction = pd.read_csv(prediction_path)
        prediction["policy_min_selection_score"] = 0.1500000004
        prediction.to_csv(prediction_path, index=False)
        audit = self._validate_runtime()
        self.assertTrue(
            audit["execution_policy_relation"]["model_raw_canonical_q8_match"]
        )
        self.assertTrue(
            audit["prediction_model_policy_text_surface"]["exact_decimal_match"]
        )

        self._write_runtime()
        meta = json.loads(meta_path.read_text())
        meta["selection_policy"]["thresholds"]["min_selection_score"] = (
            0.15000002
        )
        _write_json(meta_path, meta)
        prediction = pd.read_csv(prediction_path)
        prediction["policy_min_selection_score"] = 0.15000002
        prediction.to_csv(prediction_path, index=False)
        with self.assertRaisesRegex(DecisionModelFreezeError, "canonicalize"):
            self._validate_runtime()

        self._write_runtime()
        for path in (meta_path, backtest_path):
            payload = json.loads(path.read_text())
            payload["trade_selector"]["production_policy"]["thresholds"][
                "min_trade_score"
            ] = 0.1000000004
            _write_json(path, payload)
        audit = self._validate_runtime()
        self.assertTrue(
            audit["execution_policy_relation"][
                "selector_raw_canonical_q8_match"
            ]
        )

        for column, row_number, value in (
            ("gate_selection_score", 0, 0.5),
            ("risk_gate_pass", 0, 1),
            ("trade_gate_pass", 1, 0),
            ("trade_selector_policy_ready", 1, 0.5),
        ):
            with self.subTest(column=column):
                self._write_runtime()
                prediction = pd.read_csv(prediction_path)
                prediction[column] = prediction[column].astype(object)
                prediction.loc[row_number, column] = value
                prediction.to_csv(prediction_path, index=False)
                with self.assertRaises(DecisionModelFreezeError):
                    self._validate_runtime()

    def test_prediction_stage_relationship_mutations_each_fail(self):
        prediction_path = self.root / "outputs/auction_v3/predictions/pred_latest.csv"
        mutations = (
            ("stage", "2\u21924"),
            ("stage_transition", "2\u21924"),
            ("stage_focus", 0),
            ("stage", "02\u21923"),
            ("stage", "2->3"),
            ("stage", "2\u219203"),
        )
        for column, value in mutations:
            with self.subTest(column=column, value=value):
                self._write_runtime()
                prediction = pd.read_csv(prediction_path)
                prediction[column] = prediction[column].astype(object)
                prediction.loc[0, column] = value
                prediction.to_csv(prediction_path, index=False)
                with self.assertRaisesRegex(
                    DecisionModelFreezeError, "stage"
                ):
                    self._validate_runtime()

    def test_prediction_execution_surface_mutations_each_fail(self):
        prediction_path = self.root / "outputs/auction_v3/predictions/pred_latest.csv"
        mutations = (
            ("action", 0, "BUY"),
            ("guidance_only", 0, 0),
            ("broker_connected", 0, 1),
            ("market_order_allowed", 0, 1),
            ("order_type", 0, "MARKET"),
            ("recommended_max_price", 0, 999.0),
            ("max_auction_change_pct", 0, 9.9),
        )
        for column, row_number, value in mutations:
            with self.subTest(column=column):
                self._write_runtime()
                prediction = pd.read_csv(prediction_path)
                prediction[column] = prediction[column].astype(object)
                prediction.loc[row_number, column] = value
                prediction.to_csv(prediction_path, index=False)
                with self.assertRaises(DecisionModelFreezeError):
                    self._validate_runtime()

        self._write_runtime()
        prediction = pd.read_csv(prediction_path).drop(columns=["action"])
        prediction.to_csv(prediction_path, index=False)
        with self.assertRaisesRegex(DecisionModelFreezeError, "missing columns"):
            self._validate_runtime()

    def test_raw_policy_rank_gate_shadow_and_formula_contracts_are_independent(self):
        prediction_path = self.root / "outputs/auction_v3/predictions/pred_latest.csv"
        model_raw = copy.deepcopy(
            self.model_layer["fingerprint_v2"]["policy_projection"]
        )
        model_raw["ready"] = True
        model_raw["max_positions"] = 1
        selector_raw = copy.deepcopy(
            self.selector_layer["fingerprint_v2"]["policy_projection"]
        )
        selector_raw["ready"] = False
        selector_raw["max_positions"] = 1

        self._write_runtime()
        template = pd.read_csv(prediction_path).iloc[1].to_dict()
        scores = (0.4, 0.3, 0.2, 0.1)
        selector_fills = (0.1, 0.8, 0.8, 0.8)
        selector_means = (4.0, 0.375, 0.25, 0.125)
        trade_scores = tuple(
            fill * mean
            for fill, mean in zip(selector_fills, selector_means)
        )
        promotion_scores = (0.1, 0.4, 0.3, 0.2)
        promotion_ranks = (4, 1, 2, 3)
        rows = []
        for index in range(4):
            row = dict(template)
            model_gate = int(scores[index] >= MODEL_THRESHOLDS["min_selection_score"])
            row.update(
                {
                    "signal_date": "20260814",
                    "ts_code": f"00000{index + 1}.SZ",
                    "stage_focus": 1,
                    "predicted_exit_probability": 0.9,
                    "predicted_fill_probability": 0.8,
                    "predicted_big_loss_probability": 0.1,
                    "predicted_mean_return_lcb": 0.1,
                    "predicted_return_lcb": 0.05 - index * 0.001,
                    "conservative_ev": 0.1,
                    "selection_score": scores[index],
                    "source_rank": index + 1,
                    "gate_policy_ready": 1,
                    "gate_stage_focus": 1,
                    "gate_exit_probability": 1,
                    "gate_fill_probability": 1,
                    "gate_big_loss_probability": 1,
                    "gate_mean_return_lcb": 1,
                    "gate_conservative_ev": 1,
                    "gate_selection_score": model_gate,
                    "risk_gate_pass": model_gate,
                    "recommended_max_price": 10.1 if model_gate else None,
                    "max_auction_change_pct": 1.0 if model_gate else None,
                    "model_reason": (
                        "ok" if model_gate else "selection_score_below_policy_cutoff"
                    ),
                    "shadow_rank": index + 1,
                    "shadow_selected": int(index < 2),
                    "first_layer_shadow_selected": int(index < 2),
                    "first_layer_selected": int(index == 0),
                    "observation_selected": 1,
                    "observation_rank": index + 1,
                    "promotion_rank_score": promotion_scores[index],
                    "promotion_rank": promotion_ranks[index],
                    "trade_rank": index + 1,
                    "trade_predicted_fill_probability": selector_fills[index],
                    "trade_predicted_mean_return_lcb": selector_means[index],
                    "trade_predicted_big_loss_probability": 0.1,
                    "trade_predicted_outcome_q10": 0.1,
                    "trade_tail_loss_proxy": 0.0,
                    "trade_base_score": trade_scores[index],
                    "trade_tail_risk_weight": 0.25,
                    "trade_score": trade_scores[index],
                    "trade_gate_pass": int(index == 1),
                    "trade_shadow_selected": int(index < 2),
                    "trade_selected": 0,
                    "selected": 0,
                    "trade_selector_policy_ready": 0,
                    "trade_selector_promoted": 0,
                    "action": "SHADOW_ONLY" if index < 2 else "REJECT",
                    "trade_model_reason": (
                        "relative_best_two_only"
                        if index < 2
                        else "below_learned_policy"
                    ),
                }
            )
            rows.append(row)

        def validate(frame: pd.DataFrame) -> dict:
            frame.to_csv(prediction_path, index=False)
            parsed = freeze_module._read_csv(prediction_path, "mixed prediction")
            text = freeze_module._read_csv_exact_text(
                prediction_path, "mixed prediction text"
            )
            return freeze_module._validate_prediction_policy_gates(
                parsed,
                text,
                model_raw_projection=model_raw,
                selector_raw_projection=selector_raw,
            )

        baseline = pd.DataFrame(rows)
        for intermediate in (
            "limit_times",
            "diagnostic_gap",
            "recommended_max_gap",
        ):
            self.assertNotIn(intermediate, baseline.columns)
        audit = validate(baseline)
        self.assertEqual(audit["selector_trade_gate_pass_rows"], 1)
        self.assertTrue(audit["trade_base_and_tail_formula_exact"])
        self.assertTrue(audit["stage_transition_exact"])
        self.assertTrue(audit["persisted_execution_surface_exact"])

        mutations = []
        for column, row_number, value in (
            ("trade_tail_risk_weight", 0, 0.2500000000000001),
            ("trade_base_score", 0, 0.4000000000000001),
            ("trade_tail_loss_proxy", 0, 0.0000000000000001),
            ("trade_score", 0, 0.4000000000000001),
            ("promotion_rank", 0, 1),
            ("trade_selected", 1, 1),
            ("model_reason", 0, "selection_policy_not_ready"),
            ("trade_model_reason", 1, "shadow_policy_only"),
            ("gate_selection_score", 0, 0.5),
            ("stage", 0, "02\u21923"),
            ("recommended_max_price", 0, 10.2),
            ("max_auction_change_pct", 0, 2.0),
        ):
            changed = baseline.copy(deep=True)
            changed[column] = changed[column].astype(object)
            changed.loc[row_number, column] = value
            mutations.append((column, changed))
        for label, left, right in (
            ("trade_rank_swap", (0, "trade_rank"), (1, "trade_rank")),
            ("shadow_rank_swap", (0, "shadow_rank"), (2, "shadow_rank")),
            ("trade_gate_swap", (1, "trade_gate_pass"), (2, "trade_gate_pass")),
            (
                "trade_shadow_swap",
                (0, "trade_shadow_selected"),
                (2, "trade_shadow_selected"),
            ),
            (
                "first_shadow_swap",
                (0, "first_layer_shadow_selected"),
                (2, "first_layer_shadow_selected"),
            ),
            (
                "first_selected_swap",
                (0, "first_layer_selected"),
                (1, "first_layer_selected"),
            ),
        ):
            changed = baseline.copy(deep=True)
            changed.loc[left[0], left[1]], changed.loc[right[0], right[1]] = (
                changed.loc[right[0], right[1]],
                changed.loc[left[0], left[1]],
            )
            mutations.append((label, changed))
        changed = baseline.copy(deep=True)
        changed.loc[0, "trade_base_score"] = 0.41
        changed.loc[0, "trade_score"] = 0.41
        mutations.append(("base_and_score_synchronized", changed))
        changed = baseline.copy(deep=True)
        changed.loc[0, "trade_tail_loss_proxy"] = -0.001
        changed.loc[0, "trade_score"] = 0.399975
        mutations.append(("tail_and_score_synchronized", changed))
        for label, changed in mutations:
            with self.subTest(mutation=label):
                with self.assertRaises(DecisionModelFreezeError):
                    validate(changed)

    def test_observation_membership_rank_risk_and_pool_are_recomputed(self):
        prediction_path = self.root / "outputs/auction_v3/predictions/pred_latest.csv"
        mutations = (
            (0, "observation_selected", 1),
            (1, "observation_rank", 2),
            (1, "observation_risk_tier", 2),
            (1, "observation_risk_label", "观察风险可控 "),
            (0, "observation_pool_size", 2),
            (1, "stage_transition", "4→5"),
            (1, "mechanism_limit_pct", 10.01),
        )
        for row_number, column, value in mutations:
            with self.subTest(row=row_number, column=column):
                self._write_runtime()
                prediction = pd.read_csv(prediction_path)
                prediction[column] = prediction[column].astype(object)
                prediction.loc[row_number, column] = value
                prediction.to_csv(prediction_path, index=False)
                with self.assertRaises(DecisionModelFreezeError):
                    self._validate_runtime()

    def test_legacy_v1_mismatch_is_audit_only(self):
        meta_path = self.root / "outputs/auction_v3/models/model_meta_latest.json"
        backtest_path = self.root / "outputs/auction_v3/metrics/backtest_latest.json"
        meta = json.loads(meta_path.read_text())
        backtest = json.loads(backtest_path.read_text())
        meta["model_artifact_sha256"] = "9" * 64
        backtest["model_artifact_sha256"] = "9" * 64
        _write_json(meta_path, meta)
        _write_json(backtest_path, backtest)
        audit = self._validate_runtime()
        self.assertFalse(audit["legacy_v1_audit"]["all_match"])
        self.assertTrue(audit["validated"])

        self.manifest["production"]["legacy_v1_audit"][
            "trade_selector_artifact_sha256"
        ] = "8" * 64
        audit = self._validate_runtime()
        self.assertFalse(
            audit["legacy_v1_audit"]["matches"]["selector_prediction"]
        )
        self.assertTrue(audit["validated"])

    def test_selector_v1_must_match_same_run_surfaces_but_not_manifest_pin(self):
        backtest_path = self.root / "outputs/auction_v3/metrics/backtest_latest.json"
        backtest = json.loads(backtest_path.read_text())
        backtest["trade_selector"]["production_artifact_sha256"] = "8" * 64
        _write_json(backtest_path, backtest)
        with self.assertRaisesRegex(DecisionModelFreezeError, "same-run"):
            self._validate_runtime()

        self._write_runtime()
        prediction_path = self.root / "outputs/auction_v3/predictions/pred_latest.csv"
        prediction = pd.read_csv(prediction_path)
        prediction.loc[1, "trade_selector_artifact_sha256"] = "a" * 64
        prediction.to_csv(prediction_path, index=False)
        with self.assertRaisesRegex(DecisionModelFreezeError, "same-run"):
            self._validate_runtime()

    def test_fully_consistent_runtime_resign_cannot_move_the_frozen_v2_pin(self):
        drift = copy.deepcopy(self.model_layer)
        projection = drift["fingerprint_v2"]["policy_projection"]
        projection["thresholds"]["min_fill_probability"] += 2e-8
        policy_sha = canonical_mapping_sha256(
            {
                "schema": CANONICAL_FINGERPRINT_SCHEMA,
                "artifact_kind": "decision_model_executable_policy",
                "projection": projection,
            },
            decimals=8,
            exact_strings=True,
        )
        drift["fingerprint_v2"]["policy_sha256"] = policy_sha
        artifact = compose_artifact_fingerprint(
            artifact_kind="decision_model_canonical_runtime_v2",
            provenance_sha256=drift["fingerprint_v2"]["provenance_sha256"],
            semantic_sha256=drift["fingerprint_v2"]["semantic_sha256"],
            policy_sha256=policy_sha,
            decimals=8,
        )
        drift["artifact_v2_sha256"] = artifact
        drift["fingerprint_v2"]["artifact_sha256"] = artifact

        for path in (
            self.root / "outputs/auction_v3/models/model_meta_latest.json",
            self.root / "outputs/auction_v3/metrics/backtest_latest.json",
        ):
            payload = json.loads(path.read_text())
            payload["model_artifact_v2_sha256"] = artifact
            payload["model_fingerprint_v2"] = drift["fingerprint_v2"]
            _write_json(path, payload)
        prediction_path = self.root / "outputs/auction_v3/predictions/pred_latest.csv"
        prediction = pd.read_csv(prediction_path)
        prediction["model_artifact_v2_sha256"] = artifact
        prediction.to_csv(prediction_path, index=False)
        action_path = self.root / "outputs/decision/action_plan_latest.json"
        action = json.loads(action_path.read_text())
        action["model"]["artifact_v2_sha256"] = artifact
        action["model"]["fingerprint_v2"] = drift["fingerprint_v2"]
        _write_json(action_path, action)

        with self.assertRaisesRegex(
            DecisionModelFreezeError,
            "differs across manifest/meta/backtest",
        ):
            self._validate_runtime()

    def test_meta_backtest_prediction_and_action_mutations_each_fail(self):
        meta_path = self.root / "outputs/auction_v3/models/model_meta_latest.json"
        meta = json.loads(meta_path.read_text())
        meta["model_canonical_v2_version"] = "drift"
        _write_json(meta_path, meta)
        with self.assertRaises(DecisionModelFreezeError):
            self._validate_runtime()
        self._write_runtime()

        backtest_path = self.root / "outputs/auction_v3/metrics/backtest_latest.json"
        backtest = json.loads(backtest_path.read_text())
        backtest["trade_selector"]["canonical_contract"]["execution_mode"] = "q8"
        _write_json(backtest_path, backtest)
        with self.assertRaises(DecisionModelFreezeError):
            self._validate_runtime()
        self._write_runtime()

        prediction_path = self.root / "outputs/auction_v3/predictions/pred_latest.csv"
        prediction = pd.read_csv(prediction_path)
        prediction.loc[1, "model_canonical_decimals"] = 10
        prediction.to_csv(prediction_path, index=False)
        with self.assertRaises(DecisionModelFreezeError):
            self._validate_runtime()
        self._write_runtime()

        action_path = self.root / "outputs/decision/action_plan_latest.json"
        action = json.loads(action_path.read_text())
        action["model"]["canonical_contracts_match"] = False
        _write_json(action_path, action)
        with self.assertRaises(DecisionModelFreezeError):
            self._validate_runtime()
        self._write_runtime()

        action = json.loads(action_path.read_text())
        action["model"]["version"] = "model-drift"
        _write_json(action_path, action)
        with self.assertRaisesRegex(DecisionModelFreezeError, "model version"):
            self._validate_runtime()
        self._write_runtime()

        action = json.loads(action_path.read_text())
        action["model"]["trade_selector"]["version"] = "selector-drift"
        _write_json(action_path, action)
        with self.assertRaisesRegex(DecisionModelFreezeError, "selector version"):
            self._validate_runtime()

    def test_final_prediction_policy_version_ready_positions_and_thresholds_fail(self):
        prediction_path = self.root / "outputs/auction_v3/predictions/pred_latest.csv"
        projection = self.model_layer["fingerprint_v2"]["policy_projection"]
        mutations = [
            ("selection_policy_version", "model-policy-drift"),
            ("gate_policy_ready", 1),
            ("policy_max_positions", int(projection["max_positions"]) + 1),
        ]
        mutations.extend(
            (f"policy_{name}", float(value) + 0.01)
            for name, value in projection["thresholds"].items()
        )
        self.assertEqual(len(mutations), 9)
        for column, value in mutations:
            with self.subTest(column=column):
                self._write_runtime()
                prediction = pd.read_csv(prediction_path)
                prediction.loc[0, column] = value
                prediction.to_csv(prediction_path, index=False)
                with self.assertRaises(DecisionModelFreezeError):
                    self._validate_runtime()

    def test_prediction_fill_relationship_mutations_each_fail(self):
        prediction_path = self.root / "outputs/auction_v3/predictions/pred_latest.csv"
        mutations = (
            ("predicted_fill_probability", 0, 1.1),
            ("predicted_public_market_buyable_probability", 0, -0.1),
            ("trade_predicted_fill_probability", 1, float("nan")),
            (
                "trade_predicted_public_market_buyable_probability",
                1,
                float("inf"),
            ),
            ("actual_order_fill_probability_available", 0, 2),
            ("predicted_actual_order_fill_probability", 1, 1.1),
        )
        for column, row_number, value in mutations:
            with self.subTest(column=column):
                self._write_runtime()
                prediction = pd.read_csv(prediction_path)
                prediction.loc[row_number, column] = value
                prediction.to_csv(prediction_path, index=False)
                with self.assertRaises(DecisionModelFreezeError):
                    self._validate_runtime()

    def test_prediction_fill_equalities_and_missing_availability_are_exact(self):
        prediction_path = self.root / "outputs/auction_v3/predictions/pred_latest.csv"
        mutations = (
            ("predicted_public_market_buyable_probability", 0, 0.250000001),
            (
                "trade_predicted_public_market_buyable_probability",
                1,
                0.400000001,
            ),
            ("actual_order_fill_probability_available", 0, 1),
            ("actual_order_fill_probability_available", 1, 0),
            ("predicted_actual_order_fill_probability", 0, 0.2),
            ("predicted_actual_order_fill_probability", 1, float("nan")),
        )
        for column, row_number, value in mutations:
            with self.subTest(column=column, row=row_number):
                self._write_runtime()
                prediction = pd.read_csv(prediction_path)
                prediction.loc[row_number, column] = value
                prediction.to_csv(prediction_path, index=False)
                with self.assertRaises(DecisionModelFreezeError):
                    self._validate_runtime()

    def test_prediction_fill_columns_and_nonempty_rows_are_schema_hard(self):
        prediction_path = self.root / "outputs/auction_v3/predictions/pred_latest.csv"
        fill_columns = freeze_module.PREDICTION_FILL_RELATIONSHIP_COLUMNS
        for column in fill_columns:
            with self.subTest(missing_column=column):
                self._write_runtime()
                prediction = pd.read_csv(prediction_path).drop(columns=[column])
                prediction.to_csv(prediction_path, index=False)
                with self.assertRaisesRegex(DecisionModelFreezeError, "missing"):
                    self._validate_runtime()
        self._write_runtime()
        prediction = pd.read_csv(prediction_path).iloc[0:0]
        prediction.to_csv(prediction_path, index=False)
        with self.assertRaisesRegex(DecisionModelFreezeError, "must not be empty"):
            self._validate_runtime()

    def test_prediction_selector_domain_and_outside_mutations_each_fail(self):
        prediction_path = self.root / "outputs/auction_v3/predictions/pred_latest.csv"
        mutations = (
            (0, "trade_selector_artifact_v2_sha256", "1" * 64),
            (0, "trade_selector_artifact_sha256", "f" * 64),
            (0, "trade_selector_canonical_v2_version", "selector-drift"),
            (0, "promotion_rank", 1),
            (0, "trade_gate_pass", 1),
            (0, "trade_model_reason", "outside_observation_top10 "),
            (0, "trade_selector_version", "selector-drift"),
            (1, "trade_selector_artifact_v2_sha256", float("nan")),
            (1, "trade_selector_artifact_sha256", float("nan")),
            (0, "trade_predicted_public_market_buyable_probability", 0.2),
            (1, "trade_selected", 1),
            (1, "trade_selector_promoted", 1),
            (1, "trade_shadow_selected", 0),
        )
        for row_number, column, value in mutations:
            with self.subTest(row=row_number, column=column):
                self._write_runtime()
                prediction = pd.read_csv(prediction_path)
                prediction.loc[row_number, column] = value
                prediction.to_csv(prediction_path, index=False)
                with self.assertRaises(DecisionModelFreezeError):
                    self._validate_runtime()

        self._write_runtime()
        prediction = pd.read_csv(prediction_path)
        prediction = pd.concat(
            [prediction, prediction.iloc[[1]].copy()], ignore_index=True
        )
        prediction.loc[2, "trade_shadow_selected"] = 0
        prediction.loc[2, "trade_selector_artifact_v2_sha256"] = "1" * 64
        prediction.to_csv(prediction_path, index=False)
        with self.assertRaises(DecisionModelFreezeError):
            self._validate_runtime()

    def test_missing_action_plan_buy_nonzero_target_and_watch_label_fail(self):
        action_path = self.root / "outputs/decision/action_plan_latest.json"
        action_path.unlink()
        with self.assertRaises(DecisionModelFreezeError):
            self._validate_runtime()
        self._write_runtime()

        action = json.loads(action_path.read_text())
        action["candidates"][0]["action"] = "BUY"
        _write_json(action_path, action)
        with self.assertRaises(DecisionModelFreezeError):
            self._validate_runtime()
        self._write_runtime()

        action = json.loads(action_path.read_text())
        action["stage_watchlist"][0]["target_weight"] = 0.1
        _write_json(action_path, action)
        with self.assertRaises(DecisionModelFreezeError):
            self._validate_runtime()
        self._write_runtime()

        action = json.loads(action_path.read_text())
        action["stage_watchlist"][0]["target_weight"] = False
        _write_json(action_path, action)
        with self.assertRaises(DecisionModelFreezeError):
            self._validate_runtime()
        self._write_runtime()

        action = json.loads(action_path.read_text())
        action["candidates"][0]["target_weight"] = False
        _write_json(action_path, action)
        with self.assertRaises(DecisionModelFreezeError):
            self._validate_runtime()
        self._write_runtime()

        action = json.loads(action_path.read_text())
        action["candidates"][0]["target_weight"] = "0.0"
        _write_json(action_path, action)
        with self.assertRaises(DecisionModelFreezeError):
            self._validate_runtime()
        self._write_runtime()

        action = json.loads(action_path.read_text())
        action["stage_watchlist"][0]["stage_watch_rank"] = 1.0
        _write_json(action_path, action)
        with self.assertRaises(DecisionModelFreezeError):
            self._validate_runtime()
        self._write_runtime()

        action = json.loads(action_path.read_text())
        action["stage_watchlist"][0]["trade_shadow_selected"] = 1.0
        _write_json(action_path, action)
        with self.assertRaises(DecisionModelFreezeError):
            self._validate_runtime()
        self._write_runtime()

        action = json.loads(action_path.read_text())
        action["candidates"][1]["trade_shadow_selected"] = 0
        _write_json(action_path, action)
        with self.assertRaisesRegex(DecisionModelFreezeError, "relative-best-two"):
            self._validate_runtime()
        self._write_runtime()

        action = json.loads(action_path.read_text())
        action["stage_watchlist"][0]["trade_shadow_selected"] = 0
        _write_json(action_path, action)
        with self.assertRaisesRegex(DecisionModelFreezeError, "relative-best-two"):
            self._validate_runtime()
        self._write_runtime()

        action = json.loads(action_path.read_text())
        action["candidates"][1]["ts_code"] = "000003.SZ"
        _write_json(action_path, action)
        with self.assertRaisesRegex(DecisionModelFreezeError, "matching candidate"):
            self._validate_runtime()
        self._write_runtime()

        action = json.loads(action_path.read_text())
        action["stage_watchlist"][0]["watch_label"] = "二筛影子\u00a0"
        _write_json(action_path, action)
        with self.assertRaises(DecisionModelFreezeError):
            self._validate_runtime()

    def test_top_level_production_zeros_cannot_be_confused_with_nested_oos(self):
        backtest_path = self.root / "outputs/auction_v3/metrics/backtest_latest.json"
        backtest = json.loads(backtest_path.read_text())
        self.assertEqual(
            backtest["trade_selector"]["formal_policy_oos"]["all_candidates"][
                "signals"
            ],
            1,
        )
        backtest["signals"] = 158
        _write_json(backtest_path, backtest)
        with self.assertRaisesRegex(DecisionModelFreezeError, "production_backtest_signals"):
            self._validate_runtime()

    def test_nested_oos_research_metrics_are_nonformal_but_exact(self):
        audit = self._validate_runtime()
        self.assertFalse(audit["nested_oos_research"]["formal_authorization"])
        backtest_path = self.root / "outputs/auction_v3/metrics/backtest_latest.json"
        backtest = json.loads(backtest_path.read_text())
        backtest["trade_selector"]["formal_policy_oos"]["market_buyable_only"][
            "filled_trades"
        ] = 0
        _write_json(backtest_path, backtest)
        with self.assertRaisesRegex(DecisionModelFreezeError, "nested OOS"):
            self._validate_runtime()


EXPECTED_ADAPTER_TOP10_DISCRETE = (
    "stage",
    "stage_focus",
    "policy_max_positions",
    "observation_rank",
    "observation_selected",
    "observation_risk_tier",
    "observation_risk_label",
    "shadow_rank",
    "shadow_selected",
    "selected",
    "model_reason",
    "selection_policy_version",
    "gate_policy_ready",
    "gate_stage_focus",
    "gate_exit_probability",
    "gate_fill_probability",
    "gate_big_loss_probability",
    "gate_mean_return_lcb",
    "gate_conservative_ev",
    "gate_selection_score",
    "risk_gate_pass",
)
EXPECTED_ADAPTER_TOP10_SCORES = (
    "predicted_net_return",
    "predicted_return_lcb",
    "predicted_return_ucb",
    "predicted_mean_return_lcb",
    "predicted_mean_return_ucb",
    "predicted_outcome_q10",
    "predicted_outcome_q90",
    "predicted_profit_probability",
    "predicted_big_loss_probability",
    "predicted_continuation_limit_up_probability",
    "predicted_fill_probability",
    "predicted_exit_probability",
    "conservative_ev",
    "selection_score",
    "diagnostic_gap",
    "recommended_max_gap",
    "policy_max_big_loss_probability",
    "policy_min_mean_return_lcb",
    "policy_min_fill_probability",
    "policy_min_exit_probability",
    "policy_min_conservative_ev",
    "policy_min_selection_score",
)
EXPECTED_ADAPTER_OOS_DISCRETE = (
    "stage",
    "stage_focus",
    "policy_max_positions",
    "observation_rank",
    "observation_selected",
    "observation_risk_tier",
    "observation_risk_label",
    "promotion_rank",
    "trade_rank",
    "trade_gate_pass",
    "trade_selected",
    "trade_shadow_selected",
    "trade_model_reason",
    "shadow_rank",
    "shadow_selected",
    "selected",
    "model_reason",
    "selection_policy_version",
    "trade_selector_promoted",
    "trade_selector_globally_promoted",
    "trade_selector_policy_ready",
    "gate_policy_ready",
    "gate_stage_focus",
    "gate_exit_probability",
    "gate_fill_probability",
    "gate_big_loss_probability",
    "gate_mean_return_lcb",
    "gate_conservative_ev",
    "gate_selection_score",
    "risk_gate_pass",
)
EXPECTED_ADAPTER_OOS_SCORES = (
    *EXPECTED_ADAPTER_TOP10_SCORES,
    "trade_predicted_conditional_net_return",
    "trade_predicted_mean_return_lcb",
    "trade_predicted_fill_probability",
    "trade_predicted_big_loss_probability",
    "promotion_rank_score",
    "predicted_promotion_probability",
    "trade_predicted_outcome_q10",
    "trade_tail_loss_proxy",
    "trade_tail_risk_weight",
    "trade_base_score",
    "trade_score",
)


class DiagnoseActivationEvidenceAdapterTest(unittest.TestCase):
    def test_diagnose_workflow_has_explicit_post_replay_runtime_honest_gate(self):
        workflow = (
            diagnose.ROOT / ".github/workflows/diagnose_decision_fingerprint.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("Checkout immutable C6 frozen references", workflow)
        self.assertIn(
            "ref: c6de497aaab48c40e205aa7fe8401ad6ad9780ad",
            workflow,
        )
        self.assertIn("path: _dc20_fingerprint_reference_c6", workflow)
        self.assertIn("sparse-checkout-cone-mode: false", workflow)
        self.assertIn(
            'expected_reference_commit="c6de497aaab48c40e205aa7fe8401ad6ad9780ad"',
            workflow,
        )
        self.assertIn("persisted_reference_commit_verified=false", workflow)
        reference_checkout = (
            '"${{reference_checkout}}/outputs/auction_v3/{path}"'
        )
        for path in (
            "metrics/backtest_top10_latest.csv",
            "metrics/backtest_trade_selector_oos_latest.csv",
            "metrics/backtest_latest.json",
            "models/model_meta_latest.json",
        ):
            self.assertIn(reference_checkout.format(path=path), workflow)
        for current_runtime_blob in (
            "36f138c46c50ec07a192f0ce4072e68ed22e6d08",
            "bc45b2969a3f77d07deb474bcdee77abf0bc0ca1",
            "d9697f8946f8651444d21ebb0f05f831d789086b",
            "64e80ab35ad0593fd8ba8a4f9ab779c51d5832a1",
        ):
            self.assertNotIn(current_runtime_blob, workflow)
        self.assertIn("id: runtime", workflow)
        self.assertIn(
            "validate_decision_model_freeze.py --runtime --force-inactive",
            workflow,
        )
        self.assertIn(
            "87605814bce9f2180e151ed91d6c16e2c22b46c3dcd147e8bdab7895c3f0975a",
            workflow,
        )
        self.assertIn('"exact_v1_loader"', workflow)
        self.assertIn('"live_policy_exact"', workflow)
        self.assertIn('"prediction_domain"', workflow)
        self.assertIn('"action_formal_zero"', workflow)
        self.assertIn("RUNTIME_OUTCOME: ${{ steps.runtime.outcome }}", workflow)
        self.assertIn('if [ "${RUNTIME_OUTCOME}" != "success" ]', workflow)

    def test_shared_exact_envelope_and_order_are_snapshotted(self):
        self.assertEqual(
            freeze_module.FINGERPRINT_KEYS,
            frozenset(
                {
                    "schema",
                    "canonical_version",
                    "canonical_contract",
                    "provenance_sha256",
                    "semantic_sha256",
                    "policy_sha256",
                    "policy_projection",
                    "artifact_sha256",
                    "schema_valid",
                    "missing_columns",
                    "invalid_cell_count",
                }
            ),
        )
        self.assertEqual(
            tuple(diagnose.FREEZE_IDENTITY_COLUMNS), ("signal_date", "ts_code")
        )
        self.assertEqual(
            tuple(diagnose.FREEZE_TOP10_DISCRETE_COLUMNS),
            EXPECTED_ADAPTER_TOP10_DISCRETE,
        )
        self.assertEqual(
            tuple(diagnose.FREEZE_TOP10_SCORE_COLUMNS),
            EXPECTED_ADAPTER_TOP10_SCORES,
        )
        self.assertEqual(
            tuple(diagnose.FREEZE_OOS_DISCRETE_COLUMNS),
            EXPECTED_ADAPTER_OOS_DISCRETE,
        )
        self.assertEqual(
            tuple(diagnose.FREEZE_OOS_SCORE_COLUMNS),
            EXPECTED_ADAPTER_OOS_SCORES,
        )
        self.assertEqual(
            tuple(diagnose.ACTION_WATCHLIST_COLUMNS),
            (
                "ts_code",
                "action",
                "stage_watch_rank",
                "watch_label",
                "target_weight",
            ),
        )

    def test_newly_locked_behavior_columns_rotate_exact_hashes(self):
        top10 = _top10_frame()
        top_contract = {
            "identity_columns": list(diagnose.FREEZE_IDENTITY_COLUMNS),
            "discrete_columns": list(diagnose.FREEZE_TOP10_DISCRETE_COLUMNS),
            "score_columns": list(diagnose.FREEZE_TOP10_SCORE_COLUMNS),
            "score_decimals": 8,
        }
        before_top = diagnose.compute_behavior_fingerprints(
            top10, top_contract, context="adapter.top10"
        )
        changed_top = top10.copy()
        changed_top.loc[0, "selection_policy_version"] += " "
        after_top = diagnose.compute_behavior_fingerprints(
            changed_top, top_contract, context="adapter.top10.changed"
        )
        self.assertNotEqual(
            before_top["discrete_sha256"], after_top["discrete_sha256"]
        )

        oos = _oos_frame()
        oos_contract = {
            "identity_columns": list(diagnose.FREEZE_IDENTITY_COLUMNS),
            "discrete_columns": list(diagnose.FREEZE_OOS_DISCRETE_COLUMNS),
            "score_columns": list(diagnose.FREEZE_OOS_SCORE_COLUMNS),
            "score_decimals": 8,
        }
        before_oos = diagnose.compute_behavior_fingerprints(
            oos, oos_contract, context="adapter.oos"
        )
        changed_oos = oos.copy()
        changed_oos.loc[0, "trade_tail_risk_weight"] += 2e-8
        after_oos = diagnose.compute_behavior_fingerprints(
            changed_oos, oos_contract, context="adapter.oos.changed"
        )
        self.assertNotEqual(before_oos["scores_sha256"], after_oos["scores_sha256"])

    def test_action_hash_is_only_the_shared_five_column_projection(self):
        action = {
            "stage_watchlist": [
                {
                    "ts_code": "000001.SZ",
                    "action": "SHADOW_ONLY",
                    "stage_watch_rank": 1,
                    "watch_label": "二筛影子",
                    "target_weight": 0.0,
                    "trade_shadow_selected": 1,
                    "trade_rank": 99,
                },
                {
                    "ts_code": "600001.SH",
                    "action": "REJECT",
                    "stage_watch_rank": 2,
                    "watch_label": "仅观察",
                    "target_weight": 0.0,
                    "trade_shadow_selected": 0,
                    "trade_rank": 88,
                },
            ]
        }
        contract = {"columns": list(diagnose.ACTION_WATCHLIST_COLUMNS)}
        before = diagnose.compute_action_watchlist_fingerprint(action, contract)
        excluded_change = copy.deepcopy(action)
        excluded_change["stage_watchlist"][0]["trade_rank"] = 1
        self.assertEqual(
            before["sha256"],
            diagnose.compute_action_watchlist_fingerprint(
                excluded_change, contract
            )["sha256"],
        )
        included_change = copy.deepcopy(action)
        included_change["stage_watchlist"][0]["target_weight"] = 2e-8
        self.assertNotEqual(
            before["sha256"],
            diagnose.compute_action_watchlist_fingerprint(
                included_change, contract
            )["sha256"],
        )

    def test_current_source_six_matches_reviewed_activation_or_new_freeze(self):
        actual = {
            path: diagnose._sha256(diagnose.ROOT / path)
            for path in diagnose.ACTIVATION_SOURCE_PATHS
        }
        manifest = json.loads(
            (diagnose.ROOT / "models/decision_model_freeze.json").read_text(
                encoding="utf-8"
            )
        )
        three_rank = (manifest.get("production") or {}).get("three_rank")
        if three_rank is None:
            # The historical activation evidence remains byte-for-byte exact
            # until a separately signed three-rank freeze supersedes the live
            # source surface.
            self.assertEqual(actual, REVIEWED_ACTIVATION_SOURCE_FILES)
            self.assertEqual(
                diagnose._activation_source6_sha256(actual),
                diagnose.EXPECTED_ACTIVATION_SOURCE6_SHA256,
            )
        else:
            # Do not rewrite the historical evidence constants.  The upgraded
            # live files are instead authorized by the new active pin set.
            pins = manifest.get("pinned_files") or {}
            self.assertEqual(
                actual,
                {path: pins.get(path) for path in diagnose.ACTIVATION_SOURCE_PATHS},
            )

    def test_source_six_drift_and_candidate_alias_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = {}
            for index, relative in enumerate(diagnose.ACTIVATION_SOURCE_PATHS):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f"source-{index}".encode())
                expected[relative] = diagnose._sha256(path)
            report = {
                "candidate_source": {
                    "candidate_commit": "a" * 40,
                    "file_sha256": expected,
                }
            }
            aggregate = diagnose._activation_source6_sha256(expected)
            with patch.object(
                diagnose,
                "EXPECTED_ACTIVATION_SOURCE6_SHA256",
                aggregate,
            ):
                result = diagnose._activation_source_evidence(root, report)
                self.assertEqual(result["file_sha256"], expected)
                self.assertEqual(result["sha256"], aggregate)
                (root / diagnose.ACTIVATION_SOURCE_PATHS[0]).write_bytes(b"drift")
                with self.assertRaisesRegex(RuntimeError, "six-file SHA map"):
                    diagnose._activation_source_evidence(root, report)
                aliased = copy.deepcopy(report)
                aliased["candidate_source"]["base_commit"] = aliased[
                    "candidate_source"
                ].pop("candidate_commit")
                with self.assertRaisesRegex(RuntimeError, "keys drifted"):
                    diagnose._activation_source_evidence(root, aliased)

    def test_persisted_nested_counts_fail_closed(self):
        top10 = _top10_frame()
        oos = _oos_frame()
        watchlist = []
        for rank in range(1, 10):
            shadow = rank <= 2
            watchlist.append(
                {
                    "ts_code": f"{rank:06d}.SZ",
                    "action": "SHADOW_ONLY" if shadow else "REJECT",
                    "stage_watch_rank": rank,
                    "watch_label": "二筛影子" if shadow else "仅观察",
                    "target_weight": 0.0,
                    "trade_shadow_selected": int(shadow),
                }
            )
        action = {
            "status_code": "NO_TRADE_MODEL_NOT_PROMOTED",
            "formal_buy_count": 0,
            "stage_watchlist": watchlist,
        }
        backtest = {
            "promoted": False,
            "signals": 0,
            "signal_dates": 0,
            "filled_trades": 0,
            "trade_selector": {
                "promoted": False,
                "formal_policy_oos": {
                    "all_candidates": {
                        "signals": 1,
                        "signal_dates": 1,
                        "filled_trades": 1,
                    },
                    "market_buyable_only": {"filled_trades": 0},
                }
            },
        }
        constants = {
            "KNOWN_TOP10_ROWS": 2,
            "KNOWN_TOP10_DATES": 2,
            "KNOWN_OOS_ROWS": 2,
            "KNOWN_OOS_DATES": 2,
            "KNOWN_ACTION_SHADOW_ROWS": 2,
            "KNOWN_NESTED_OOS_SIGNALS": 1,
            "KNOWN_NESTED_OOS_SIGNAL_DATES": 1,
            "KNOWN_NESTED_OOS_FILLED_TRADES": 1,
            "KNOWN_NESTED_OOS_MARKET_BUYABLE_FILLED_TRADES": 0,
            "KNOWN_NESTED_OOS_TRADE_SELECTED": 1,
        }
        expected_frozen_counts = {
            "top10": {
                "rows": 2,
                "signal_dates": 2,
                "observation_selected": 2,
                "shadow_selected": 2,
                "risk_gate_pass": 0,
                "selected": 0,
            },
            "trade_selector_oos": {
                "rows": 2,
                "signal_dates": 2,
                "trade_selected": 1,
                "trade_shadow_selected": 1,
                "shadow_selected": 2,
                "trade_selector_promoted": 2,
                "trade_selector_globally_promoted": 0,
                "trade_selector_policy_ready": 0,
            },
            "nested_oos_research": {
                "signals": 1,
                "signal_dates": 1,
                "filled_trades": 1,
                "market_buyable_filled_trades": 0,
            },
            "production": {
                "promoted": False,
                "trade_selector_promoted": False,
                "signals": 0,
                "signal_dates": 0,
                "filled_trades": 0,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.multiple(diagnose.freeze_contract, **constants),
                patch.object(
                    diagnose,
                    "EXPECTED_FROZEN_BEHAVIOR_COUNTS",
                    expected_frozen_counts,
                ),
            ):
                result = diagnose._behavior_activation_evidence(
                    Path(directory),
                    top10=top10,
                    oos=oos,
                    backtest=backtest,
                    action=action,
                    expected_action_rows=9,
                )
                self.assertEqual(
                    {
                        key: result["persisted_counts"][key]
                        for key in expected_frozen_counts
                    },
                    expected_frozen_counts,
                )
                self.assertEqual(
                    result["persisted_counts"]["action_watchlist"],
                    {
                        "rows": 9,
                        "shadow_only_rows": 2,
                        "reject_rows": 7,
                        "formal_buy_count": 0,
                    },
                )
                expanded = copy.deepcopy(action)
                expanded["stage_watchlist"].append(
                    {
                        "ts_code": "000010.SZ",
                        "action": "REJECT",
                        "stage_watch_rank": 10,
                        "watch_label": "仅观察",
                        "target_weight": 0.0,
                        "trade_shadow_selected": 0,
                    }
                )
                expanded_result = diagnose._behavior_activation_evidence(
                    Path(directory),
                    top10=top10,
                    oos=oos,
                    backtest=backtest,
                    action=expanded,
                    expected_action_rows=10,
                )
                self.assertEqual(
                    expanded_result["persisted_counts"]["action_watchlist"],
                    {
                        "rows": 10,
                        "shadow_only_rows": 2,
                        "reject_rows": 8,
                        "formal_buy_count": 0,
                    },
                )
                single = copy.deepcopy(action)
                single["stage_watchlist"] = single["stage_watchlist"][:1]
                single_result = diagnose._behavior_activation_evidence(
                    Path(directory),
                    top10=top10,
                    oos=oos,
                    backtest=backtest,
                    action=single,
                    expected_action_rows=1,
                )
                self.assertEqual(
                    single_result["persisted_counts"]["action_watchlist"],
                    {
                        "rows": 1,
                        "shadow_only_rows": 1,
                        "reject_rows": 0,
                        "formal_buy_count": 0,
                    },
                )
                with self.assertRaisesRegex(
                    RuntimeError,
                    "rows differ from prediction observation domain",
                ):
                    diagnose._behavior_activation_evidence(
                        Path(directory),
                        top10=top10,
                        oos=oos,
                        backtest=backtest,
                        action=expanded,
                        expected_action_rows=9,
                    )
                third_shadow = copy.deepcopy(expanded)
                third_shadow["stage_watchlist"][2].update(
                    {
                        "action": "SHADOW_ONLY",
                        "watch_label": "二筛影子",
                        "trade_shadow_selected": 1,
                    }
                )
                with self.assertRaisesRegex(RuntimeError, "relative-best shadows"):
                    diagnose._behavior_activation_evidence(
                        Path(directory),
                        top10=top10,
                        oos=oos,
                        backtest=backtest,
                        action=third_shadow,
                        expected_action_rows=10,
                    )
                changed = copy.deepcopy(backtest)
                changed["trade_selector"]["formal_policy_oos"]["all_candidates"][
                    "signals"
                ] = 2
                with self.assertRaisesRegex(RuntimeError, "nested OOS signals"):
                    diagnose._behavior_activation_evidence(
                        Path(directory),
                        top10=top10,
                        oos=oos,
                        backtest=changed,
                        action=action,
                        expected_action_rows=9,
                    )

    def test_latest_prediction_final_policy_all_nine_fields_are_direct(self):
        case = FreezeV2Fixture(methodName="runTest")
        case.setUp()
        try:
            action_path = case.root / "outputs/decision/action_plan_latest.json"
            action = json.loads(action_path.read_text())
            action["model"]["v2_integrity_match"] = True
            action["model"]["v2_eligibility_match"] = False
            _write_json(action_path, action)
            with patch.object(diagnose.freeze_contract, "KNOWN_ACTION_SHADOW_ROWS", 1):
                diagnose._runtime_surface_evidence(case.root)
                path = case.root / "outputs/auction_v3/predictions/pred_latest.csv"
                original = pd.read_csv(
                    path,
                    dtype={"signal_date": "string", "ts_code": "string"},
                )
                for column in (
                    "selection_policy_version",
                    "gate_policy_ready",
                    "policy_max_positions",
                    "policy_max_big_loss_probability",
                    "policy_min_mean_return_lcb",
                    "policy_min_fill_probability",
                    "policy_min_exit_probability",
                    "policy_min_conservative_ev",
                    "policy_min_selection_score",
                ):
                    changed = original.copy()
                    if column == "selection_policy_version":
                        changed[column] = "drifted-final-policy"
                    else:
                        changed[column] = pd.to_numeric(changed[column]) + 1
                    changed.to_csv(path, index=False)
                    with self.subTest(column=column):
                        with self.assertRaises(DecisionModelFreezeError):
                            diagnose._runtime_surface_evidence(case.root)
                    original.to_csv(path, index=False)
        finally:
            case.tearDown()

    def test_history_loader_is_schema_aware_and_active_v2_is_valid(self):
        common = {
            **diagnose.EXPECTED_HISTORY_EVIDENCE,
            "path": freeze_module.KNOWN_HISTORY_PATH,
            "sha256": freeze_module.KNOWN_HISTORY_SHA256,
            "rows": freeze_module.KNOWN_HISTORY_ROWS,
            "bootstrap_mode": False,
            "forced_frozen_replay": True,
            "manifest_mutated_on_disk": False,
            "live_history_fallback": False,
        }
        legacy = {
            **common,
            "active": False,
            "manifest_active": False,
            "manifest_active_on_disk": False,
            "manifest_schema_version": "decision_model_freeze_v1",
            "manifest_content_sha256": diagnose.LEGACY_DIAGNOSTIC_MANIFEST_SHA256,
            "source": "legacy_v1_exact_diagnostic_bootstrap",
            "loader_contract": "one_time_exact_v1_no_live_fallback",
        }
        self.assertFalse(
            diagnose._history_activation_evidence({"history": legacy})[
                "manifest_active_on_disk"
            ]
        )
        active_v2 = {
            **common,
            "active": True,
            "manifest_active": True,
            "manifest_active_on_disk": True,
            "manifest_schema_version": "decision_model_freeze_v2",
            "manifest_content_sha256": "b" * 64,
            "source": "forced_frozen_snapshot",
            "loader_contract": "v2_complete_contract_and_pins_no_live_fallback",
            "pinned_files": {
                "active": True,
                "validated": True,
                "enforced": True,
                "forced_enforcement": False,
                "pinned_files": len(freeze_module.REQUIRED_ACTIVE_PIN_PATHS),
            },
        }
        self.assertTrue(
            diagnose._history_activation_evidence({"history": active_v2})[
                "manifest_active_on_disk"
            ]
        )
        report = {
            "status": "fail",
            "diagnostic_mode": "workspace_only_forced_frozen_canonical_v2",
            "force_prediction": True,
            "history": active_v2,
            "golden": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            _write_json(report_path, report)
            summary, passed = diagnose._canonical_replay_report_probe(report_path)
        self.assertFalse(passed)
        self.assertTrue(summary["checks"]["manifest_activity_schema_valid"])
        self.assertTrue(summary["checks"]["frozen_snapshot_source"])
        invalid = copy.deepcopy(active_v2)
        invalid["pinned_files"]["forced_enforcement"] = True
        with self.assertRaisesRegex(RuntimeError, "pinned-file enforcement"):
            diagnose._history_activation_evidence({"history": invalid})
        for field, value in diagnose.EXPECTED_HISTORY_EVIDENCE.items():
            drifted = copy.deepcopy(active_v2)
            if isinstance(value, int):
                drifted[field] = value + 1
            else:
                drifted[field] = "0" * len(value)
            with self.subTest(field=field):
                with self.assertRaisesRegex(RuntimeError, f"history {field} drifted"):
                    diagnose._history_activation_evidence({"history": drifted})

    def test_precision_and_ci_evidence_reject_aliases_and_malformed_hashes(self):
        surface = {
            precision: {
                "gate": "hard" if precision == "8" else "audit_only",
                "equal": True,
                "reference_sha256": "a" * 64,
                "candidate_sha256": "b" * 64,
            }
            for precision in ("6", "8", "10", "12")
        }
        report = {
            "golden": {
                "canonical_scores": {
                    "top10": copy.deepcopy(surface),
                    "selector_oos": copy.deepcopy(surface),
                }
            }
        }
        self.assertEqual(
            set(diagnose._canonical_precision_evidence(report)),
            {"6", "8", "10", "12"},
        )
        for field, value in (
            ("equal", "true"),
            ("reference_sha256", "a" * 63),
            ("candidate_sha256", {"sha256": "b" * 64}),
        ):
            changed = copy.deepcopy(report)
            changed["golden"]["canonical_scores"]["top10"]["6"][field] = value
            with self.subTest(field=field):
                with self.assertRaises(RuntimeError):
                    diagnose._canonical_precision_evidence(changed)

        source = {"candidate_commit": "c" * 40}
        valid_env = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_SHA": "c" * 40,
            "GITHUB_RUN_ID": "31961734392",
            "GITHUB_RUN_ATTEMPT": "1",
            "RUNNER_OS": "Linux",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REPOSITORY": "njedu2023-prog/DC20",
            "GITHUB_REF": "refs/heads/main",
        }
        with patch.dict(os.environ, valid_env, clear=True):
            self.assertEqual(
                diagnose._ci_activation_evidence(source)["github_run_id"],
                valid_env["GITHUB_RUN_ID"],
            )
        for key, bad in (
            ("GITHUB_ACTIONS", "false"),
            ("GITHUB_RUN_ID", "٠١٢"),
            ("GITHUB_RUN_ID", "０１２"),
            ("GITHUB_RUN_ID", "0"),
            ("GITHUB_RUN_ID", "01"),
            ("GITHUB_RUN_ATTEMPT", "2"),
            ("RUNNER_OS", "macOS"),
            ("GITHUB_EVENT_NAME", "push"),
            ("GITHUB_REF", "refs/heads/other"),
        ):
            environment = {**valid_env, key: bad}
            with self.subTest(key=key, bad=bad):
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaises(RuntimeError):
                        diagnose._ci_activation_evidence(source)

    def test_public_evidence_is_single_line_and_rejects_sensitive_values(self):
        evidence = _public_evidence_shape_fixture()
        safe = diagnose._render_compact_activation_evidence(evidence)
        self.assertNotIn("/tmp/", safe)
        public = diagnose._public_activation_evidence_line(safe)
        self.assertEqual(public, safe)
        self.assertNotIn("\n", public)
        legacy = copy.deepcopy(evidence)
        legacy_domain = legacy["canonical_v2"]["surface_consistency"][
            "prediction_trade_selector_domain"
        ]
        legacy_fill = legacy["canonical_v2"]["surface_consistency"][
            "prediction_fill_relationships"
        ]
        legacy_domain.update(
            {"observation_domain_rows": 9, "outside_domain_rows": 42}
        )
        legacy_fill.update(
            {
                "rows": 51,
                "trade_fill_observation_domain_rows": 9,
                "trade_fill_outside_domain_rows": 42,
                "actual_fill_available_rows": 0,
                "actual_fill_missing_rows": 51,
            }
        )
        legacy["behavior_contract"]["action_watchlist"]["rows"] = 9
        legacy["behavior_contract"]["persisted_counts"]["action_watchlist"].update(
            {"rows": 9, "reject_rows": 7}
        )
        legacy_rendered = diagnose._render_compact_activation_evidence(legacy)
        self.assertEqual(
            diagnose._public_activation_evidence_line(legacy_rendered),
            legacy_rendered,
        )
        relationship_mutations = []
        outside_partition = copy.deepcopy(evidence)
        outside_partition["canonical_v2"]["surface_consistency"][
            "prediction_trade_selector_domain"
        ]["outside_domain_rows"] += 1
        relationship_mutations.append(outside_partition)
        fill_total = copy.deepcopy(evidence)
        fill_total["canonical_v2"]["surface_consistency"][
            "prediction_fill_relationships"
        ]["rows"] += 1
        relationship_mutations.append(fill_total)
        fill_observation = copy.deepcopy(evidence)
        fill_observation["canonical_v2"]["surface_consistency"][
            "prediction_fill_relationships"
        ]["trade_fill_observation_domain_rows"] += 1
        relationship_mutations.append(fill_observation)
        fill_availability = copy.deepcopy(evidence)
        fill_availability["canonical_v2"]["surface_consistency"][
            "prediction_fill_relationships"
        ]["actual_fill_available_rows"] += 1
        relationship_mutations.append(fill_availability)
        action_rows = copy.deepcopy(evidence)
        action_rows["behavior_contract"]["action_watchlist"]["rows"] += 1
        relationship_mutations.append(action_rows)
        action_shadow = copy.deepcopy(evidence)
        action_shadow["behavior_contract"]["action_watchlist"][
            "shadow_only_rows"
        ] += 1
        relationship_mutations.append(action_shadow)
        persisted_reject = copy.deepcopy(evidence)
        persisted_reject["behavior_contract"]["persisted_counts"][
            "action_watchlist"
        ]["reject_rows"] += 1
        relationship_mutations.append(persisted_reject)
        for index, changed in enumerate(relationship_mutations):
            with self.subTest(relationship_mutation=index):
                with self.assertRaises(RuntimeError):
                    diagnose._public_activation_evidence_line(
                        diagnose._render_compact_activation_evidence(changed)
                    )
        for active in (False, True):
            v2_evidence = copy.deepcopy(evidence)
            v2_evidence["history_snapshot"].update(
                {
                    "manifest_schema_version": "decision_model_freeze_v2",
                    "manifest_active_on_disk": active,
                    "manifest_content_sha256": "e" * 64,
                    "source": "forced_frozen_snapshot",
                    "loader_contract": (
                        "v2_complete_contract_and_pins_no_live_fallback"
                    ),
                    "pinned_files": {
                        "count": len(freeze_module.REQUIRED_ACTIVE_PIN_PATHS),
                        "validated": True,
                        "enforced": True,
                        "forced_enforcement": not active,
                    },
                }
            )
            with self.subTest(v2_active=active):
                rendered_v2 = diagnose._render_compact_activation_evidence(
                    v2_evidence
                )
                self.assertEqual(
                    diagnose._public_activation_evidence_line(rendered_v2),
                    rendered_v2,
                )
        with self.assertRaisesRegex(RuntimeError, "one line"):
            diagnose._public_activation_evidence_line(safe + "\n")
        shape_mutations = []
        top_extra = copy.deepcopy(evidence)
        top_extra["unapproved_raw"] = "opaque-unreviewed-value"
        shape_mutations.append(top_extra)
        top_missing = copy.deepcopy(evidence)
        top_missing.pop("canonical_precision")
        shape_mutations.append(top_missing)
        nested_extra = copy.deepcopy(evidence)
        nested_extra["behavior_contract"]["top10"]["unapproved_raw"] = "opaque"
        shape_mutations.append(nested_extra)
        nested_missing = copy.deepcopy(evidence)
        nested_missing["canonical_v2"]["surface_consistency"].pop(
            "prediction_fill_relationships"
        )
        shape_mutations.append(nested_missing)
        source_extra = copy.deepcopy(evidence)
        source_extra["candidate_source"]["file_sha256"]["raw/private.csv"] = "d" * 64
        shape_mutations.append(source_extra)
        for index, changed in enumerate(shape_mutations):
            with self.subTest(shape_mutation=index):
                with self.assertRaisesRegex(RuntimeError, "allowlist|path list"):
                    diagnose._public_activation_evidence_line(
                        diagnose._render_compact_activation_evidence(changed)
                    )
        contract_mutations = []
        top10_extra_column = copy.deepcopy(evidence)
        top10_extra_column["behavior_contract"]["top10"][
            "discrete_columns"
        ].append("unreviewed_field")
        contract_mutations.append(top10_extra_column)
        top10_missing_version = copy.deepcopy(evidence)
        top10_missing_version["behavior_contract"]["top10"][
            "discrete_columns"
        ].remove("selection_policy_version")
        contract_mutations.append(top10_missing_version)
        oos_missing_tail = copy.deepcopy(evidence)
        oos_missing_tail["behavior_contract"]["trade_selector_oos"][
            "score_columns"
        ].remove("trade_tail_risk_weight")
        contract_mutations.append(oos_missing_tail)
        action_extra_column = copy.deepcopy(evidence)
        action_extra_column["behavior_contract"]["action_watchlist"][
            "columns"
        ].append("trade_rank")
        contract_mutations.append(action_extra_column)
        for index, changed in enumerate(contract_mutations):
            with self.subTest(contract_mutation=index):
                with self.assertRaisesRegex(RuntimeError, "contract"):
                    diagnose._public_activation_evidence_line(
                        diagnose._render_compact_activation_evidence(changed)
                    )
        envelope_mutations = []
        model_missing = copy.deepcopy(evidence)
        model_missing["canonical_v2"]["model"]["fingerprint_v2"].pop(
            "semantic_sha256"
        )
        envelope_mutations.append(model_missing)
        model_extra = copy.deepcopy(evidence)
        model_extra["canonical_v2"]["model"]["fingerprint_v2"]["unapproved"] = True
        envelope_mutations.append(model_extra)
        selector_missing = copy.deepcopy(evidence)
        selector_missing["canonical_v2"]["trade_selector"]["fingerprint_v2"].pop(
            "policy_sha256"
        )
        envelope_mutations.append(selector_missing)
        selector_extra = copy.deepcopy(evidence)
        selector_extra["canonical_v2"]["trade_selector"]["fingerprint_v2"][
            "unapproved"
        ] = True
        envelope_mutations.append(selector_extra)
        model_policy_tamper = copy.deepcopy(evidence)
        model_policy_tamper["canonical_v2"]["model"]["fingerprint_v2"][
            "policy_projection"
        ]["thresholds"]["min_selection_score"] += 2e-8
        envelope_mutations.append(model_policy_tamper)
        selector_policy_tamper = copy.deepcopy(evidence)
        selector_policy_tamper["canonical_v2"]["trade_selector"]["fingerprint_v2"][
            "policy_projection"
        ]["tail_risk_weight"] += 2e-8
        envelope_mutations.append(selector_policy_tamper)
        for index, changed in enumerate(envelope_mutations):
            with self.subTest(envelope_mutation=index):
                with self.assertRaises(DecisionModelFreezeError):
                    diagnose._public_activation_evidence_line(
                        diagnose._render_compact_activation_evidence(changed)
                    )
        scalar_paths = (
            ("candidate_source", "sha256"),
            ("reference_evidence", "top10_blob_sha1"),
            ("behavior_contract", "top10", "identity_sha256"),
            ("behavior_contract", "action_watchlist", "sha256"),
            ("behavior_contract", "persisted_counts", "top10", "rows"),
            ("canonical_precision", "8", "top10_candidate_sha256"),
            ("ci", "github_run_id"),
            ("history_snapshot", "rows"),
            (
                "canonical_v2",
                "surface_consistency",
                "model_meta_backtest_exact",
            ),
        )
        for path in scalar_paths:
            for replacement in ({"value": "opaque"}, ["opaque"]):
                changed = copy.deepcopy(evidence)
                target = changed
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = replacement
                with self.subTest(scalar_path=path, replacement=type(replacement)):
                    with self.assertRaises((RuntimeError, DecisionModelFreezeError)):
                        diagnose._public_activation_evidence_line(
                            diagnose._render_compact_activation_evidence(changed)
                        )
        nested_list_mutations = []
        source_path_object = copy.deepcopy(evidence)
        source_path_object["candidate_source"]["paths"][0] = {
            "path": diagnose.ACTIVATION_SOURCE_PATHS[0]
        }
        nested_list_mutations.append(source_path_object)
        identity_object = copy.deepcopy(evidence)
        identity_object["behavior_contract"]["top10"]["identity_columns"][0] = {
            "column": "signal_date"
        }
        nested_list_mutations.append(identity_object)
        reason_object = copy.deepcopy(evidence)
        reason_object["behavior_contract"]["decision"]["reason_values"][0] = {
            "reason": "selection_policy_not_ready"
        }
        nested_list_mutations.append(reason_object)
        for index, changed in enumerate(nested_list_mutations):
            with self.subTest(nested_list_mutation=index):
                with self.assertRaises((RuntimeError, DecisionModelFreezeError)):
                    diagnose._public_activation_evidence_line(
                        diagnose._render_compact_activation_evidence(changed)
                    )
        numeric_aliases = (
            (
                (
                    "canonical_v2",
                    "surface_consistency",
                    "prediction_model",
                    "raw_execution_preserved",
                ),
                1,
            ),
            (
                (
                    "canonical_v2",
                    "surface_consistency",
                    "prediction_trade_selector",
                    "canonical_decimals",
                ),
                8.0,
            ),
            (("behavior_contract", "top10", "rows"), 4467.0),
            (("behavior_contract", "top10", "score_decimals"), True),
            (("behavior_contract", "action_watchlist", "rows"), 10.0),
            (("behavior_contract", "nested_oos_research", "signals"), 158.0),
            (("behavior_contract", "decision", "formal_buy_count"), False),
            (
                ("behavior_contract", "persisted_counts", "top10", "selected"),
                False,
            ),
        )
        for path, replacement in numeric_aliases:
            changed = copy.deepcopy(evidence)
            target = changed
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = replacement
            with self.subTest(numeric_alias=path):
                with self.assertRaises(RuntimeError):
                    diagnose._public_activation_evidence_line(
                        diagnose._render_compact_activation_evidence(changed)
                    )
        v2_pin_alias = copy.deepcopy(evidence)
        v2_pin_alias["history_snapshot"].update(
            {
                "manifest_schema_version": "decision_model_freeze_v2",
                "manifest_content_sha256": "e" * 64,
                "source": "forced_frozen_snapshot",
                "loader_contract": "v2_complete_contract_and_pins_no_live_fallback",
                "pinned_files": {
                    "count": False,
                    "validated": True,
                    "enforced": True,
                    "forced_enforcement": True,
                },
            }
        )
        with self.assertRaises(RuntimeError):
            diagnose._public_activation_evidence_line(
                diagnose._render_compact_activation_evidence(v2_pin_alias)
            )
        for equal_value, candidate_hash in ((False, "b" * 64), (True, "d" * 64)):
            changed = copy.deepcopy(evidence)
            changed["canonical_precision"]["6"]["top10_equal"] = equal_value
            changed["canonical_precision"]["6"][
                "top10_candidate_sha256"
            ] = candidate_hash
            with self.subTest(equal_value=equal_value, candidate_hash=candidate_hash[0]):
                with self.assertRaisesRegex(RuntimeError, "equality/hash"):
                    diagnose._public_activation_evidence_line(
                        diagnose._render_compact_activation_evidence(changed)
                    )
        for embedded_path in (
            "forced /home/runner/private",
            "prefix /private/tmp/private.json",
            "prefix=/tmp/private.json",
            "note: /Users/runner/private.json",
            "note C:\\Users\\runner\\private.json",
            r"note \\server\share\private.json",
            r"note \Users\runner\private.json",
            "note //server/share/private.json",
            "note file:///etc/passwd",
            "candidate 000001.SZ",
            "note /etc/passwd",
            "note:/usr/local/private",
            "note=/srv/private",
            "note,/data/private",
            "note(/run/private)",
        ):
            changed = copy.deepcopy(evidence)
            changed["history_snapshot"]["source"] = embedded_path
            with self.subTest(embedded_path=embedded_path):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "absolute POSIX|absolute Windows|rooted or escaped Windows|double-root|stock-level",
                ):
                    diagnose._public_activation_evidence_line(
                        diagnose._render_compact_activation_evidence(changed)
                    )
        for unsafe in (
            {"token": "not-exportable"},
            {"value": "Bearer not-exportable"},
            {"value": "/home/runner/work/private.json"},
            {"value": "/private/tmp/private.json"},
            {"value": r"C:\\Users\\runner\\private.json"},
        ):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(RuntimeError):
                    diagnose._render_compact_activation_evidence(unsafe)
        stdout = diagnose._safe_probe_stdout(
            {
                "passed": True,
                "checks": {"q8_hard": True},
                "candidate_source": {"secret_sha256": "f" * 64},
                "activation_evidence": {"path": "/private/tmp/evidence.json"},
            },
            evidence_written=True,
        )
        rendered = json.dumps(stdout, sort_keys=True)
        self.assertNotIn("sha256", rendered)
        self.assertNotIn("/private/", rendered)
        self.assertNotIn("activation_evidence", rendered)
        self.assertTrue(stdout["passed"])

    def test_main_failure_paths_emit_one_safe_line_without_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            malformed = root / "malformed-report.json"
            malformed.write_text(
                json.dumps({"golden": {"top10": [1]}}),
                encoding="utf-8",
            )
            cases = (
                {},
                {
                    "FINGERPRINT_REPLAY_REPORT": str(malformed),
                    "FINGERPRINT_EVIDENCE_OUTPUT": str(root / "evidence.json"),
                },
            )
            for environment in cases:
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    self.subTest(environment=bool(environment)),
                    patch.object(
                        diagnose,
                        "_parse_args",
                        return_value=SimpleNamespace(
                            self_test_evidence_contract=False
                        ),
                    ),
                    patch.dict(os.environ, environment, clear=True),
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                ):
                    self.assertEqual(diagnose.main(), 1)
                lines = stdout.getvalue().splitlines()
                self.assertEqual(len(lines), 1)
                payload = json.loads(lines[0])
                self.assertFalse(payload["passed"])
                self.assertEqual(stderr.getvalue(), "")
                rendered = lines[0].lower()
                self.assertNotIn("/private/", rendered)
                self.assertNotIn("/tmp/", rendered)
                self.assertNotIn("token", rendered)
                self.assertNotIn("traceback", rendered)


if __name__ == "__main__":
    unittest.main()
