"""Read-only v3 qualification experiment on two pinned diagnostic dates.

The diagnostic tables never become a new source pair, a label, or a fill.
All frozen candidates on the two dates remain in this report, including any
pending/no-trade result. No model, ledger, return, or feature is constructed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import zipfile

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
sys.path[:0] = [str(CHECKOUT), str(CHECKOUT / "src")]

from work.profit_1000_upgrade import acceptance, auction_truth, auction_truth_v3

SCHEMA = "dc20_auction_v3_two_date_qualification_20260912_v1"
# The immutable GitHub artifact digest; no user-supplied override is accepted.
V2_SHA = "58467518002c587349587eebb32681b1d81c3c8850ca5595b342818157ebfc64"
V2_RUN = "34676871475"
V2_COMMIT = "df6c38806da20066f673875a4a83c37fa895d2e4"
DIAGNOSTIC_SHA = "2131372749a7063ac5976a5a46a46fbfe151d47b922de11ba51eb4861acb3de4"
DIAGNOSTIC_RUN = "34679018528"
DIAGNOSTIC_COMMIT = "1fd82ac0db05a7d2d1ab01315f473ce68a274373"
DATES = ("20250116", "20260817")
DIAGNOSTIC_CODE = {
    "work/profit_1000_upgrade/AUCTION_DIAGNOSTIC_REQUEST.json": "ddde61b12463934c1da39df76cd358a1cfa6f34805f761efd1e7ac7711fc75df",
    "work/profit_1000_upgrade/auction_diagnostic.py": "5e8cca8327717f63d37c5a906ab4951de34d8c5b3fb1322fb3f6e03732e492f6",
    "work/profit_1000_upgrade/auction_truth.py": "b71c2ae223bc07ef110b206a45b778c3ed4aad76273ff32be3984520b8b996af",
    "work/profit_1000_upgrade/collect.py": "9885bb417779638bb85f5a03893137508a17d21ae3ba93d25732cbb4e83af9fa",
}
FLAGS = {"research_only": True, "diagnostic_only": True,
         "source_import_allowed": False, "source_values_modified": False,
         "entry_source_eligible": False, "label_source_eligible": False,
         "training_performed": False, "settlement_performed": False,
         "labels_rebuilt": False, "actual_execution_claimed": False,
         "actual_capacity_verified": False, "production_activation_allowed": False,
         "forward_holdout_evaluated": False, "candidate_fitted_or_reranked": False,
         "profitability_improvement_proven": False, "network_requests": 0}
_IMPLEMENTATIONS = (Path(__file__), HERE / "auction_truth_v3.py", HERE / "acceptance.py")


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _file_sha(path):
    path = Path(path)
    _require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)),
             "REGULAR_UNALIASED_FILE_REQUIRED")
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


_IMPORTED_HASHES = {p: _file_sha(p) for p in _IMPLEMENTATIONS}


def _diagnostic(archive):
    _require(_file_sha(archive) == DIAGNOSTIC_SHA, "DIAGNOSTIC_ZIP_SHA_MISMATCH")
    expected_names = {"auction_diagnostic.json", "request_contract.json",
                      *(day + ".original_data.json" for day in DATES)}
    with zipfile.ZipFile(archive) as bundle:
        infos = bundle.infolist()
        _require(len(infos) == 4 and {i.filename for i in infos} == expected_names,
                 "DIAGNOSTIC_MEMBER_SET_MISMATCH")
        raw = {}
        for info in infos:
            mode = info.external_attr >> 16
            _require(not info.is_dir() and not info.flag_bits & 1
                     and stat.S_IFMT(mode) in (0, stat.S_IFREG)
                     and info.compress_type in (zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED)
                     and info.file_size <= 4_000_000, "UNSAFE_DIAGNOSTIC_MEMBER")
            body = bundle.read(info)
            _require(len(body) == info.file_size, "DIAGNOSTIC_MEMBER_SIZE_MISMATCH")
            raw[info.filename] = body
    docs = {name: acceptance._json(body, name) for name, body in raw.items()}
    receipt = docs["auction_diagnostic.json"]
    _require(receipt["run_id"] == DIAGNOSTIC_RUN and receipt["run_commit"] == DIAGNOSTIC_COMMIT
             and receipt["api_calls"] == 2 and receipt["retries"] == 0
             and receipt["status"] == "DIAGNOSTIC_REQUESTS_COMPLETE", "DIAGNOSTIC_IDENTITY_MISMATCH")
    for key in ("source_import_allowed", "entry_source_eligible", "label_source_eligible",
                "training_performed", "settlement_performed", "production_writes",
                "source_values_modified", "fallback_generated"):
        _require(receipt[key] is False, "DIAGNOSTIC_NOT_READ_ONLY:" + key)
    _require(receipt["diagnostic_only"] is True and receipt["execution_file_bindings"] == DIAGNOSTIC_CODE,
             "DIAGNOSTIC_CODE_OR_KIND_MISMATCH")
    for path, digest in DIAGNOSTIC_CODE.items():
        _require(_file_sha(CHECKOUT / path) == digest, "DIAGNOSTIC_CODE_CHANGED:" + path)
    files = receipt["source_files"]
    _require(len(files) == 3 and {f["path"] for f in files} == expected_names - {"auction_diagnostic.json"},
             "DIAGNOSTIC_BINDING_SET_MISMATCH")
    for binding in files:
        body = raw[binding["path"]]
        _require(binding["sha256"] == _sha(body) and binding["bytes"] == len(body),
                 "DIAGNOSTIC_SOURCE_BINDING_MISMATCH")
    requests = receipt["requests"]
    _require(len(requests) == 2 and tuple(r["trade_date"] for r in requests) == DATES,
             "DIAGNOSTIC_DATES_CHANGED")
    for req in requests:
        day = req["trade_date"]
        _require(req["request"] == auction_truth.request_contract(day)
                 and req["network_request_performed"] is True and req["api_code"] == 0
                 and req["original_table_retained"] is True
                 and req["original_table_sha256"] == _sha(raw[day + ".original_data.json"]),
                 "DIAGNOSTIC_REQUEST_OR_TABLE_CHANGED")
    return docs, receipt


def _daily_rows(raw, day):
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    fields = reader.fieldnames
    _require(fields is not None and len(fields) == len(set(fields))
             and {"ts_code", "trade_date", "open"} <= set(fields), "DAILY_HEADER_INVALID")
    rows = {}
    for row in reader:
        _require(None not in row and all(v is not None for v in row.values())
                 and row["trade_date"] == day, "DAILY_ROW_INVALID")
        code = auction_truth._code(row["ts_code"], market_row=True)
        _require(code not in rows, "DAILY_DUPLICATE_CODE")
        rows[code] = row
    _require(bool(rows), "EMPTY_DAILY_TABLE")
    return rows


def _old_row(data, day, code):
    fields = data["fields"]
    index = fields.index("ts_code")
    items = [r for r in data["items"] if r[index] == code]
    if not items:
        return {"accepted": False, "reason": "ROW_ABSENT"}
    try:
        auction_truth._table({"fields": fields, "items": items}, day, None)
    except auction_truth.AuctionSourceError as exc:
        return {"accepted": False, "reason": str(exc)}
    return {"accepted": True, "reason": None}


def _compare(manifest, saved, daily, docs):
    selected = sorted((r for r in manifest["rows"] if r["exec_date"] in DATES),
                      key=lambda r: (r["signal_date"], r["promotion_rank"]))
    _require(len(selected) == 19 and {r["signal_date"] for r in selected} == {"20250115", "20260814"},
             "PINNED_DIAGNOSTIC_COHORT_CHANGED")
    prior = {(r["signal_date"], r["ts_code"]): r for r in saved["rows"]}
    tables = {day: auction_truth_v3.table_rows(docs[day + ".original_data.json"], day) for day in DATES}
    output = []
    for candidate in selected:
        day, code = candidate["exec_date"], candidate["ts_code"]
        old = prior[(candidate["signal_date"], code)]
        _require(old["exec_date"] == day and old["promotion_rank"] == candidate["promotion_rank"],
                 "FROZEN_LABEL_CANDIDATE_MISMATCH")
        _require(code in daily[day], "CANDIDATE_DAILY_ROW_MISSING")
        opening = daily[day][code]["open"]
        row = tables[day].get(code)
        qualified = auction_truth_v3.qualify_row(row, day, code, opening)
        output.append({"signal_date": candidate["signal_date"], "exec_date": day,
                       "ts_code": code, "promotion_rank": candidate["promotion_rank"],
                       "old_label_status_unchanged": old["label_status"],
                       "old_single_row_check": _old_row(docs[day + ".original_data.json"], day, code),
                       "daily_open": opening, "qualification_v3": qualified,
                       "candidate_source_values_unchanged": row is None or qualified["raw_values"] == dict(row),
                       "price_is_original_reported_value": qualified["price"] is None or qualified["price"] == row["price"],
                       **FLAGS})
    _require(all(r["candidate_source_values_unchanged"] and r["price_is_original_reported_value"] for r in output),
             "REPORT_CHANGED_ORIGINAL_PRICE")
    return output


def generate_report(v2_archive, diagnostic_archive):
    """Compare fixed original tables only; no import, refit, relabel, or I/O writes."""
    for p, expected in _IMPORTED_HASHES.items():
        _require(_file_sha(p) == expected, "IMPLEMENTATION_CHANGED_SINCE_IMPORT")
    _require(_file_sha(v2_archive) == V2_SHA, "V2_ZIP_SHA_MISMATCH")
    audit = acceptance.audit_archive(v2_archive, expected_zip_sha256=V2_SHA,
                                     expected_run_id=V2_RUN, expected_commit=V2_COMMIT)
    docs, receipt = _diagnostic(diagnostic_archive)
    with zipfile.ZipFile(v2_archive) as bundle:
        manifest_raw = bundle.read("research_inputs/manifest.json")
        labels_raw = bundle.read("research_results/labels.json")
        manifest = acceptance._json(manifest_raw, "manifest")
        saved = acceptance._json(labels_raw, "labels")
        bindings = {b["path"]: b["sha256"] for b in saved["source_files"]}
        daily, used = {}, []
        for day in DATES:
            path = f"data/market/raw/{day[:4]}/{day}/daily.csv"
            raw = bundle.read(path)
            _require(bindings.get(path) == _sha(raw), "DAILY_NOT_BOUND_TO_V2_LABELS")
            daily[day] = _daily_rows(raw, day)
            used.append({"path": path, "sha256": _sha(raw)})
    rows = _compare(manifest, saved, daily, docs)
    _require(_file_sha(v2_archive) == V2_SHA and _file_sha(diagnostic_archive) == DIAGNOSTIC_SHA,
             "ARCHIVE_CHANGED_DURING_COMPARISON")
    for p, expected in _IMPORTED_HASHES.items():
        _require(_file_sha(p) == expected, "IMPLEMENTATION_CHANGED_DURING_COMPARISON")
    for path, expected in DIAGNOSTIC_CODE.items():
        _require(_file_sha(CHECKOUT / path) == expected, "FROZEN_DIAGNOSTIC_CODE_CHANGED")
    price_rows = [r for r in rows if r["qualification_v3"]["status"] == "CANONICAL_PRICE_OBSERVED"]
    capacity_rows = [r for r in price_rows if r["qualification_v3"]["capacity_proxy_verified"] is True]
    return {"schema_version": SCHEMA, "status": "OFFLINE_QUALIFICATION_COMPARISON_COMPLETE",
            "v3_policy_id": auction_truth_v3.SOURCE_POLICY_ID,
            "v2_archive_sha256": V2_SHA, "v2_source_run_id": V2_RUN,
            "diagnostic_archive_sha256": DIAGNOSTIC_SHA, "diagnostic_run_id": DIAGNOSTIC_RUN,
            "source_archive_acceptance": audit,
            "candidate_manifest_file_sha256": _sha(manifest_raw),
            "old_labels_file_sha256": _sha(labels_raw), "daily_source_files": used,
            "diagnostic_source_files": receipt["source_files"],
            "rows": rows, "summary": {"candidate_rows": len(rows), "T_dates": len(DATES),
                "old_single_row_accepted": sum(r["old_single_row_check"]["accepted"] for r in rows),
                "price_proxy_qualified": len(price_rows), "capacity_arithmetic_consistent": len(capacity_rows),
                "price_qualified_capacity_unknown": len(price_rows) - len(capacity_rows),
                "unresolved_or_no_trade_rows": len(rows) - len(price_rows),
                "qualification_status_counts": dict(Counter(r["qualification_v3"]["status"] for r in rows)),
                "actual_fills_proven": 0, "returns_computed": False},
            "code_sources": [{"path": p.relative_to(CHECKOUT).as_posix(), "sha256": s}
                             for p, s in _IMPORTED_HASHES.items()],
            "price_evidence_basis": "POSTHOC_ENTRY_PRICE_CHECK_NOT_PRE0925_FEATURE",
            "limitations": ["Two diagnostic dates only; not a new eligible auction source archive.",
                "Raw provider prices are preserved; executable tick precision is not confirmed.",
                "Amount consistency is only arithmetic evidence, not an order fill or 100k capacity.",
                "No full-history labels, strategy return, training, or production activation."], **FLAGS}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v2-archive", type=Path, required=True)
    parser.add_argument("--diagnostic-archive", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(generate_report(args.v2_archive, args.diagnostic_archive),
                     ensure_ascii=False, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
