"""Read-only, bounded integrity audit of one pinned v2 research ZIP.

This does NOT replay prices, refit a model, prove provider bar timestamps, or
authorize production. A complete, internally consistent BLOCKED run is valid
evidence of a blocked stage, never of positive performance.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import re
import stat
import unicodedata
import zipfile

try:
    from . import policy_v2
except ImportError:
    import policy_v2

HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
MAX_MEMBERS = 40_000
MAX_MEMBER_BYTES = 128 * 1024**2
MAX_TOTAL_BYTES = 4 * 1024**3
REQUIRED = {
    "marker": ".dc20-profit-1000-research-root.json",
    "manifest": "research_inputs/manifest.json",
    "base": "research_inputs/base_archive_import.json",
    "collection": "collection_receipt.json",
    "labels": "research_results/labels.json",
    "candidate": "research_results/candidate.json",
    "capital": "research_capital/capital.json",
    "replay": "research_capital/candidate_replay.json",
}
SETTLED = "SETTLED_1000_LIMIT_HOLD_MINUTE_PROXY"
PROOF_FLAGS = ("production_activation_allowed", "actual_execution_claimed",
               "profitability_improvement_proven", "production_release_allowed")
COMMON_CODE = {
    "work/profit_1000_upgrade/" + name for name in (
        "run.py", "labels.py", "candidate.py", "collect.py", "PLAN.json", "COLLECTION.json",
        "PLAN_V2.json", "COLLECTION_V2.json", "policy_v2.py", "auction_truth.py",
        "minute_truth.py", "resume_v2.py")
} | {"src/top10decision/decision/shadow_exit_1000.py",
     "src/top10decision/decision/shadow_exit_minute_truth.py", "requirements-dev.lock"}
CAPITAL_CODE = COMMON_CODE | {
    "work/profit_1000_upgrade/capital.py", "work/profit_1000_upgrade/capital_report.py",
    "src/top10decision/decision/executable_profit_shadow_settlement.py", "requirements.lock"}


class AcceptanceError(ValueError):
    """An artifact failed integrity acceptance; the exception is never success."""


def _require(condition, reason):
    if not condition:
        raise AcceptanceError(reason)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical_sha(value):
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def _json(raw, name):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "DUPLICATE_JSON_KEY:" + name + ":" + key)
            result[key] = value
        return result
    def finite(value):
        number = float(value)
        _require(math.isfinite(number), "NONFINITE_JSON:" + name)
        return number
    def constant(value):
        raise AcceptanceError("NONFINITE_JSON:" + name + ":" + value)
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                          parse_float=finite, parse_constant=constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, AcceptanceError):
            raise
        raise AcceptanceError("INVALID_JSON:" + name) from exc


def _path(value, *, directory=False):
    _require(isinstance(value, str) and value and value == unicodedata.normalize("NFC", value)
             and "\\" not in value and ":" not in value
             and not any(ord(c) < 32 or ord(c) == 127 for c in value), "UNSAFE_MEMBER_PATH")
    text = value[:-1] if directory and value.endswith("/") else value
    _require(all(part not in ("", ".", "..") for part in text.split("/")), "UNSAFE_MEMBER_PATH:" + value)
    return text


def _binding(value):
    _require(isinstance(value, dict) and set(value) == {"path", "sha256"}, "MALFORMED_SOURCE_BINDING")
    path = _path(value["path"])
    _require(isinstance(value["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]),
             "MALFORMED_SOURCE_SHA:" + path)
    return path


def _walk(value, *, with_path=False):
    """Iterative traversal avoids untrusted deep-container recursion."""
    stack = [((), value)]
    while stack:
        path, item = stack.pop()
        if isinstance(item, dict):
            yield (path, item) if with_path else item
            stack.extend((path + (k,), v) for k, v in item.items() if k != "execution_provenance")
        elif isinstance(item, list):
            stack.extend((path + (i,), v) for i, v in enumerate(item))


def _code_provenance(value, expected_paths, expected_run_id, expected_commit):
    _require(isinstance(value, dict), "EXECUTION_PROVENANCE_REQUIRED")
    _require(value.get("run_id") == expected_run_id, "RUN_ID_MISMATCH")
    _require(value.get("run_commit") == expected_commit, "RUN_COMMIT_MISMATCH")
    files = value.get("source_files")
    _require(isinstance(files, list), "CODE_SOURCE_BINDINGS_REQUIRED")
    paths = [_binding(binding) for binding in files]
    _require(len(paths) == len(set(paths)) and set(paths) == expected_paths, "CODE_SOURCE_SET_MISMATCH")
    for binding in files:
        path = CHECKOUT / binding["path"]
        _require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)), "CODE_SOURCE_MISSING_OR_ALIASED")
        _require(_sha(path.read_bytes()) == binding["sha256"], "CODE_SOURCE_SHA_MISMATCH:" + binding["path"])


def _normalize_stage(value):
    return {"2_to_3": "2→3", "3_to_4": "3→4"}.get(value, value)


def _identity(row):
    _require(isinstance(row, dict), "INVALID_CANDIDATE_ROW")
    day, code = row.get("signal_date"), row.get("ts_code")
    _require(isinstance(day, str) and re.fullmatch(r"20\d{6}", day)
             and isinstance(code, str) and re.fullmatch(r"\d{6}\.(SH|SZ)", code), "INVALID_CANDIDATE_IDENTITY")
    return day, code


def _indexed(rows):
    _require(isinstance(rows, list) and rows, "CANDIDATE_ROWS_REQUIRED")
    result = {}
    for row in rows:
        key = _identity(row)
        _require(key not in result, "DUPLICATE_CANDIDATE_IDENTITY")
        result[key] = row
    return result


def _cohorts(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["signal_date"]].append(row)
    result = {}
    for day, values in sorted(grouped.items()):
        terminal = sum(r["label_status"] == SETTLED or r["label_status"] in policy_v2.NO_FILL_STATUSES for r in values)
        complete = terminal == len(values)
        for row in values:
            _require(row.get("cohort_complete") is complete, "COHORT_FLAG_MISMATCH:" + day)
        result[day] = {"expected_rows": len(values), "terminal_rows": terminal, "complete": complete,
                       "statuses": dict(Counter(r["label_status"] for r in values)),
                       "label_available_date": max(r["label_available_date"] for r in values) if complete else None}
    return result


def _quality(rows, plan):
    train, validation = defaultdict(list), defaultdict(list)
    for row in rows:
        day = row["signal_date"]
        if day < plan["training_cutoff_date"]:
            train[day].append(row)
        elif day <= plan["validation_end_date"]:
            validation[day].append(row)
    def terminal(row):
        return row["label_status"] == SETTLED or row["label_status"] in policy_v2.NO_FILL_STATUSES
    complete_train = {d: r for d, r in train.items() if all(terminal(v)
                      and v["label_available_date"] < plan["training_cutoff_date"]
                      and (v.get("actual_exit_date") is None or v["actual_exit_date"] < plan["training_cutoff_date"]) for v in r)}
    complete_val = {d: r for d, r in validation.items() if all(terminal(v) for v in r)}
    training = [v for r in complete_train.values() for v in r]
    valid = [v for r in complete_val.values() for v in r]
    coverage = {
        "training_expected_dates": len(train), "training_complete_dates": len(complete_train),
        "training_complete_rows": len(training), "training_complete_day_fraction": len(complete_train) / len(train) if train else 0.0,
        "training_purged_or_incomplete_dates": sorted(set(train) - set(complete_train)),
        "validation_expected_dates": len(validation), "validation_complete_dates": len(complete_val),
        "validation_complete_rows": len(valid), "validation_complete_day_fraction": len(complete_val) / len(validation) if validation else 0.0,
        "validation_incomplete_dates": sorted(set(validation) - set(complete_val)),
        "validation_filled_rows": sum(v["proxy_fill"] == 1 for v in valid)}
    observations = {"min_train_dates": len(complete_train), "min_train_rows": len(training),
                    "min_validation_dates": len(complete_val), "min_validation_rows": len(valid),
                    "min_validation_filled_rows": coverage["validation_filled_rows"],
                    "min_training_complete_day_fraction": coverage["training_complete_day_fraction"],
                    "min_validation_complete_day_fraction": coverage["validation_complete_day_fraction"]}
    failures = [key for key, observed in observations.items() if observed < plan["data_gates"][key]]
    if len({v["slot_net_return"] for v in training}) < 2:
        failures.append("TRAINING_TARGET_HAS_NO_VARIATION")
    if any(len(r) < 2 for r in complete_val.values()):
        failures.append("VALIDATION_DAY_HAS_FEWER_THAN_TWO_FROZEN_CANDIDATES")
    if len(complete_val) != len(validation):
        failures.append("INCOMPLETE_VALIDATION_COHORTS_CANNOT_BE_DROPPED")
    return coverage, failures, {_identity(v) for v in valid}


def audit_archive(archive, *, expected_zip_sha256, expected_run_id, expected_commit):
    """Return integrity and separate stage states; raise AcceptanceError on failure."""
    _require(isinstance(expected_zip_sha256, str) and re.fullmatch(r"[0-9a-f]{64}", expected_zip_sha256), "EXPECTED_ZIP_SHA_REQUIRED")
    _require(isinstance(expected_run_id, str) and re.fullmatch(r"[1-9]\d*", expected_run_id), "EXPECTED_RUN_ID_REQUIRED")
    _require(isinstance(expected_commit, str) and re.fullmatch(r"[0-9a-f]{40}", expected_commit), "EXPECTED_COMMIT_REQUIRED")
    archive = Path(archive)
    _require(archive.is_file() and not archive.is_symlink(), "ARCHIVE_NOT_REGULAR_FILE")
    _require(archive.stat().st_size <= MAX_TOTAL_BYTES, "ZIP_COMPRESSED_SIZE_BUDGET")
    plan_raw = (HERE / "PLAN_V2.json").read_bytes()
    plan, plan_sha = _json(plan_raw, "PLAN_V2.json"), _sha(plan_raw)
    policy_v2.validate_contract(plan)
    collection_sha = _sha((HERE / "COLLECTION_V2.json").read_bytes())
    with archive.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
        _require(digest == expected_zip_sha256, "ZIP_SHA_MISMATCH")
        handle.seek(0)
        try:
            with zipfile.ZipFile(handle) as bundle:
                infos = bundle.infolist()
                _require(len(infos) <= MAX_MEMBERS, "ZIP_MEMBER_COUNT_BUDGET")
                entries, files, total = {}, set(), 0
                for info in infos:
                    name = _path(info.filename, directory=info.is_dir())
                    _require(name not in entries, "DUPLICATE_MEMBER_PATH:" + name)
                    mode = info.external_attr >> 16
                    _require(not stat.S_ISLNK(mode) and (stat.S_IFMT(mode) in (0, stat.S_IFREG, stat.S_IFDIR)), "NONREGULAR_ZIP_MEMBER:" + name)
                    _require(not (info.flag_bits & 1), "ENCRYPTED_ZIP_MEMBER")
                    _require(info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED), "UNSUPPORTED_ZIP_COMPRESSION")
                    _require(0 <= info.file_size <= MAX_MEMBER_BYTES, "ZIP_MEMBER_SIZE_BUDGET")
                    total += info.file_size
                    _require(total <= MAX_TOTAL_BYTES, "ZIP_TOTAL_SIZE_BUDGET")
                    entries[name] = info
                    if not info.is_dir():
                        files.add(name)
                for name in entries:
                    _require(not any("/".join(name.split("/")[:n]) in files for n in range(1, len(name.split("/")))), "ZIP_FILE_DIRECTORY_ALIAS")
                absent = sorted(set(REQUIRED.values()) - files)
                _require(not absent, "INCOMPLETE_ARTIFACT:" + ",".join(absent))
                hashes, documents, metadata_pairs = {}, {}, []
                for name in sorted(files):
                    info = entries[name]
                    with bundle.open(info) as stream:
                        raw = stream.read(MAX_MEMBER_BYTES + 1)
                        _require(len(raw) == info.file_size and len(raw) <= MAX_MEMBER_BYTES, "ZIP_EXPANSION_SIZE_MISMATCH")
                    hashes[name] = _sha(raw)
                    if name.endswith(".json"):
                        value = _json(raw, name)  # All JSON, including source receipts, is strict.
                        if name.endswith(".meta.json") and isinstance(value, dict) and "data_sha256" in value:
                            metadata_pairs.append((name.removesuffix(".meta.json") + ".data.json", value["data_sha256"]))
                        if name in REQUIRED.values():
                            _require(isinstance(value, dict), "REQUIRED_JSON_OBJECT:" + name)
                            documents[name] = value
                for data_path, data_sha in metadata_pairs:
                    _require(hashes.get(data_path) == data_sha, "SOURCE_METADATA_DATA_SHA_MISMATCH:" + data_path)
                docs = {key: documents[name] for key, name in REQUIRED.items()}
                verified_bindings = _audit_documents(docs, hashes, plan, plan_sha, collection_sha, expected_run_id, expected_commit)
                spec = plan["source_inputs"]["ledger"]
                _require(hashes.get(spec["path"]) == spec["sha256"], "PINNED_LEDGER_MISSING_OR_CHANGED")
                with gzip.GzipFile(fileobj=io.BytesIO(bundle.read(spec["path"]))) as ledger:
                    raw = ledger.read(MAX_MEMBER_BYTES + 1)
                _require(len(raw) <= MAX_MEMBER_BYTES, "LEDGER_EXPANSION_SIZE_BUDGET")
                original = _indexed(list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))))
                manifest_rows = _indexed(docs["manifest"]["rows"])
                _require(set(original) == set(manifest_rows), "PINNED_LEDGER_MEMBERSHIP_CHANGED")
                for key, row in manifest_rows.items():
                    source = original[key]
                    _require(all(row[field] == source[field] for field in ("exec_date", "scheduled_exit_date"))
                             and row["promotion_rank"] == int(source["promotion_rank"])
                             and _normalize_stage(row["stage_transition"]) == _normalize_stage(source["stage_transition"]),
                             "PINNED_LEDGER_CANDIDATE_CHANGED")
        except (zipfile.BadZipFile, RuntimeError, EOFError, OSError) as exc:
            raise AcceptanceError("INVALID_OR_TRUNCATED_ARCHIVE") from exc
    labels, candidate, capital = docs["labels"], docs["candidate"], docs["capital"]
    pending = Counter((r["label_status"], r.get("missing_evidence_kind"), r.get("missing_evidence_date"))
                      for r in labels["rows"] if r["label_status"].startswith("PENDING_"))
    coverage, failures, _ = _quality(labels["rows"], plan)
    return {"schema_version": "dc20_profit_1000_archive_integrity_v1", "artifact_integrity_verified": True,
            "zip_sha256": digest, "run_id": expected_run_id, "run_commit": expected_commit,
            "plan_sha256": plan_sha, "member_count": len(infos), "uncompressed_bytes": total,
            "archive_members_hashed": len(hashes), "bound_archive_sources_verified": len(verified_bindings),
            "local_code_sources_verified": len(CAPITAL_CODE), "historical_rows": len(labels["rows"]),
            "historical_D_dates": len(labels["cohorts_by_date"]),
            "collection": {"status": docs["collection"]["status"], "api_calls": docs["collection"].get("api_calls"),
                           "auction_evidence_complete": docs["collection"].get("auction_evidence_complete")},
            "label_status_counts": dict(Counter(r["label_status"] for r in labels["rows"])),
            "pending_evidence": [{"label_status": k[0], "kind": k[1], "date": k[2], "rows": n}
                                 for k, n in sorted(pending.items(), key=lambda item: tuple(v or "" for v in item[0]))],
            "candidate": {"status": candidate["status"], "training_performed": candidate["report"]["training_performed"],
                          "reported_failed_quality_gates": candidate["report"].get("failed_quality_gates"),
                          "recomputed_failed_quality_gates": failures, "coverage": coverage},
            "capital": {"status": capital["status"], "reason": capital.get("reason")},
            "production_activation_allowed": False, "actual_execution_claimed": False,
            "profitability_improvement_proven": False, "independent_price_replay_performed": False,
            "provider_timestamp_semantics_confirmed": False,
            "limitations": ["Integrity and consistency only, not independent price replay or model reproduction.",
                            "Complete BLOCKED artifacts remain blocked; positive performance is not established.",
                            "Original HTTP envelope digests cannot be recomputed when envelope bytes were not retained."]}


def _audit_documents(docs, hashes, plan, plan_sha, collection_sha, run_id, commit):
    verified_bindings = set()
    for key in ("marker", "manifest", "collection", "candidate", "capital", "replay", "base"):
        _require(docs[key].get("plan_sha256") == plan_sha, "PLAN_SHA_MISMATCH:" + key)
    _require(docs["marker"].get("schema_version") == "dc20_profit_1000_research_mirror_v2"
             and docs["marker"].get("plan_version") == "v2" and docs["marker"].get("production_writes") is False, "MARKER_CONTRACT_MISMATCH")
    for key in ("marker", "manifest", "collection", "labels", "candidate", "capital"):
        policy_v2.validate_contract(docs[key])
    for key, paths in (("candidate", COMMON_CODE), ("capital", CAPITAL_CODE), ("replay", CAPITAL_CODE)):
        _code_provenance(docs[key].get("execution_provenance"), paths, run_id, commit)
    for key, field in (("manifest", "source_bindings"), ("base", "source_files"), ("labels", "source_files"),
                       ("collection", "candidate_source_bindings"), ("collection", "new_source_files"),
                       ("collection", "existing_source_files"), ("capital", "data_source_files")):
        values = docs[key].get(field)
        _require(isinstance(values, list), "SOURCE_BINDING_LIST_REQUIRED:" + key + ":" + field)
        paths = [_binding(value) for value in values]
        _require(len(paths) == len(set(paths)), "DUPLICATE_SOURCE_BINDING:" + key + ":" + field)
    _require(isinstance(docs["collection"].get("request_receipts"), list), "COLLECTION_REQUEST_RECEIPTS_REQUIRED")
    for key, doc in docs.items():
        for position, item in _walk(doc, with_path=True):
            for flag in PROOF_FLAGS:
                _require(flag not in item or item[flag] is False, "UNAUTHORIZED_PROOF_CLAIM:" + key + ":" + flag)
            if "path" in item and "sha256" in item:
                name = _binding(item)
                _require(hashes.get(name) == item["sha256"], "ARCHIVE_SOURCE_SHA_MISMATCH:" + name)
                verified_bindings.add(name)
                _require("stk_auction_o" not in name and "/exit_1000_1m/" not in name, "LEGACY_SOURCE_BOUND_BY_V2:" + name)
            if any(field in item for field in policy_v2.CONTRACT):
                is_account_record = (key == "capital" and len(position) == 6
                                     and position[0] == "capital_comparisons"
                                     and position[1] in {"candidate", "frozen_promotion"}
                                     and position[2] == "accounts" and position[3] in {"top1", "top2"}
                                     and position[4] == "records" and type(position[5]) is int)
                # The published capital kernel intentionally emits only the
                # entry ID on these exact account records, not a source tuple.
                if is_account_record and set(item) & set(policy_v2.CONTRACT) == {"entry_policy_id"}:
                    _require(item["entry_policy_id"] == policy_v2.ENTRY_POLICY_ID, "ACCOUNT_RECORD_ENTRY_POLICY_MISMATCH")
                else:
                    policy_v2.validate_contract(item)
    base, collection = docs["base"], docs["collection"]
    _require(base.get("base_archive") == plan["base_archive"] and base.get("old_auction_and_minute_sources_imported") is False
             and base.get("old_outcome_labels_imported") is False and base.get("production_writes") is False, "BASE_IMPORT_POLICY_MISMATCH")
    base_sources = base.get("source_files")
    _require(isinstance(base_sources, list) and len(base_sources) == plan["base_archive"]["daily_partitions"] + plan["base_archive"]["limit_partitions"]
             and len({v["path"] for v in base_sources}) == len(base_sources), "BASE_IMPORT_SOURCE_COUNT_MISMATCH")
    _require(collection.get("schema_version") == "dc20_profit_1000_collection_receipt_v2"
             and collection.get("collection_request_sha256") == collection_sha
             and collection.get("candidate_rows") == plan["historical_rows"]
             and collection.get("production_writes") is False and collection.get("credential_persisted") is False
             and collection.get("existing_truth_overwritten") is False, "COLLECTION_CONTRACT_MISMATCH")
    _require(collection.get("base_archive_import_binding") == {"path": REQUIRED["base"], "sha256": hashes[REQUIRED["base"]]}, "BASE_IMPORT_BINDING_MISMATCH")
    _require(docs["manifest"].get("source_bindings") == list(plan["source_inputs"].values())
             and collection.get("candidate_source_bindings") == docs["manifest"]["source_bindings"], "CANDIDATE_SOURCE_BINDINGS_CHANGED")
    manifest, label_rows = _indexed(docs["manifest"].get("rows")), _indexed(docs["labels"].get("rows"))
    dates = sorted({key[0] for key in manifest})
    _require(len(manifest) == plan["historical_rows"] and len(dates) == plan["historical_D_dates"]
             and dates[0] == plan["historical_D_start"] and dates[-1] == plan["historical_D_end"]
             and set(label_rows) == set(manifest), "HISTORICAL_SCOPE_OR_LABEL_MEMBERSHIP_CHANGED")
    expected = docs["manifest"].get("expected_candidate_codes")
    actual = defaultdict(list)
    for day, code in manifest:
        actual[day].append(code)
    _require(isinstance(expected, dict) and {d: sorted(c) for d, c in expected.items()} == {d: sorted(c) for d, c in actual.items()}, "MANIFEST_COHORT_CODES_MISMATCH")
    _require(docs["labels"].get("candidate_manifest_sha256") == _canonical_sha(docs["manifest"]), "LABEL_MANIFEST_SHA_MISMATCH")
    _require(docs["labels"].get("as_of_date") == collection.get("as_of_date") == docs["capital"].get("as_of_date") == plan["as_of_date"], "AS_OF_DATE_MISMATCH")
    for key, row in label_rows.items():
        policy_v2.validate_label_contract(row)
        frozen = manifest[key]
        _require(all(row.get(field) == frozen.get(field) for field in ("exec_date", "scheduled_exit_date", "promotion_rank"))
                 and _normalize_stage(row.get("stage_transition")) == _normalize_stage(frozen.get("stage_transition"))
                 and _normalize_stage(row.get("stage_transition")) in {"2→3", "3→4"}, "FROZEN_CANDIDATE_CHANGED")
        _require(row.get("shadow_max_price") is None and row.get("round_trip_cost_rate") == plan["cost_rate"], "LABEL_ENTRY_OR_COST_POLICY_CHANGED")
        status = row.get("label_status")
        _require(isinstance(status, str), "LABEL_STATUS_REQUIRED")
        if status.startswith("PENDING_"):
            _require(all(field in row and row[field] is None for field in ("net_return", "conditional_net_return", "slot_net_return")), "PENDING_LABEL_FALSE_ZERO")
        elif status in policy_v2.NO_FILL_STATUSES:
            _require(type(row.get("proxy_fill")) is int and row["proxy_fill"] == 0 and row.get("slot_net_return") == 0.0
                     and row.get("net_return") is None and row.get("conditional_net_return") is None, "NO_FILL_LABEL_RETURN_MISMATCH")
        elif status == SETTLED:
            _require(type(row.get("proxy_fill")) is int and row["proxy_fill"] == 1 and type(row.get("slot_net_return")) in (int, float)
                     and row["slot_net_return"] == row.get("net_return") == row.get("conditional_net_return"), "SETTLED_LABEL_RETURN_MISMATCH")
        else:
            raise AcceptanceError("UNKNOWN_LABEL_STATUS:" + status)
        if not status.startswith("PENDING_"):
            _require(isinstance(row.get("label_available_date"), str) and row["exec_date"] <= row["label_available_date"] <= plan["as_of_date"], "LABEL_MATURITY_INVALID")
    counts = dict(Counter(r["label_status"] for r in label_rows.values()))
    cohorts = _cohorts(list(label_rows.values()))
    _require(docs["labels"].get("cohorts_by_date") == collection.get("cohorts") == cohorts, "LABEL_COHORT_COUNTS_MISMATCH")
    for key in ("candidate", "collection"):
        _require(docs[key].get("label_status_counts") == counts, "LABEL_STATUS_COUNTS_MISMATCH:" + key)
    if "label_status_counts" in docs["capital"]:
        _require(docs["capital"]["label_status_counts"] == counts, "LABEL_STATUS_COUNTS_MISMATCH:capital")
    if "rebuilt_label_sha256" in docs["capital"]:
        _require(docs["capital"]["rebuilt_label_sha256"] == _canonical_sha(docs["labels"]), "CAPITAL_REBUILT_LABEL_SHA_MISMATCH")
    coverage, failures, valid_keys = _quality(list(label_rows.values()), plan)
    for key in ("candidate", "replay"):
        value, report = docs[key], docs[key].get("report")
        _require(isinstance(report, dict) and type(report.get("training_performed")) is bool
                 and report.get("production_activation_allowed") is False, "CANDIDATE_REPORT_REQUIRED:" + key)
        if "coverage" in report:
            _require(report["coverage"] == coverage and report.get("quality_gates") == plan["data_gates"]
                     and report.get("failed_quality_gates") == failures, "CANDIDATE_QUALITY_GATE_MISMATCH:" + key)
            policy_v2.validate_contract(report)
        if report["training_performed"]:
            _require(value.get("status") == "DEVELOPMENT_ONLY_FITTED" and not failures
                     and report.get("coverage") == coverage and report.get("quality_gates") == plan["data_gates"]
                     and report.get("failed_quality_gates") == []
                     and isinstance(value.get("candidate_model"), dict), "FITTED_CANDIDATE_GATE_VIOLATION")
            predictions = _indexed(value.get("predictions"))
            _require(set(predictions) == valid_keys, "PREDICTION_COHORT_DROPPED")
            policy_v2.validate_contract(value["candidate_model"])
            for row in predictions.values():
                policy_v2.validate_contract(row)
        else:
            _require(str(value.get("status", "")).startswith("BLOCKED_") and value.get("candidate_model") is None
                     and value.get("predictions") == [], "BLOCKED_CANDIDATE_CONTAINS_MODEL_OR_PREDICTIONS")
    _require(docs["capital"].get("candidate_replay_sha256") == _canonical_sha(docs["replay"]), "CAPITAL_CANDIDATE_REPLAY_SHA_MISMATCH")
    _require(docs["replay"].get("capital_report_status") == docs["capital"].get("status"), "CAPITAL_REPLAY_STAGE_MISMATCH")
    if docs["capital"].get("status") != "CAPITAL_REPLAY_COMPLETE":
        _require(str(docs["capital"].get("status", "")).startswith("BLOCKED_") and docs["capital"].get("capital_comparisons") is None, "BLOCKED_CAPITAL_CONTAINS_NAV")
    else:
        comparisons = docs["capital"].get("capital_comparisons")
        _require(docs["candidate"]["report"]["training_performed"] is True
                 and docs["replay"]["report"]["training_performed"] is True
                 and isinstance(comparisons, dict) and set(comparisons) == {"candidate", "frozen_promotion"}, "COMPLETE_CAPITAL_REPORT_MISSING_REPLAYS")
        for comparison in comparisons.values():
            policy_v2.validate_contract(comparison)
            _require(comparison.get("production_activation_allowed") is False
                     and comparison.get("label_verification") == "REBUILT_FROM_BOUND_REPOSITORY_PRICE_SOURCES",
                     "CAPITAL_PRICE_REPLAY_DECLARATION_REQUIRED")
            accounts = comparison.get("accounts")
            _require(isinstance(accounts, dict) and set(accounts) == {"top1", "top2"}, "CAPITAL_TWO_ACCOUNTS_REQUIRED")
            for account in accounts.values():
                _require(isinstance(account, dict) and isinstance(account.get("records"), list)
                         and account["records"], "CAPITAL_ACCOUNT_RECORDS_REQUIRED")
    return verified_bindings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--expected-zip-sha256", required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args()
    try:
        report = audit_archive(args.archive, expected_zip_sha256=args.expected_zip_sha256,
                               expected_run_id=args.expected_run_id, expected_commit=args.expected_commit)
    except (AcceptanceError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"artifact_integrity_verified": False, "error": str(exc),
                          "production_activation_allowed": False, "actual_execution_claimed": False,
                          "profitability_improvement_proven": False}, ensure_ascii=False, allow_nan=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
