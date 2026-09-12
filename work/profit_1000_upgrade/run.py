#!/usr/bin/env python3
"""Reproducible research preparation and gated evaluation; no production writes."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(HERE)]

ARCHIVES = (
    {"zip_sha256": "e56fcaf1ee54bd8ff562d1eae832a2d46397de256588c4aa097556533e0410c7",
     "manifest_sha256": "1be7507a8fdea6764c2ce5e9e0e617f38792a5eb41971b5d3d10e1e94c7e6953",
     "run_id": 34023469106, "original_status": "BLOCKED_COLLECTION"},
    {"zip_sha256": "5ab1226154915a86cb56ec668abf8da80dc288173a2abfac8083c4d56402a2e9",
     "manifest_sha256": "aab422db860841f5c09e6d278cb2637934c13400b7da336d5ce21241c8e16b95",
     "run_id": 34028882328, "original_status": "SUPPLEMENTAL_RUN_NOT_REPLACEMENT"},
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, obj):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)) or path.exists():
        raise ValueError("research outputs are immutable; use a new run directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def safe_input(root, spec):
    relative = PurePosixPath(spec["path"])
    path = Path(root) / relative
    if (relative.is_absolute() or ".." in relative.parts
            or any(p.is_symlink() for p in (path, *path.parents) if p != root)
            or not path.is_file() or sha(path) != spec["sha256"]):
        raise ValueError("immutable input binding changed: " + str(relative))
    return path


def load_plan():
    plan = json.loads((HERE / "PLAN.json").read_text())
    if (plan["label_policy_id"] != "dc20_exit_1000_limit_hold_20260912_v1"
            or plan["production_release_allowed"] is not False
            or plan["future_holdout_start_date"] != "20260914"
            or plan["cost_rate"] != .0045 or plan["stress_cost_rate"] != .009
            or plan["negative_score_skip_allowed"] is not False):
        raise ValueError("research plan policy changed")
    return plan


def initialize_mirror(output):
    output = Path(output)
    if output.is_symlink() or any(p.is_symlink() for p in output.parents):
        raise ValueError("symlink output forbidden")
    output = output.resolve()
    if output == ROOT or ROOT in output.parents:
        raise ValueError("research mirror must be outside checkout")
    output.mkdir(parents=True, exist_ok=False)
    plan = load_plan()
    for spec in plan["source_inputs"].values():
        source = safe_input(ROOT, spec)
        dest = output / spec["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    write_json(output / ".dc20-profit-1000-research-root.json", {
        "schema_version": "dc20_profit_1000_research_mirror_v1", "production_writes": False,
        "plan_sha256": sha(HERE / "PLAN.json")})
    return output


def require_research_mirror(root):
    path = Path(root)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("aliased research mirror")
    path = path.resolve(strict=True)
    if path == ROOT or path in ROOT.parents or ROOT in path.parents:
        raise ValueError("research mirror must be outside checkout")
    marker = path / ".dc20-profit-1000-research-root.json"
    if marker.is_symlink() or not marker.is_file():
        raise ValueError("research mirror marker missing")
    metadata = json.loads(marker.read_text())
    if (metadata.get("schema_version") != "dc20_profit_1000_research_mirror_v1"
            or metadata.get("production_writes") is not False
            or metadata.get("plan_sha256") != sha(HERE / "PLAN.json")):
        raise ValueError("research mirror plan binding changed")
    return path


def import_archive(root, archive, spec):
    """Verify the whole retained archive; import only bound candidate raw truth."""
    root = require_research_mirror(root)
    archive = Path(archive)
    if sha(archive) != spec["zip_sha256"]:
        raise ValueError("historical ZIP SHA mismatch")
    copied = []
    with zipfile.ZipFile(archive) as bundle:
        infos = bundle.infolist()
        names = [item.filename for item in infos]
        if len(names) != len(set(names)):
            raise ValueError("duplicate archive member")
        for info in infos:
            name = PurePosixPath(info.filename)
            if name.is_absolute() or ".." in name.parts or "\\" in info.filename or ((info.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError("unsafe archive member")
        manifest_names = [n for n in names if n.endswith("artifact_manifest.json")]
        if len(manifest_names) != 1:
            raise ValueError("archive manifest ambiguous")
        manifest_raw = bundle.read(manifest_names[0])
        if hashlib.sha256(manifest_raw).hexdigest() != spec["manifest_sha256"]:
            raise ValueError("archive manifest SHA mismatch")
        manifest = json.loads(manifest_raw)
        prefix = manifest_names[0][:-len("artifact_manifest.json")]
        files = manifest["files"]
        if set(names) != {prefix + key for key in files} | {manifest_names[0]}:
            raise ValueError("unbound archive members")
        for relative, binding in files.items():
            raw = bundle.read(prefix + relative)
            if len(raw) != binding["bytes"] or hashlib.sha256(raw).hexdigest() != binding["sha256"]:
                raise ValueError("historical archive member SHA mismatch")
            match = re.fullmatch(r"candidate_sources/(20\d{6})/(daily|stk_limit)\.csv", relative)
            if not match:
                continue
            date, kind = match.groups()
            dest = Path(root) / "data/market/raw" / date[:4] / date / (kind + ".csv")
            if any(p.is_symlink() for p in (dest, *dest.parents)):
                raise ValueError("aliased historical destination")
            if dest.exists():
                if dest.read_bytes() != raw:
                    raise ValueError("conflicting historical source partitions")
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(raw)
            copied.append({"path": str(dest.relative_to(root)), "sha256": binding["sha256"]})
    return dict(spec, imported_partitions=len(copied), sources=copied)


def prepare_history(root):
    """Whitelist D-only features; never copy legacy outcome columns into labels."""
    root = Path(root).resolve(strict=True)
    plan = load_plan()
    source = {key: safe_input(root, spec) for key, spec in plan["source_inputs"].items()}
    fm = json.loads(source["manifest"].read_text())
    columns = fm["feature_contract"]["columns"]
    if fm["feature_contract"]["known_at"] != "D close":
        raise ValueError("feature temporal contract changed")
    with gzip.open(source["ledger"], "rt", encoding="utf-8-sig", newline="") as handle:
        original = list(csv.DictReader(handle))
    dates = sorted({r["signal_date"] for r in original})
    if (len(original) != plan["historical_rows"] or len(dates) != plan["historical_D_dates"]
            or dates[0] != plan["historical_D_start"] or dates[-1] != plan["historical_D_end"]):
        raise ValueError("historical candidate scope changed")
    rows, model_rows, codes = [], [], {}
    for row in original:
        d = row["signal_date"]
        if not row["promotion_oof_train_end"] < d:
            raise ValueError("promotion OOF model trained after D")
        features = {}
        for key in columns:
            number = None if row[key] == "" else float(row[key])
            if number is not None and not math.isfinite(number):
                raise ValueError("nonfinite feature")
            features[key] = number
        normalized = {key: row[key] for key in ("signal_date", "ts_code", "stage_transition", "exec_date", "scheduled_exit_date")}
        normalized.update(promotion_rank=int(row["promotion_rank"]), features=features,
                          feature_as_of_date=d,
                          feature_available_at=f"{d[:4]}-{d[4:6]}-{d[6:]}T23:59:00+08:00")
        rows.append(normalized)
        codes.setdefault(d, []).append(row["ts_code"])
        model_rows.append({**features, "signal_date": d, "ts_code": row["ts_code"],
                           "promotion_rank": int(row["promotion_rank"]), "board_stage": int(row["stage"]),
                           "feature_as_of_date": d, "promotion_oof_train_end": row["promotion_oof_train_end"]})
    manifest = {"schema_version": "dc20_profit_1000_research_candidates_v1",
                "evidence_kind": "RETROSPECTIVE_D_ONLY_RECONSTRUCTION",
                "feature_availability_is_natural_freeze_evidence": False,
                "feature_columns": columns, "rows": rows, "expected_candidate_codes": codes,
                "source_bindings": list(plan["source_inputs"].values())}
    model_manifest = {"source_sha256": plan["source_inputs"]["ledger"]["sha256"],
                      "source_provenance_verified": True,
                      "feature_timestamp_semantics": "RETROSPECTIVE_D_ONLY_BOUND",
                      "promotion_prediction_provenance": "HISTORICAL_OOF",
                      "day_candidate_counts": {d: len(c) for d, c in codes.items()},
                      "natural_freeze_verified": False,
                      "entry_policy_id": "research_auction_or_open_no_cap_v1",
                      "cost_rate": plan["cost_rate"],
                      "entry_policy_matches_production_shadow": False,
                      "causal_feature_audit_scope": "Pinned existing D-close feature contract; not new independent proof for every raw feature",
                      "missing_historical_signals": ["promotion_probability", "named_limit_path", "existing_profit_rank"]}
    return manifest, model_rows, model_manifest


def evaluate(root):
    from labels import build_labels
    from candidate import run_candidate
    root = require_research_mirror(root)
    plan = load_plan()
    manifest, frozen, evidence = prepare_history(root)
    labels = build_labels(root, manifest, as_of_date=plan["as_of_date"])
    try:
        receipt_path = Path(root) / "collection_receipt.json"
        if any(p.is_symlink() for p in (receipt_path, *receipt_path.parents)):
            raise ValueError("collection receipt path is aliased")
        receipt = json.loads(receipt_path.read_text())
        if (receipt.get("schema_version") != "dc20_profit_1000_collection_receipt_v1"
                or receipt.get("as_of_date") != plan["as_of_date"]
                or receipt.get("auction_attempts_complete") is not True
                or receipt.get("production_writes") is not False
                or receipt.get("existing_truth_overwritten") is not False
                or receipt.get("candidate_source_bindings") != manifest["source_bindings"]):
            raise ValueError("collection source/auction-attempt contract incomplete")
        for binding in receipt["new_source_files"]:
            safe_input(root, binding)
        considered = {r["trade_date"] for r in receipt["request_receipts"]
                      if r["endpoint"] == "stk_auction_o" and
                      (r.get("network_request_performed") is True or
                       r["status"] in {"EXACT_TRUTH_WRITTEN", "EXISTING_TRUTH_NOT_OVERWRITTEN"})}
        if considered != {r["exec_date"] for r in manifest["rows"]}:
            raise ValueError("not every T auction source was attempted")
        result = run_candidate(frozen, labels["rows"], as_of_date=plan["as_of_date"],
                               training_cutoff_date=plan["training_cutoff_date"],
                               validation_end_date=plan["validation_end_date"],
                               holdout_start_date=plan["future_holdout_start_date"],
                               frozen_manifest=evidence, gates=plan["data_gates"])
        result["collection_receipt_sha256"] = sha(receipt_path)
    except (OSError, KeyError, ValueError) as exc:
        result = {"status": "BLOCKED_COLLECTION_EVIDENCE", "candidate_model": None,
                  "predictions": [], "report": {"training_performed": False,
                  "production_activation_allowed": False, "reason": str(exc),
                  "profitability_improvement_proven": False}}
    result["plan_sha256"] = sha(HERE / "PLAN.json")
    result["execution_provenance"] = {
        "run_commit": os.environ.get("GITHUB_SHA"), "run_id": os.environ.get("GITHUB_RUN_ID"),
        "source_files": [{"path": str(p.relative_to(ROOT)), "sha256": sha(p)} for p in
                         [HERE / name for name in ("run.py", "labels.py", "candidate.py", "collect.py", "PLAN.json", "COLLECTION.json")]
                         + [ROOT / "src/top10decision/decision/shadow_exit_1000.py",
                            ROOT / "src/top10decision/decision/shadow_exit_minute_truth.py",
                            ROOT / "requirements-dev.lock"]]}
    result["label_status_counts"] = dict(Counter(r["label_status"] for r in labels["rows"]))
    result["promotion_model_changed"] = False
    result["production_release_allowed"] = False
    out = Path(root) / "research_results"
    write_json(out / "labels.json", labels)
    write_json(out / "candidate.json", result)
    write_json(out / "feature_evidence.json", evidence)
    print(json.dumps({"status": result["status"], "label_status_counts": result["label_status_counts"],
                      "report": result["report"], "production_release_allowed": False}, ensure_ascii=False))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source-zip", type=Path)
    parser.add_argument("--tail-zip", type=Path)
    parser.add_argument("--evaluate-root", type=Path)
    args = parser.parse_args()
    if args.evaluate_root:
        evaluate(args.evaluate_root)
        return 0
    if not args.output:
        parser.error("--output or --evaluate-root required")
    root = initialize_mirror(args.output)
    imported = []
    for path, spec in zip((args.source_zip, args.tail_zip), ARCHIVES):
        if path:
            imported.append(import_archive(root, path, spec))
    manifest, _, evidence = prepare_history(root)
    write_json(root / "research_inputs/manifest.json", manifest)
    write_json(root / "research_inputs/imports.json", imported)
    write_json(root / "research_inputs/feature_evidence.json", evidence)
    print(json.dumps({"status": "RESEARCH_INPUTS_PREPARED", "rows": len(manifest["rows"]),
                      "dates": len(manifest["expected_candidate_codes"]), "root": str(root),
                      "production_changed": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
