from __future__ import annotations

import argparse
import hashlib
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

import benchmark


SCHEMA = "dc20_executable_profit_internal_forward_challenger_v1"
LABEL_CUTOFF_EXCLUSIVE = "20260824"


def run(repo_root: Path, work_root: Path) -> dict[str, object]:
    frame, variants, provenance = benchmark.load_frame(repo_root.resolve(), work_root.resolve())
    features = variants["full_priors"]
    first = benchmark.fit_two_stage(
        frame,
        feature_columns=features,
        kind="hgb",
        label_available_before=LABEL_CUTOFF_EXCLUSIVE,
    )
    second = benchmark.fit_two_stage(
        frame,
        feature_columns=features,
        kind="hgb",
        label_available_before=LABEL_CUTOFF_EXCLUSIVE,
    )
    first_predictions = first.predict(frame)
    second_predictions = second.predict(frame)
    benchmark._expect(
        all(np.array_equal(left, right) for left, right in zip(first_predictions, second_predictions)),
        "full-history deterministic refit predictions drifted",
    )

    bundle = {
        "schema_version": SCHEMA,
        "status": "INTERNAL_FORWARD_RESEARCH_CHALLENGER_ONLY_NOT_READY",
        "model_kind": "hgb",
        "variant": "full_priors",
        "feature_columns": features,
        "training_audit": first.training_audit,
        "fill_model": first.fill,
        "conditional_profit_model": first.conditional_profit,
    }
    second_bundle = {**bundle, "fill_model": second.fill, "conditional_profit_model": second.conditional_profit}
    payload = pickle.dumps(bundle, protocol=5)
    second_payload = pickle.dumps(second_bundle, protocol=5)
    benchmark._expect(payload == second_payload, "full-history deterministic refit pickle drifted")
    model_path = work_root / "outputs/internal_forward_challenger.pkl"
    benchmark._atomic_bytes(model_path, payload)

    score_frame = frame[["signal_date", "ts_code"]].copy()
    score_frame["p_fill"] = first_predictions[0]
    score_frame["p_profit_given_fill"] = first_predictions[1]
    score_frame["p_executable_profit"] = first_predictions[2]
    score_frame = score_frame.sort_values(["signal_date", "ts_code"], kind="stable")
    score_hash = hashlib.sha256(benchmark._deterministic_gzip(score_frame)).hexdigest()
    audit = {
        "schema_version": SCHEMA,
        "status": "INTERNAL_FORWARD_RESEARCH_CHALLENGER_ONLY_NOT_READY",
        "official_trade_action_allowed": False,
        "front_end_rank_allowed": False,
        "historical_effect_claim_allowed": False,
        "retrospective_confirmation_window_has_been_viewed": True,
        "independent_untouched_confirmation_available": False,
        "forward_release_evidence_available": False,
        "challenger_selection_used_viewed_retrospective_results": True,
        "use_scope": "new forward dates only; collect evidence without changing official ranking",
        "model_kind": "hgb",
        "variant": "full_priors",
        "objective": "P(fill proxy) * P(net profit after cost > 0 | fill proxy)",
        "feature_count": len(features),
        "feature_columns_sha256": provenance["variant_feature_columns_sha256"]["full_priors"],
        "training": first.training_audit,
        "deterministic_refit": {
            "exact_prediction_arrays_equal": True,
            "exact_pickle_bytes_equal": True,
            "training_prediction_snapshot_sha256": score_hash,
        },
        "artifact": {
            "path": str(model_path.relative_to(work_root)),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
            "format": "trusted-local-python-pickle-protocol-5",
        },
        "provenance": provenance,
        "release_block": "Historical development/retrospective gates rejected all lagged-prior variants; no untouched forward evidence exists.",
    }
    audit_payload = (
        json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
    audit_path = work_root / "outputs/internal_forward_challenger_audit.json"
    benchmark._atomic_bytes(audit_path, audit_payload)
    audit["audit_sha256"] = hashlib.sha256(audit_payload).hexdigest()
    return audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    try:
        result = run(args.repo_root, args.work_root.resolve())
    except (benchmark.BenchmarkError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"valid": True, **result}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
