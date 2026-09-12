"""Read-only frozen-candidate scope from one already replay-accepted v3 ZIP.

Old labels are integrity-audited only, never returned as new outcomes or used
to select candidates. No extraction, network, collection or training occurs.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import unicodedata
import zipfile

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
BASE_SHA256 = "f35140230a777b58276e58e861ec63076f375a27b0f11d32bd170174658d44ad"
BASE_BYTES = 43_248_418
BASE_RUN_ID = "34684000971"
BASE_RUN_COMMIT = "eb89f4a04be31a30ae6cab3836d0b604cc30d10c"
BASE_ARTIFACT_ID = 10295337206
REPLAY_ACCEPTANCE_SHA256 = "071aa839b15985a6de69cd2acda3b95c97961b7ab629e2827bcae26ece6438db"
PREFLIGHT_T_DATES = ("20250319", "20250826", "20260203")
COUNTS = {"candidate_rows": 6753, "T_dates": 910, "reused_dates": 181,
          "precoverage_dates": 517, "gap_dates": 212, "gap_candidate_pairs": 1860}
MAX_MEMBERS, MAX_MEMBER_BYTES, MAX_TOTAL_BYTES = 20_000, 128 * 1024**2, 2 * 1024**3
MANIFEST = "research_inputs/manifest.json"
COLLECTION, JOURNAL = "collection_receipt.json", "collection_requests.jsonl"
LABELS, SUMMARY = "research_results/labels.json", "research_results/summary.json"
DOCUMENTS = {MANIFEST, COLLECTION, JOURNAL, LABELS, SUMMARY}
SOURCE_ROOT = "data/research/canonical_auction_price_v3"
FIELDS = ["ts_code", "trade_date", "price", "vol", "amount", "pre_close"]
GAP_REASON = "PLACEHOLDER_DETAIL_REQUIRES_SUCCESS_AND_COMPLETE_TABLE"
CODE_PATHS = {"work/profit_1000_upgrade/" + n for n in (
    "research_v3.py", "collect_v3.py", "labels_v3.py", "policy_v3.py", "auction_truth_v3.py",
    "auction_http_v3.py", "auction_truth.py", "collect.py", "minute_truth.py", "acceptance.py",
    "PLAN_V3.json", "COLLECTION_V3.json")} | {"src/top10decision/decision/" + n for n in (
    "executable_profit_shadow_settlement.py", "shadow_exit_1000.py", "shadow_exit_minute_truth.py")}
EXECUTION_PATHS = CODE_PATHS - {"work/profit_1000_upgrade/acceptance.py"}
PLAN_PATH = "work/profit_1000_upgrade/PLAN_V3.json"
CONTRACT_PATH = "work/profit_1000_upgrade/COLLECTION_V3.json"
ADAPTER_PATH = "work/profit_1000_upgrade/auction_http_v3.py"


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _manifest_sha(value):
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def _file_sha(path):
    path = Path(path)
    _require(path.is_file() and stat.S_ISREG(path.stat().st_mode) and
             not any(p.is_symlink() for p in (path, *path.parents)), "REGULAR_UNALIASED_FILE_REQUIRED")
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def verify_base_unchanged(archive):
    path = Path(archive)
    _require(_file_sha(path) == BASE_SHA256 and path.stat().st_size == BASE_BYTES, "PINNED_BASE_ARCHIVE_CHANGED")
    return BASE_SHA256


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    def finite(text):
        value = float(text)
        _require(math.isfinite(value), "NONFINITE_JSON_NUMBER")
        return value
    def invalid(_):
        raise ValueError("NONFINITE_JSON_NUMBER")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_float=finite, parse_constant=invalid)
    except (UnicodeError, json.JSONDecodeError, RecursionError, OverflowError):
        raise ValueError("INVALID_SOURCE_JSON") from None


def _path(name, directory=False):
    _require(isinstance(name, str) and name and len(name.encode("utf-8")) <= 4096
             and "\\" not in name and ":" not in name and not name.startswith("/")
             and not any(ord(c) < 32 or ord(c) == 127 for c in name), "UNSAFE_ARCHIVE_PATH")
    name = name[:-1] if directory and name.endswith("/") else name
    _require(name and all(p not in ("", ".", "..") for p in name.split("/"))
             and unicodedata.normalize("NFC", name) == name, "NONCANONICAL_ARCHIVE_PATH")
    return name


def _audit_zip(archive):
    verify_base_unchanged(archive)
    bindings, documents, metadata, kinds, total = {}, {}, {}, {}, 0
    try:
        with zipfile.ZipFile(archive) as bundle:
            infos = bundle.infolist()
            _require(0 < len(infos) <= MAX_MEMBERS, "ZIP_MEMBER_LIMIT")
            for item in infos:
                _require(item.orig_filename == item.filename, "TRUNCATED_ARCHIVE_NAME")
                name, directory = _path(item.filename, item.is_dir()), item.is_dir()
                _require(name.casefold() not in kinds, "DUPLICATE_OR_ALIASED_MEMBER")
                kinds[name.casefold()] = directory
                mode = stat.S_IFMT(item.external_attr >> 16)
                _require(mode in ((0, stat.S_IFDIR) if directory else (0, stat.S_IFREG)), "NONREGULAR_ZIP_MEMBER")
                _require(not item.flag_bits & 1 and item.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
                         "ENCRYPTED_OR_UNSUPPORTED_MEMBER")
                _require(0 <= item.file_size <= MAX_MEMBER_BYTES and (not directory or item.file_size == 0), "ZIP_MEMBER_SIZE_LIMIT")
                total += item.file_size
                _require(total <= MAX_TOTAL_BYTES, "ZIP_TOTAL_SIZE_LIMIT")
            for name in kinds:
                _require(all(kinds.get(p.as_posix()) is not False for p in PurePosixPath(name).parents
                             if p.as_posix() != "."), "FILE_DIRECTORY_ALIAS")
            for item in infos:
                name, digest, size = _path(item.filename, item.is_dir()), hashlib.sha256(), 0
                capture = name in DOCUMENTS or name.startswith(SOURCE_ROOT + "/") and name.endswith(".meta.json")
                chunks = []
                with bundle.open(item) as handle:
                    for block in iter(lambda: handle.read(1024**2), b""):
                        size += len(block)
                        _require(size <= item.file_size, "DECOMPRESSED_SIZE_MISMATCH")
                        digest.update(block)
                        if capture:
                            chunks.append(block)
                _require(size == item.file_size, "ZIP_MEMBER_SIZE_MISMATCH")
                if not item.is_dir():
                    bindings[name] = {"path": name, "sha256": digest.hexdigest(), "bytes": size}
                    if capture:
                        (documents if name in DOCUMENTS else metadata)[name] = b"".join(chunks)
    except (zipfile.BadZipFile, RuntimeError, EOFError):
        raise ValueError("ZIP_CRC_OR_CONTAINER_INVALID") from None
    _require(DOCUMENTS <= bindings.keys(), "REQUIRED_BASE_DOCUMENT_MISSING")
    verify_base_unchanged(archive)
    return bindings, documents, metadata, {"member_count": len(infos), "uncompressed_bytes": total, "CRC_verified": True}


def _bindings(values, files):
    _require(isinstance(values, list), "BINDING_LIST_REQUIRED")
    result = {}
    for item in values:
        _require(isinstance(item, dict) and set(item) == {"path", "sha256"}, "INVALID_SOURCE_BINDING")
        name = _path(item["path"])
        _require(name not in result and isinstance(item["sha256"], str) and
                 re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) and
                 files.get(name, {}).get("sha256") == item["sha256"], "SOURCE_BINDING_MISMATCH")
        result[name] = item["sha256"]
    return result


def _code_bindings(summary, receipt):
    rows, execution = summary.get("code_bindings"), receipt.get("execution_file_bindings")
    _require(isinstance(rows, list) and isinstance(execution, dict) and set(execution) == EXECUTION_PATHS,
             "OLD_EXECUTION_CODE_SET_CHANGED")
    files = {p: {"sha256": _file_sha(CHECKOUT / p)} for p in CODE_PATHS}
    code = _bindings(rows, files)
    _require(set(code) == CODE_PATHS and all(code[p] == s for p, s in execution.items()), "OLD_CODE_BINDINGS_CHANGED")
    return code


def _date(value):
    _require(isinstance(value, str) and re.fullmatch(r"20\d{6}", value), "INVALID_FROZEN_DATE")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError:
        raise ValueError("INVALID_FROZEN_DATE") from None
    return value


def _candidate_scope(manifest):
    rows, identities, by_date, by_signal, ranks, signal_to_t = manifest.get("rows"), set(), defaultdict(set), defaultdict(list), defaultdict(list), {}
    _require(isinstance(rows, list) and len(rows) == COUNTS["candidate_rows"], "FROZEN_CANDIDATE_COUNT_CHANGED")
    order = []
    for row in rows:
        d, t, code, rank = _date(row["signal_date"]), _date(row["exec_date"]), row["ts_code"], row["promotion_rank"]
        _require(d < t < "20260914" and isinstance(code, str) and re.fullmatch(r"\d{6}\.(SH|SZ)", code)
                 and row["stage_transition"] in ("2_to_3", "3_to_4") and type(rank) is int and rank > 0,
                 "FROZEN_CANDIDATE_IDENTITY_CHANGED")
        _require((d, code) not in identities and code not in by_date[t] and
                 signal_to_t.get(d, t) == t, "DUPLICATE_OR_REVERSED_FROZEN_COHORT")
        identities.add((d, code)); by_date[t].add(code); by_signal[d].append(code); ranks[d].append(rank); signal_to_t[d] = t
        order.append((d, rank, code))
    _require(order == sorted(order) and len(by_date) == len(by_signal) == COUNTS["T_dates"]
             and list(signal_to_t.values()) == sorted(signal_to_t.values())
             and all(values == list(range(1, len(values) + 1)) for values in ranks.values()), "FROZEN_COHORT_ORDER_CHANGED")
    _require(manifest.get("expected_candidate_codes") == dict(by_signal), "FROZEN_EXPECTED_CODES_CHANGED")
    return {day: sorted(codes) for day, codes in sorted(by_date.items())}, identities


def read_base_archive(archive):
    """Verify the fixed ZIP, then derive only frozen T/code request gaps."""
    files, raw, metadata, audit = _audit_zip(archive)
    manifest, receipt, labels, summary = (_json(raw[p]) for p in (MANIFEST, COLLECTION, LABELS, SUMMARY))
    code = _code_bindings(summary, receipt)
    _require(receipt["plan_sha256"] == manifest["plan_sha256"] == summary["plan_sha256"] == code[PLAN_PATH]
             and receipt["collection_contract_sha256"] == code[CONTRACT_PATH]
             and receipt["http_envelope_adapter_sha256"] == code[ADAPTER_PATH], "OLD_PLAN_OR_ADAPTER_BINDING_CHANGED")
    by_date, identities = _candidate_scope(manifest)
    journal = [_json(line) for line in raw[JOURNAL].splitlines()]
    requests = receipt["request_receipts"]
    _require(len(journal) == len(requests) == COUNTS["T_dates"] and
             sorted(journal, key=lambda r: r["trade_date"]) == requests and
             [r["trade_date"] for r in requests] == list(by_date), "ORIGINAL_REQUEST_JOURNAL_CHANGED")
    imported = _bindings(receipt["imported_source_files"], files)
    new = _bindings(receipt["new_source_files"], files)
    _require(receipt["source_files"] == receipt["new_source_files"] and not imported.keys() & new.keys(), "SOURCE_SET_CONFLICT")
    for values in (manifest["source_bindings"], labels["source_files"], [receipt["journal_binding"]],
                   [summary["labels_binding"], summary["collection_receipt_binding"]]):
        _bindings(values, files)
    _require(receipt["journal_binding"]["path"] == JOURNAL and summary["labels_binding"]["path"] == LABELS
             and summary["collection_receipt_binding"]["path"] == COLLECTION
             and labels["candidate_manifest_sha256"] == _manifest_sha(manifest), "AUDIT_DOCUMENT_BINDING_CHANGED")
    _require(set(files) == set(imported) | set(new) | {JOURNAL, COLLECTION, LABELS, SUMMARY}, "UNBOUND_ARCHIVE_FILE")
    label_ids = [(r["signal_date"], r["ts_code"]) for r in labels["rows"]]
    _require(len(label_ids) == len(identities) and set(label_ids) == identities and
             summary["candidate_rows"] == COUNTS["candidate_rows"] and summary["D_dates"] == COUNTS["T_dates"],
             "OLD_LABEL_AUDIT_IDENTITY_CHANGED")
    gaps, reused, pre, used = [], [], [], set()
    for item in requests:
        day = item["trade_date"]
        _require(item["candidate_codes"] == by_date[day] and item["endpoint"] == "stk_auction" and item["ts_code"] is None,
                 "REQUEST_CANDIDATES_OR_ENDPOINT_CHANGED")
        for key in ("plan_sha256", "collection_contract_sha256", "http_envelope_adapter_id", "http_envelope_adapter_sha256"):
            _require(item[key] == receipt[key], "REQUEST_PROVENANCE_CHANGED")
        _require(item["source_policy_id"] == manifest["auction_source_policy_id"], "SOURCE_POLICY_CHANGED")
        paths = [f"{SOURCE_ROOT}/{day[:4]}/{day}/stk_auction.{kind}.json" for kind in ("data", "meta")]
        if day < "20250101":
            _require(item["status"] == "HISTORY_BEFORE_CANONICAL_COVERAGE" and item["network_request_performed"] is False
                     and item["new_source_files"] == [] and not any(p in files for p in paths)
                     and not any(k in item for k in ("request", "http_response_sha256", "http_response_bytes")), "PRE_COVERAGE_DECLARATION_CHANGED")
            pre.append(day)
            continue
        _require(item["network_request_performed"] is True and item["request"] == {
            "api_name": "stk_auction", "params": {"trade_date": day}, "fields": FIELDS}, "OLD_HTTP_REQUEST_CHANGED")
        _require(type(item["http_response_bytes"]) is int and 0 < item["http_response_bytes"] <= 4_000_000 and
                 re.fullmatch(r"[0-9a-f]{64}", item["http_response_sha256"]), "OLD_HTTP_BINDING_CHANGED")
        if item["status"] == "EXACT_TRUTH_WRITTEN":
            bound = _bindings(item["new_source_files"], files)
            _require(set(bound) == set(paths) and all(new.get(p) == s for p, s in bound.items()), "REUSED_PAIR_BINDING_CHANGED")
            meta = _json(metadata[paths[1]])
            _require(meta["request"] == item["request"] and meta["http_response_sha256"] == item["http_response_sha256"]
                     and meta["http_response_bytes"] == item["http_response_bytes"] and meta["rows"] == item["source_rows"]
                     and meta["status"] == item["source_status"] and meta["api_code"] == item["api_code"], "REUSED_HTTP_SOURCE_MISMATCH")
            used.update(paths); reused.append(day)
        else:
            _require(item["status"] == "PENDING_INVALID_SOURCE_NOT_IMPUTED" and item["reason"] == GAP_REASON
                     and item["new_source_files"] == [] and not any(p in files for p in paths), "UNEXPECTED_GAP_REASON_OR_SOURCE")
            gaps.append(day)
    _require(set(new) == used and len(reused) == COUNTS["reused_dates"] and len(pre) == COUNTS["precoverage_dates"]
             and len(gaps) == COUNTS["gap_dates"] and sum(len(by_date[d]) for d in gaps) == COUNTS["gap_candidate_pairs"]
             and all(d in gaps for d in PREFLIGHT_T_DATES), "FIXED_GAP_SCOPE_CHANGED")
    _require(receipt["api_calls"] == len(gaps) + len(reused) and receipt["request_status_counts"] == dict(Counter(r["status"] for r in requests)),
             "ORIGINAL_REQUEST_ACCOUNTING_CHANGED")
    for p, digest in code.items():
        _require(_file_sha(CHECKOUT / p) == digest, "OLD_CODE_CHANGED_DURING_READ")
    verify_base_unchanged(archive)
    return {"scope_schema_version": "dc20_stocks_scope_inputs_v1", "base_archive": {
        "sha256": BASE_SHA256, "bytes": BASE_BYTES, "run_id": BASE_RUN_ID, "run_commit": BASE_RUN_COMMIT,
        "artifact_id": BASE_ARTIFACT_ID, "run_metadata_basis": "PINNED_EXTERNAL_RUN_METADATA",
        "prior_full_replay_acceptance_sha256": REPLAY_ACCEPTANCE_SHA256}, "frozen_manifest": manifest,
        "gap_dates": gaps, "gap_candidate_codes": {d: by_date[d] for d in gaps}, "reused_dates": reused,
        "precoverage_dates": pre, "preflight_T_dates": list(PREFLIGHT_T_DATES),
        "candidate_rows": COUNTS["candidate_rows"], "T_dates": COUNTS["T_dates"],
        "gap_candidate_pairs": COUNTS["gap_candidate_pairs"], "archive_file_bindings": [files[p] for p in sorted(files)],
        "code_bindings": [{"path": p, "sha256": s} for p, s in sorted(code.items())], "archive_audit": audit,
        "old_outcomes_used_for_scope": False, "network_requests": 0, "training_performed": False, "files_written": 0}
