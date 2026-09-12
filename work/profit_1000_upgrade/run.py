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
V1_PLAN_SHA256 = "73e3873ae88b5f44b0f7528b4ee2664ac1fe5a1c683b64e9b7122fe4d86ac221"
BASE_V2_ZIP_SHA256 = "d004f6decba35d6148082333764ba0988bc3fa25062224492d485ac031fe2a29"

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


def plan_path(plan_version="v1"):
    if plan_version not in ("v1", "v2"):
        raise ValueError("unknown research plan version")
    return HERE / ("PLAN.json" if plan_version == "v1" else "PLAN_V2.json")


def load_plan(plan_version="v1"):
    plan = json.loads(plan_path(plan_version).read_text())
    if (plan["label_policy_id"] != "dc20_exit_1000_limit_hold_20260912_v1"
            or plan["production_release_allowed"] is not False
            or plan["future_holdout_start_date"] != "20260914"
            or plan["cost_rate"] != .0045 or plan["stress_cost_rate"] != .009
            or plan["negative_score_skip_allowed"] is not False):
        raise ValueError("research plan policy changed")
    if plan_version == "v2":
        from work.profit_1000_upgrade.policy_v2 import validate_contract
        validate_contract(plan)
        old = json.loads(plan_path("v1").read_text())
        if sha(plan_path("v1")) != V1_PLAN_SHA256 or plan["supersedes_plan_sha256"] != V1_PLAN_SHA256:
            raise ValueError("prior registered plan bytes changed")
        allowed_changes = {"schema_version", "buy_rule", "research_entry_policy_id"}
        if any(plan.get(key) != value for key, value in old.items() if key not in allowed_changes):
            raise ValueError("v2 cannot change original model, gates, dates or accounting")
        if (plan.get("schema_version") != "dc20_profit_1000_upgrade_plan_v2"
                or plan["research_entry_policy_id"] != plan["entry_policy_id"]):
            raise ValueError("v2 plan identity changed")
        if (plan["base_archive"]["zip_sha256"] != BASE_V2_ZIP_SHA256
                or plan["base_archive"]["run_id"] != "34671477608"
                or plan["base_archive"]["run_commit"] != "4675fab984050fa32be875fff07e0285e8903ebb"
                or plan["base_archive"]["daily_partitions"] != 926 or plan["base_archive"]["limit_partitions"] != 926):
            raise ValueError("registered base archive scope changed")
        adapter_paths = {"work/profit_1000_upgrade/auction_truth.py", "work/profit_1000_upgrade/minute_truth.py",
                         "src/top10decision/decision/shadow_exit_1000.py", "src/top10decision/decision/shadow_exit_minute_truth.py"}
        if len(plan["adapter_sources"]) != 4 or {b["path"] for b in plan["adapter_sources"]} != adapter_paths:
            raise ValueError("v2 adapter source bindings incomplete")
        for binding in plan["adapter_sources"]:
            safe_input(ROOT, binding)
    return plan


def initialize_mirror(output, plan_version="v1"):
    output = Path(output)
    if output.is_symlink() or any(p.is_symlink() for p in output.parents):
        raise ValueError("symlink output forbidden")
    output = output.resolve()
    if output == ROOT or ROOT in output.parents:
        raise ValueError("research mirror must be outside checkout")
    output.mkdir(parents=True, exist_ok=False)
    plan = load_plan() if plan_version == "v1" else load_plan(plan_version)
    for spec in plan["source_inputs"].values():
        source = safe_input(ROOT, spec)
        dest = output / spec["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    marker = {"schema_version": "dc20_profit_1000_research_mirror_" + plan_version, "production_writes": False,
              "plan_sha256": sha(plan_path(plan_version))}
    if plan_version == "v2":
        from work.profit_1000_upgrade.policy_v2 import CONTRACT
        marker.update(plan_version="v2", **dict(CONTRACT))
    write_json(output / ".dc20-profit-1000-research-root.json", marker)
    return output


def plan_version_for(root):
    marker = Path(root) / ".dc20-profit-1000-research-root.json"
    if any(p.is_symlink() for p in (marker, *marker.parents)) or not marker.is_file():
        raise ValueError("research mirror marker missing or aliased")
    metadata = json.loads(marker.read_text())
    schema = metadata.get("schema_version")
    if schema == "dc20_profit_1000_research_mirror_v1":
        return "v1"
    if schema == "dc20_profit_1000_research_mirror_v2" and metadata.get("plan_version") == "v2":
        return "v2"
    raise ValueError("unknown research mirror version")


def require_research_mirror(root, plan_version=None):
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
    actual_version = plan_version_for(path)
    if (plan_version is not None and plan_version != actual_version
            or metadata.get("production_writes") is not False
            or metadata.get("plan_sha256") != sha(plan_path(actual_version))):
        raise ValueError("research mirror plan binding changed")
    if actual_version == "v2":
        from work.profit_1000_upgrade.policy_v2 import validate_contract
        validate_contract(metadata)
        load_plan("v2")
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


def prepare_history(root, plan_version=None):
    """Whitelist D-only features; never copy legacy outcome columns into labels."""
    root = Path(root).resolve(strict=True)
    if plan_version is None:
        plan_version = plan_version_for(root) if (root / ".dc20-profit-1000-research-root.json").exists() else "v1"
    plan = load_plan() if plan_version == "v1" else load_plan(plan_version)
    if plan_version == "v2":
        require_research_mirror(root, plan_version="v2")
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
    if plan_version == "v2":
        from work.profit_1000_upgrade.policy_v2 import CONTRACT
        for value in (manifest, model_manifest):
            value.update(dict(CONTRACT), plan_version="v2", plan_sha256=sha(plan_path("v2")))
    return manifest, model_rows, model_manifest


def validate_collection_evidence(root, manifest, plan, *, plan_version=None):
    """Shared label/model/capital gate; attempted is not qualified source truth."""
    root = require_research_mirror(root, plan_version=plan_version)
    version = plan_version_for(root)
    receipt_path = root / "collection_receipt.json"
    if any(p.is_symlink() for p in (receipt_path, *receipt_path.parents)):
        raise ValueError("collection receipt path is aliased")
    receipt = json.loads(receipt_path.read_text())
    if not isinstance(receipt, dict):
        raise ValueError("collection receipt must be an object")
    if (receipt.get("schema_version") != "dc20_profit_1000_collection_receipt_" + version
            or receipt.get("as_of_date") != plan["as_of_date"]
            or receipt.get("auction_attempts_complete") is not True
            or receipt.get("production_writes") is not False
            or receipt.get("existing_truth_overwritten") is not False
            or receipt.get("candidate_source_bindings") != manifest["source_bindings"]):
        raise ValueError("collection source/auction-attempt contract incomplete")
    if (not isinstance(receipt.get("new_source_files"), list) or not isinstance(receipt.get("request_receipts"), list)
            or any(not isinstance(r, dict) for r in receipt["request_receipts"])):
        raise ValueError("invalid collection bindings or request receipts")
    bindings = {"collection_receipt.json": {"path": "collection_receipt.json", "sha256": sha(receipt_path)}}
    def bind(value):
        safe_input(root, value)
        if value["path"] in bindings and bindings[value["path"]] != value:
            raise ValueError("conflicting collection binding")
        bindings[value["path"]] = dict(value)
    def v2_source_path(value):
        path = value.get("path", "")
        patterns = (
            r"data/market/raw/(20\d{2})/(20\d{6})/(daily|stk_limit)(\.csv|\.meta\.json)",
            r"data/research/canonical_auction_0925/(20\d{2})/(20\d{6})/stk_auction\.(data|meta)\.json",
            r"research_inputs/minute_truth_0931/(20\d{2})/(20\d{6})/\d{6}_(SH|SZ)\.(data|meta)\.json",
        )
        match = next((m for pattern in patterns if (m := re.fullmatch(pattern, path))), None)
        if match is None or match.group(1) != match.group(2)[:4]:
            raise ValueError("v2 collection source outside new-source whitelist")
    for value in receipt["new_source_files"]:
        if version == "v2":
            v2_source_path(value)
        bind(value)
    expected = {row["exec_date"] for row in manifest["rows"]}
    declarations = set()
    if version == "v1":
        considered = {r["trade_date"] for r in receipt["request_receipts"]
                      if r["endpoint"] == "stk_auction_o" and
                      (r.get("network_request_performed") is True or
                       r["status"] in {"EXACT_TRUTH_WRITTEN", "EXISTING_TRUTH_NOT_OVERWRITTEN"})}
    else:
        from work.profit_1000_upgrade import auction_truth
        from work.profit_1000_upgrade.policy_v2 import validate_contract
        validate_contract(manifest)
        validate_contract(plan)
        validate_contract(receipt)
        if plan != load_plan("v2"):
            raise ValueError("collection gate requires current registered v2 plan")
        if (receipt.get("plan_sha256") != sha(plan_path("v2"))
                or receipt.get("collection_request_sha256") != sha(HERE / "COLLECTION_V2.json")
                or receipt.get("auction_evidence_complete") is not True
                or receipt.get("credential_persisted") is not False):
            raise ValueError("v2 collection plan/source qualification incomplete")
        base_binding = receipt["base_archive_import_binding"]
        if base_binding["path"] != "research_inputs/base_archive_import.json":
            raise ValueError("wrong base import receipt path")
        bind(base_binding)
        base = json.loads(safe_input(root, base_binding).read_text())
        if (base.get("schema_version") != "dc20_profit_1000_base_archive_import_v2"
                or base.get("base_archive") != plan["base_archive"]
                or base.get("plan_sha256") != sha(plan_path("v2"))
                or base.get("production_writes") is not False
                or base.get("old_auction_and_minute_sources_imported") is not False
                or base.get("old_outcome_labels_imported") is not False):
            raise ValueError("base archive import provenance changed")
        base_sources = base["source_files"]
        if (len(base_sources) != plan["base_archive"]["daily_partitions"] + plan["base_archive"]["limit_partitions"]
                or len({b["path"] for b in base_sources}) != len(base_sources)
                or any(not re.fullmatch(r"data/market/raw/20\d{2}/20\d{6}/(daily|stk_limit)\.csv", b["path"]) for b in base_sources)):
            raise ValueError("base import sources incomplete or outside whitelist")
        for value in base_sources + receipt.get("existing_source_files", []):
            v2_source_path(value)
            bind(value)
        considered = set()
        for item in receipt["request_receipts"]:
            if item.get("endpoint") != "stk_auction":
                continue
            day = item.get("trade_date")
            if day not in expected or day in considered or item.get("ts_code") is not None:
                raise ValueError("duplicate/out-of-scope canonical T receipt")
            considered.add(day)
            if day < auction_truth.COVERAGE_START:
                if (item.get("status") != "HISTORY_BEFORE_CANONICAL_COVERAGE"
                        or item.get("network_request_performed") is not False
                        or item.get("source_policy_id") != auction_truth.SOURCE_POLICY_ID
                        or item.get("plan_sha256") != sha(plan_path("v2"))):
                    raise ValueError("invalid pre-coverage declaration")
                if any(p.exists() for p in auction_truth.source_paths(root, day)):
                    raise ValueError("pre-coverage declaration conflicts with present canonical source")
                declarations.add(day)
                continue
            if item.get("status") not in {"EXACT_TRUTH_WRITTEN", "EXISTING_TRUTH_NOT_OVERWRITTEN"}:
                raise ValueError("canonical attempt is not qualified evidence")
            source = auction_truth.load(root, day)
            if source.requested_code is not None:
                raise ValueError("canonical T receipt must cover full-market query")
            for value in source.source_files:
                if bindings.get(value["path"]) != dict(value):
                    raise ValueError("canonical source not bound by current collection receipt")
            meta = json.loads(auction_truth.source_paths(root, day)[1].read_text())
            if (item.get("http_response_sha256") != meta["http_response_sha256"]
                    or item.get("request") != meta["request"]
                    or item.get("source_status") != meta["status"]
                    or not (item.get("network_request_performed") is True
                            or item["status"] == "EXISTING_TRUTH_NOT_OVERWRITTEN"
                            and item.get("network_request_performed") is False
                            and item.get("source_network_request_performed") is True)):
                raise ValueError("canonical request/body receipt does not bind source")
        from work.profit_1000_upgrade import minute_truth
        minute_keys = set()
        declared_minute_list = [b["path"] for b in receipt["new_source_files"] + receipt.get("existing_source_files", [])
                                if b["path"].startswith(minute_truth.SOURCE_ROOT + "/")]
        if len(declared_minute_list) != len(set(declared_minute_list)):
            raise ValueError("duplicate declared research minute source")
        verified_minute_paths = set()
        for item in receipt["request_receipts"]:
            if item.get("endpoint") != "stk_mins" or item.get("status") not in {"EXACT_TRUTH_WRITTEN", "EXISTING_TRUTH_NOT_OVERWRITTEN"}:
                continue
            day, code = item.get("trade_date"), item.get("ts_code")
            if (day, code) in minute_keys:
                raise ValueError("duplicate qualified minute receipt")
            minute_keys.add((day, code))
            payload = minute_truth.load(root, day, code)
            if payload is None:
                raise ValueError("qualified minute receipt has no source")
            for value in payload["source_files"]:
                if bindings.get(value["path"]) != value:
                    raise ValueError("minute source outside collection bindings")
                verified_minute_paths.add(value["path"])
            meta = json.loads(minute_truth.paths(root, day, code)[1].read_text())
            expected_request = {"api_name": "stk_mins", "params": minute_truth.request_parameters(day, code), "fields": list(minute_truth.FIELDS)}
            if (item.get("request") != expected_request
                    or item.get("http_response_sha256") != meta["response_body_sha256"]
                    or item.get("status") == "EXACT_TRUTH_WRITTEN" and item.get("network_request_performed") is not True):
                raise ValueError("minute request/body receipt does not bind source")
        if verified_minute_paths != set(declared_minute_list):
            raise ValueError("research minute sources lack exact qualified request receipt coverage")
    if considered != expected:
        raise ValueError("not every T auction source was attempted and qualified")
    summary = {"sha256": sha(receipt_path), "verified_auction_dates": len(considered - declarations),
               "verified_coverage_declarations": len(declarations), "new_source_files_verified": len(receipt["new_source_files"]),
               "reported_status": receipt.get("status"), "upstream_collection_request_sha256": receipt.get("collection_request_sha256")}
    return bindings, summary


def evaluate(root, plan_version=None):
    from labels import build_labels
    from candidate import run_candidate
    root = require_research_mirror(root, plan_version=plan_version)
    version = plan_version_for(root)
    plan = load_plan() if version == "v1" else load_plan(version)
    manifest, frozen, evidence = prepare_history(root)
    labels = build_labels(root, manifest, as_of_date=plan["as_of_date"])
    try:
        source_bindings, collection_summary = validate_collection_evidence(root, manifest, plan)
        if version == "v2":
            for binding in labels["source_files"]:
                safe_input(root, binding)
                if binding["path"] not in {b["path"] for b in manifest["source_bindings"]} and source_bindings.get(binding["path"]) != binding:
                    raise ValueError("label source outside qualified collection/base bindings")
        result = run_candidate(frozen, labels["rows"], as_of_date=plan["as_of_date"],
                               training_cutoff_date=plan["training_cutoff_date"],
                               validation_end_date=plan["validation_end_date"],
                               holdout_start_date=plan["future_holdout_start_date"],
                               frozen_manifest=evidence, gates=plan["data_gates"])
        result["collection_receipt_sha256"] = collection_summary["sha256"]
    except (OSError, KeyError, ValueError, TypeError) as exc:
        result = {"status": "BLOCKED_COLLECTION_EVIDENCE", "candidate_model": None,
                  "predictions": [], "report": {"training_performed": False,
                  "production_activation_allowed": False, "reason": str(exc),
                  "profitability_improvement_proven": False}}
    result["plan_sha256"] = sha(plan_path(version))
    result["execution_provenance"] = {
        "run_commit": os.environ.get("GITHUB_SHA"), "run_id": os.environ.get("GITHUB_RUN_ID"),
        "source_files": [{"path": str(p.relative_to(ROOT)), "sha256": sha(p)} for p in
                         [HERE / name for name in ("run.py", "labels.py", "candidate.py", "collect.py", "PLAN.json", "COLLECTION.json")]
                         + [ROOT / "src/top10decision/decision/shadow_exit_1000.py",
                            ROOT / "src/top10decision/decision/shadow_exit_minute_truth.py",
                            ROOT / "requirements-dev.lock"]]}
    if version == "v2":
        from work.profit_1000_upgrade.policy_v2 import CONTRACT
        result.update(dict(CONTRACT), plan_version="v2")
        result["execution_provenance"]["source_files"] += [
            {"path": str(p.relative_to(ROOT)), "sha256": sha(p)} for p in
            [HERE / name for name in ("PLAN_V2.json", "COLLECTION_V2.json", "policy_v2.py", "auction_truth.py", "minute_truth.py", "resume_v2.py")]]
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
    parser.add_argument("--plan-version", choices=("v1", "v2"), default="v1")
    parser.add_argument("--resume-zip", type=Path)
    args = parser.parse_args()
    if args.evaluate_root:
        evaluate(args.evaluate_root, plan_version=args.plan_version)
        return 0
    if not args.output:
        parser.error("--output or --evaluate-root required")
    if args.plan_version == "v2" and (not args.resume_zip or args.source_zip or args.tail_zip):
        parser.error("v2 requires --resume-zip only, never the old source/tail import")
    if args.plan_version == "v1" and args.resume_zip:
        parser.error("--resume-zip requires --plan-version v2")
    root = initialize_mirror(args.output, plan_version=args.plan_version)
    imported = []
    if args.plan_version == "v2":
        from work.profit_1000_upgrade.resume_v2 import import_verified_archive
        imported.append(import_verified_archive(root, args.resume_zip, load_plan("v2")))
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
