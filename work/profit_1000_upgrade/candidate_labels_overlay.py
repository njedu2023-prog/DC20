"""Explicit single-candidate source overlay for the unchanged v3 economics.

The frozen v3 builder still owns every base row. Only an independently verified
collection may supply an originally missing (T, code). No old LoadedSource is
constructed, no module is patched, no source/label/model/ledger file is written.
Source qualification is a new, auditable step; source-only metadata stays intact.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import stat

from work.profit_1000_upgrade import auction_candidate_scope as candidate_source
from work.profit_1000_upgrade import auction_truth_v3 as qualification
from work.profit_1000_upgrade import labels_v3 as base_labels, minute_truth, policy_v3

OVERLAY_POLICY_ID = "dc20_candidate_auction_label_overlay_20260913_v1"
LABEL_SCHEMA = "dc20_profit_1000_research_candidate_overlay_labels_v1"
CONTRACT = {
    "overlay_policy_id": OVERLAY_POLICY_ID,
    "entry_policy_id": policy_v3.ENTRY_POLICY_ID,
    "exit_policy_id": base_labels.EXIT_POLICY_ID,
    "auction_source_policy_ids": [policy_v3.AUCTION_SOURCE_POLICY_ID, candidate_source.SOURCE_POLICY_ID],
    "auction_qualification_policy_id": qualification.SOURCE_POLICY_ID,
    "minute_source_policy_id": policy_v3.MINUTE_SOURCE_POLICY_ID,
    "minute_time_semantics": policy_v3.MINUTE_TIME_SEMANTICS,
    "complete_empty_response_rule": "DAILY_OPEN_PROXY_CAPACITY_UNKNOWN",
    "missing_rejected_invalid_response_rule": "PENDING_NOT_FALLBACK_NOT_ZERO",
    "round_trip_cost_rate": .0045,
}
_FROZEN = {
    "work/profit_1000_upgrade/labels_v3.py": "cfe592fd7d90101514204d45d4e7f3a27e12f13bd8d3905fc77d89c9dee2fe9b",
    "work/profit_1000_upgrade/policy_v3.py": "a384365e7200643738c4f03e6f8236cd4ac8984eb879ff69752f5a4de0ad729b",
    "work/profit_1000_upgrade/auction_truth_v3.py": "889765e435f2e44c6bedc21ef74789c5155081160da54624a147fdd3bca43665",
    "work/profit_1000_upgrade/auction_truth.py": "b71c2ae223bc07ef110b206a45b778c3ed4aad76273ff32be3984520b8b996af",
    "work/profit_1000_upgrade/minute_truth.py": "69f2eb9fe2ceb1579a59d5247a3f1ddc72b80d251abbed9fe69e06e0d83df268",
    "src/top10decision/decision/shadow_exit_1000.py": "23d1e693bbf4e38771b107f434aab7cd40f82855e0f480e391d819778e05c5af",
    "src/top10decision/decision/executable_profit_shadow_settlement.py": "acb2cf0a74d5fe7849a1b480b512834916954d3c43ce4f71534c1cccdf92ea12",
    "src/top10decision/decision/shadow_exit_minute_truth.py": "0cdd36ed69e734ab8c59bb3b44a5bf27cc702a9ce67879a225f4a94d1d14ee65",
}
_CHECKOUT = Path(__file__).resolve().parents[2]
_SELF_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_CANDIDATE_SHA = hashlib.sha256(Path(candidate_source.__file__).read_bytes()).hexdigest()


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _guard():
    for relative, digest in {**_FROZEN, "work/profit_1000_upgrade/candidate_labels_overlay.py": _SELF_SHA,
                             "work/profit_1000_upgrade/auction_candidate_scope.py": _CANDIDATE_SHA}.items():
        base_labels._binding(_CHECKOUT, {"path": relative, "sha256": digest})
    candidate_source._guard()


def _authority(value):
    # Imported lazily so the pure qualification/replay helpers are testable;
    # absence of the verifier is a closed gate, never a duck-typed permission.
    from work.profit_1000_upgrade.candidate_scope_verify import VerifiedCandidateCollection
    _require(type(value) is VerifiedCandidateCollection, "VERIFIED_CANDIDATE_COLLECTION_REQUIRED")
    value.assert_unchanged()
    return value


def _market_authority(value):
    from work.profit_1000_upgrade.minute_gap_verify import VerifiedMinuteCollection
    _require(type(value) is VerifiedMinuteCollection, "VERIFIED_MINUTE_COLLECTION_REQUIRED")
    value.assert_unchanged()
    return value


def _root(path):
    value = Path(path)
    _require(".." not in value.parts and value.is_dir()
             and not any(p.is_symlink() for p in (value, *value.parents)), "UNALIASED_SOURCE_ROOT_REQUIRED")
    return value.resolve(strict=True)


def _base_inventory(root, bindings):
    expected = {}
    for item in bindings:
        binding = {key: item[key] for key in ("path", "sha256")}
        _require(binding["path"] not in expected, "DUPLICATE_BASE_BINDING")
        base_labels._binding(root, binding)
        expected[binding["path"]] = binding["sha256"]
    actual = set()
    for path in root.rglob("*"):
        _require(not path.is_symlink(), "BASE_SYMLINK_FORBIDDEN")
        if path.is_file():
            _require(stat.S_ISREG(path.stat().st_mode) and path.stat().st_nlink == 1, "BASE_HARDLINK_FORBIDDEN")
            actual.add(path.relative_to(root).as_posix())
        else:
            _require(path.is_dir(), "BASE_NONREGULAR_FILE_FORBIDDEN")
    _require(actual == set(expected), "BASE_EXTRA_OR_MISSING_FILES")
    return expected


def _prior_report(path, expected_sha):
    path = Path(path)
    _require(type(expected_sha) is str and re.fullmatch(r"[0-9a-f]{64}", expected_sha), "PRIOR_LABEL_SHA_REQUIRED")
    _require(".." not in path.parts and not any(p.is_symlink() for p in (path, *path.parents))
             and path.is_file(), "UNALIASED_PRIOR_LABEL_FILE_REQUIRED")
    info = path.stat()
    _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 < info.st_size <= 128 * 1024**2,
             "BOUNDED_REGULAR_PRIOR_LABEL_FILE_REQUIRED")
    with path.open("rb") as handle:
        raw = handle.read(128 * 1024**2 + 1)
    _require(hashlib.sha256(raw).hexdigest() == expected_sha, "REGISTERED_PRIOR_LABEL_SHA_CHANGED")
    report = minute_truth._parse(raw)
    _require(type(report) is dict, "PRIOR_LABEL_REPORT_OBJECT_REQUIRED")
    _digest(report)  # Reject nonfinite JSON numbers anywhere, not just outcomes.
    return report


def _minute_payload(root, pair):
    payload = minute_truth.load(root, *pair)
    _require(payload is not None and payload.get("time_semantics") == policy_v3.MINUTE_TIME_SEMANTICS
             and payload.get("timestamp_semantics") == "BAR_END" and payload.get("complete_session") is True
             and payload.get("provider_timestamp_semantics_confirmed") is False
             and payload.get("production_activation_allowed") is False and payload.get("research_only") is True,
             "UNQUALIFIED_REGISTERED_MINUTE_SOURCE")
    return payload


def _market_admission(root, candidate_root, authority, manifest, as_of, *,
                      verified_market_scope, market_source_root, prior_label_report):
    """Admit only byte-bound, newly absent minute pairs; no source rewriting."""
    market = _market_authority(verified_market_scope)
    market_root = _root(market_source_root)
    roots = (root, candidate_root, market_root)
    _require(market_root == _root(market.root)
             and all(a != b and a not in b.parents and b not in a.parents
                     for index, a in enumerate(roots) for b in roots[index + 1:]), "MINUTE_SOURCE_ROOT_MISMATCH")
    _require(market.as_of_date == as_of, "MINUTE_AS_OF_DATE_CHANGED")
    for digest in (market.receipt_sha256, market.plan_sha256, market.label_report_sha256):
        _require(type(digest) is str and re.fullmatch(r"[0-9a-f]{64}", digest), "MINUTE_AUTHORITY_SHA_REQUIRED")
    prior = _prior_report(prior_label_report, market.label_report_sha256)
    expected = {"schema_version": LABEL_SCHEMA, "source_overlay_contract": CONTRACT,
                "candidate_manifest_sha256": _digest(manifest), "base_archive_sha256": authority.base_archive_sha256,
                "candidate_collection_receipt_sha256": authority.receipt_sha256, "as_of_date": as_of,
                "entry_policy_id": policy_v3.ENTRY_POLICY_ID, "label_policy_id": base_labels.EXIT_POLICY_ID,
                "round_trip_cost_rate": .0045, "feature_evidence_kind": manifest["evidence_kind"],
                "feature_columns": manifest["feature_columns"], "research_only": True,
                "historical_counterfactual": True, "training_performed": False,
                "production_activation_allowed": False, "actual_execution_claimed": False,
                "source_only_metadata_rewritten": False, "files_written": 0}
    for key, value in expected.items():
        _require(key in prior and _digest(prior[key]) == _digest(value), "PRIOR_LABEL_CONTRACT_OR_AUTHORITY_CHANGED")
    _require(all(prior.get(k) is None for k in ("market_collection_receipt_sha256", "market_registered_plan_sha256",
                                               "market_registered_label_report_sha256")), "MULTIPLE_MINUTE_ADMISSION_CHAIN_NOT_REGISTERED")
    prior_rows = prior.get("rows")
    _require(type(prior_rows) is list and all(type(r) is dict for r in prior_rows), "PRIOR_LABEL_ROWS_REQUIRED")
    members = {(d, c) for d, codes in manifest["expected_candidate_codes"].items() for c in codes}
    identities = [(r.get("signal_date"), r.get("ts_code")) for r in prior_rows]
    _require(len(identities) == len(set(identities)) and set(identities) == members, "PRIOR_FULL_CANDIDATE_IDENTITIES_CHANGED")
    missing = set()
    for row in prior_rows:
        validate_label_contract(row)
        if row.get("label_status") != "PENDING_EXIT_MISSING_MINUTES":
            continue
        _require(row.get("missing_evidence_kind") == "research_exit_1000_1m_0931"
                 and row.get("missing_evidence_code") == row["ts_code"], "PRIOR_MISSING_MINUTE_IDENTITY_CHANGED")
        pair = (row.get("missing_evidence_date"), row["ts_code"])
        minute_truth._identity(*pair)
        _require(row["scheduled_exit_date"] <= pair[0] <= as_of, "PRIOR_MISSING_MINUTE_OUTSIDE_HOLDING_WINDOW")
        missing.add(pair)
    pairs, successes = tuple(market.gap_pairs), tuple(market.successful_pairs)
    _require(pairs == tuple(sorted(set(pairs))) and set(pairs) == missing
             and successes == tuple(sorted(set(successes))) and set(successes) <= missing,
             "REGISTERED_MINUTE_PAIRS_NOT_EXACT_PRIOR_GAPS")
    old_files = {b["path"]: b["sha256"] for b in authority.base_file_bindings}
    candidate_files = {b["path"]: b["sha256"] for b in authority.source_bindings}
    seen = set()
    _require(type(prior.get("source_files")) is list and prior["source_files"], "PRIOR_SOURCE_BINDINGS_REQUIRED")
    for item in prior["source_files"]:
        _require(type(item) is dict and set(item) == {"origin", "path", "sha256"}, "PRIOR_SOURCE_BINDING_CHANGED")
        key = (item["origin"], item["path"])
        allowed = old_files if item["origin"] == "base" else candidate_files if item["origin"] == "candidate" else {}
        _require(key not in seen and allowed.get(item["path"]) == item["sha256"], "PRIOR_SOURCE_NOT_IN_ORIGINAL_AUTHORITIES")
        seen.add(key)
    bound = {(b["path"], b["sha256"]) for b in market.source_bindings}
    _require(len(bound) == len(market.source_bindings), "DUPLICATE_MINUTE_SOURCE_BINDING")
    expected_paths, additions = set(), {}
    for pair in pairs:
        names = [p.relative_to(root).as_posix() for p in minute_truth.paths(root, *pair)]
        _require(not any(name in old_files for name in names), "MINUTE_OVERLAY_CANNOT_REPLACE_EXISTING_OR_ORPHAN_SOURCE")
        if pair not in successes:
            continue
        expected_paths.update(names)
        original, copied = _minute_payload(market_root, pair), _minute_payload(root, pair)
        _require(_digest(original) == _digest(copied), "AUGMENTED_MINUTE_BYTES_OR_SEMANTICS_CHANGED")
        for item in original["source_files"]:
            _require((item["path"], item["sha256"]) in bound, "UNBOUND_REGISTERED_MINUTE_SOURCE")
            for source_root in (market_root, root):
                file, _ = base_labels._binding(source_root, item)
                _require(file.stat().st_nlink == 1, "MINUTE_SOURCE_HARDLINK_FORBIDDEN")
            additions[item["path"]] = item["sha256"]
    _require({p for p, _ in bound} == expected_paths == set(additions)
             and len(bound) == len(additions), "MINUTE_SOURCE_BINDINGS_NOT_EXACT_SUCCESS_PAIRS")
    return market, market_root, prior, additions


def qualify_candidate(loaded, day, code, daily_open, *, daily_source_binding):
    """New source identity + frozen v3 per-row economics, not a legacy preload."""
    _require(type(loaded) is candidate_source.LoadedCandidateSource and loaded.trade_date == day
             and loaded.ts_code == code, "EXACT_LOADED_CANDIDATE_SOURCE_REQUIRED")
    _require(set(daily_source_binding) == {"path", "sha256"}, "BOUND_DAILY_SOURCE_REQUIRED")
    if loaded.status == "CANDIDATE_TABLE_PRESENT":
        _require(set(loaded.rows) == {code}, "SINGLE_CANDIDATE_ROW_REQUIRED")
        result = qualification.qualify_row(loaded.rows[code], day, code, daily_open)
        result["entry_price_source"] = "TUSHARE_STK_AUCTION"
    else:
        _require(loaded.status == "CANDIDATE_TABLE_EMPTY" and not loaded.rows, "COMPLETE_EMPTY_SOURCE_REQUIRED")
        base_labels._finite(daily_open, positive=True)
        reason = "CANONICAL_ROW_ABSENT_AFTER_VALID_REQUEST"
        result = {"status": "DAILY_OPEN_PRICE_PROXY_CAPACITY_UNKNOWN", "price": daily_open,
                  "price_qualified": False, "reported_auction_price": None, "reported_auction_amount": None,
                  "volume": None, "amount": None, "capacity_amount": None, "raw_values": None,
                  "amount_identity_delta": None, "daily_open_cent_match": None, "auction_trade_observed": None,
                  "entry_price_source": "DAILY_OPEN_PROXY", "fallback_reason": reason,
                  "capacity_proxy_verified": False, "capacity_evidence": "UNKNOWN", "capacity_reason": reason,
                  "source_import_allowed": False, **qualification.FLAGS}
    return {**result, "source_policy_id": candidate_source.SOURCE_POLICY_ID,
            "qualification_policy_id": qualification.SOURCE_POLICY_ID, "overlay_policy_id": OVERLAY_POLICY_ID,
            "trade_date": day, "ts_code": code, "daily_open": daily_open,
            "daily_source_origin": "base", "daily_source_binding": dict(daily_source_binding),
            "source_origin": "candidate", "source_files": [dict(b) for b in loaded.source_files],
            "loaded_source_qualification_claimed": True, "source_only_metadata_rewritten": False}


def _resume(root, seed, loaded, dates, as_of, bind):
    """Resume an entry-missing seed; no candidate selection or exit math copied."""
    row = deepcopy(seed)
    row.update(auction_source_policy_id=candidate_source.SOURCE_POLICY_ID,
               auction_qualification_policy_id=qualification.SOURCE_POLICY_ID,
               source_overlay_policy_id=OVERLAY_POLICY_ID,
               missing_evidence_kind=None, missing_evidence_date=None, missing_evidence_code=None)
    settlement, code = base_labels.settlement, row["ts_code"]
    t, t1 = row["exec_date"], row["scheduled_exit_date"]

    def missing(day, kind):
        row.update(missing_evidence_date=day, missing_evidence_code=code, missing_evidence_kind=kind)

    def table(day, name):
        _require(day <= as_of, "FUTURE_MARKET_TRUTH_FORBIDDEN")
        path = settlement._find_market_file(root, day, name)
        if path is None:
            missing(day, name)
            return None, None
        bind("base", settlement._source_binding(root, path))
        value = settlement._market_rows(path, day).get(code)
        if value is None:
            missing(day, name)
        return path, value

    try:
        daily_path, daily = table(t, "daily")
        _, limits = table(t, "stk_limit")
        _require(daily is not None and limits is not None, "VALIDATED_BASE_ENTRY_ROW_DISAPPEARED")
        prices = {key: base_labels._finite(daily.get(key), positive=True) for key in ("open", "high", "low", "close", "pre_close")}
        volume = base_labels._finite(daily.get("vol"))
        up, down = (base_labels._finite(limits.get(key), positive=True) for key in ("up_limit", "down_limit"))
        _require(volume >= 0 and prices["low"] <= min(prices["open"], prices["close"])
                 <= max(prices["open"], prices["close"]) <= prices["high"]
                 and down < up and prices["high"] <= up + 1e-8 and prices["low"] >= down - 1e-8,
                 "INVALID_T_DAILY_OHLC_LIMIT_VOLUME")
        flat = all(settlement._same_rounded_price(prices["close"], prices[k]) for k in ("open", "high", "low"))
        row.update(t_daily_volume=volume, t_daily_ohlc_flat_at_cent=flat,
                   t_opening_limit_up_observed=settlement._same_rounded_price(prices["open"], up))
        _require(volume != 0 or flat, "ZERO_VOLUME_NONFLAT_DAILY")
        price = qualify_candidate(loaded, t, code, prices["open"],
                                  daily_source_binding=settlement._source_binding(root, daily_path))
        for binding in loaded.source_files:
            bind("candidate", binding)
        row.update(entry_price=price["price"], entry_price_source=price["entry_price_source"],
                   entry_price_evidence=price, auction_request_receipt_observed=True,
                   entry_qualification_status=price["status"], entry_price_fallback_reason=price["fallback_reason"])
        for key in ("auction_trade_observed", "capacity_evidence", "price_qualified", "capacity_amount",
                    "capacity_reason", "reported_auction_amount", "amount_identity_delta", "daily_open_cent_match",
                    "capacity_proxy_verified"):
            row[key] = price[key]
        if price["status"].startswith("PENDING_CANONICAL_"):
            row["label_status"] = "PENDING_ENTRY_" + price["status"].removeprefix("PENDING_CANONICAL_")
            return row
        _require(price["status"] in {"CANONICAL_PRICE_OBSERVED", "DAILY_OPEN_PRICE_PROXY_CAPACITY_UNKNOWN",
                                     "OBSERVED_NO_AUCTION_TRADE"}, "UNKNOWN_ENTRY_QUALIFICATION")
        if volume == 0 and price["auction_trade_observed"] is True:
            row["label_status"] = "PENDING_ENTRY_SOURCE_CONFLICT"
            return row
        status = None
        if price["status"] == "OBSERVED_NO_AUCTION_TRADE":
            status = "NO_FILL_CANONICAL_AUCTION_ZERO_VOLUME"
        elif volume == 0:
            status = "NO_FILL_SUSPENDED"
        elif not settlement._same_rounded_price(price["price"], prices["open"]):
            row["label_status"] = "PENDING_ENTRY_SOURCE_CONFLICT"
            return row
        elif settlement._same_rounded_price(price["price"], up):
            status = "NO_FILL_OPENING_LIMIT_UP_UNCONFIRMED"
        elif price["capacity_proxy_verified"] and price["amount"] * settlement.MAX_AUCTION_PARTICIPATION + 1e-9 < settlement.SHADOW_NOTIONAL_CNY:
            status = "NO_FILL_CAPACITY"
        if status:
            row.update(label_status=status, proxy_fill=0, slot_net_return=0.,
                       label_available_date=t, label_available_at=base_labels._at(t, "15:00:00"),
                       label_maturity_at=base_labels._at(t, "09:25:00"))
            return row
        row["proxy_fill"] = 1

        def minutes(day):
            _require(day <= as_of, "FUTURE_MINUTE_TRUTH_FORBIDDEN")
            try:
                payload = minute_truth.load(root, day, code)
                if payload is not None:
                    _require(payload.get("time_semantics") == policy_v3.MINUTE_TIME_SEMANTICS
                             and payload.get("provider_timestamp_semantics_confirmed") is False
                             and payload.get("production_activation_allowed") is False
                             and payload.get("research_only") is True, "UNQUALIFIED_MINUTE_SEMANTICS")
                    for binding in payload["source_files"]:
                        bind("base", binding)
            except (ValueError, OSError, TypeError, KeyError) as exc:
                raise base_labels._ResearchSourceFailure("PENDING_EXIT_INVALID_RESEARCH_MINUTE_SOURCE", type(exc).__name__) from None
            if payload is None:
                missing(day, "research_exit_1000_1m_0931")
            else:
                row["minute_source_observed"] = True
                row["minute_source_observed_dates"] = sorted(set(row["minute_source_observed_dates"]) | {day})
            return payload

        result, status = base_labels.resolve_exit_1000(
            dates, t1, as_of, code, price["price"], prices["close"],
            lambda day: table(day, "daily")[1], lambda day: table(day, "stk_limit")[1], minutes)
        row["label_status"] = status
        if result is not None:
            _require(row["minute_source_observed"], "SETTLEMENT_REQUIRES_MINUTES")
            net = result["gross_return"] - base_labels.COST_RATE
            row.update(label_status=base_labels.SETTLED, net_return=net, conditional_net_return=net,
                       slot_net_return=net, actual_exit_date=result["actual_exit_date"],
                       actual_exit_time=result["actual_exit_time"], decision_time=result["decision_time"],
                       held_limit_up_sessions=result["held_limit_up_sessions"], label_maturity_at=result["actual_exit_time"],
                       label_available_date=result["actual_exit_date"],
                       label_available_at=base_labels._at(result["actual_exit_date"], "15:00:00"), exit_evidence=result)
    except base_labels._ResearchSourceFailure as exc:
        row.update(label_status=exc.status, error_type=exc.error_type,
                   net_return=None, conditional_net_return=None, slot_net_return=None)
    except (ValueError, OSError, TypeError, settlement.ExecutableProfitSettlementError) as exc:
        row.update(label_status="PENDING_INVALID_SOURCE", error_type=type(exc).__name__,
                   net_return=None, conditional_net_return=None, slot_net_return=None)
    return row


def validate_label_contract(row):
    """Validate actual source identity separately; never project it into old IDs."""
    _require(isinstance(row, Mapping), "OVERLAY_LABEL_MAPPING_REQUIRED")
    if row.get("auction_source_policy_id") == policy_v3.AUCTION_SOURCE_POLICY_ID:
        _require("source_overlay_policy_id" not in row and "auction_qualification_policy_id" not in row,
                 "BASE_ROW_CANNOT_CLAIM_CANDIDATE_OVERLAY")
        return policy_v3.validate_label_contract(row)
    _require(row.get("auction_source_policy_id") == candidate_source.SOURCE_POLICY_ID
             and row.get("auction_qualification_policy_id") == qualification.SOURCE_POLICY_ID
             and row.get("source_overlay_policy_id") == OVERLAY_POLICY_ID
             and row.get("entry_policy_id") == policy_v3.ENTRY_POLICY_ID
             and row.get("label_policy_id") == base_labels.EXIT_POLICY_ID
             and row.get("minute_source_policy_id") == policy_v3.MINUTE_SOURCE_POLICY_ID
             and row.get("minute_time_semantics") == policy_v3.MINUTE_TIME_SEMANTICS,
             "OVERLAY_SOURCE_ECONOMIC_CONTRACT_CHANGED")
    _require("source_policy_contract" not in row and row.get("plan_version", "v3") == "v3"
             and not any(isinstance(k, str) and k.endswith("source_policy_id")
                         and k not in {"auction_source_policy_id", "minute_source_policy_id"} for k in row),
             "OVERLAY_UNKNOWN_OR_NESTED_SOURCE_CONTRACT")
    for key, expected in {"research_only": True, "known_before_0925": False, "price_reporting_precision_confirmed": False,
                          "reported_price_preserved": True, "actual_capacity_verified": False,
                          "actual_execution_claimed": False, "production_activation_allowed": False}.items():
        _require(row.get(key) is expected, "OVERLAY_QUALIFICATION_FLAGS_CHANGED")
    _require(row.get("basis") == policy_v3.BASIS and row.get("round_trip_cost_rate") == .0045
             and row.get("shadow_max_price") is None, "OVERLAY_ECONOMICS_CHANGED")
    for key in ("minute_source_observed", "auction_request_receipt_observed", "capacity_proxy_verified", "price_qualified"):
        _require(type(row.get(key)) is bool, "OVERLAY_EVIDENCE_BOOL_REQUIRED")
    _require(row.get("auction_trade_observed") is None or type(row["auction_trade_observed"]) is bool,
             "OVERLAY_AUCTION_OBSERVATION_BOOL_REQUIRED")
    _require(row.get("entry_price_source") in {None, "TUSHARE_STK_AUCTION", "DAILY_OPEN_PROXY"},
             "OVERLAY_ENTRY_PRICE_SOURCE_CHANGED")
    evidence = row.get("entry_price_evidence")
    if evidence is not None:
        _require(isinstance(evidence, Mapping) and evidence.get("source_policy_id") == candidate_source.SOURCE_POLICY_ID
                 and evidence.get("qualification_policy_id") == qualification.SOURCE_POLICY_ID
                 and evidence.get("overlay_policy_id") == OVERLAY_POLICY_ID
                 and evidence.get("source_origin") == "candidate" and evidence.get("daily_source_origin") == "base"
                 and evidence.get("source_only_metadata_rewritten") is False
                 and evidence.get("loaded_source_qualification_claimed") is True
                 and evidence.get("basis") == policy_v3.BASIS and evidence.get("known_before_0925") is False
                 and evidence.get("price_reporting_precision_confirmed") is False
                 and evidence.get("reported_price_preserved") is True and evidence.get("research_only") is True
                 and evidence.get("production_activation_allowed") is False
                 and evidence.get("trade_date") == row.get("exec_date") and evidence.get("ts_code") == row.get("ts_code")
                 and evidence.get("actual_execution_claimed") is False and evidence.get("actual_capacity_verified") is False,
                 "OVERLAY_ENTRY_SOURCE_IDENTITY_CHANGED")
        for label_key, evidence_key in (("entry_price", "price"), ("entry_price_source", "entry_price_source"),
                ("entry_qualification_status", "status"), ("capacity_proxy_verified", "capacity_proxy_verified"),
                ("capacity_amount", "capacity_amount"), ("capacity_reason", "capacity_reason"),
                ("capacity_evidence", "capacity_evidence"), ("price_qualified", "price_qualified"),
                ("auction_trade_observed", "auction_trade_observed")):
            _require(row.get(label_key) == evidence.get(evidence_key), "OVERLAY_ENTRY_LABEL_MISMATCH")
        if evidence.get("entry_price_source") == "TUSHARE_STK_AUCTION":
            proof = qualification.qualify_row(evidence.get("raw_values"), row["exec_date"], row["ts_code"], evidence.get("daily_open"))
            for key in ("status", "price", "price_qualified", "capacity_amount", "capacity_proxy_verified", "capacity_reason"):
                _require(proof.get(key) == evidence.get(key), "OVERLAY_PRICE_OR_CAPACITY_REQUALIFICATION_FAILED")
        else:
            _require(evidence.get("entry_price_source") == "DAILY_OPEN_PROXY"
                     and evidence.get("status") == "DAILY_OPEN_PRICE_PROXY_CAPACITY_UNKNOWN"
                     and evidence.get("fallback_reason") == "CANONICAL_ROW_ABSENT_AFTER_VALID_REQUEST"
                     and evidence.get("price") == evidence.get("daily_open")
                     and evidence.get("capacity_proxy_verified") is False and evidence.get("price_qualified") is False,
                     "OVERLAY_UNQUALIFIED_FALLBACK")
    elif row["price_qualified"] or row["capacity_proxy_verified"]:
        raise ValueError("OVERLAY_QUALIFICATION_WITHOUT_EVIDENCE")
    if row["capacity_proxy_verified"]:
        _require(base_labels._finite(row.get("capacity_amount"), positive=True) == evidence.get("amount")
                 and row.get("capacity_evidence") == "CANONICAL_AMOUNT_ARITHMETIC_ONLY_NOT_ORDER_CAPACITY",
                 "OVERLAY_CAPACITY_QUALIFICATION_CHANGED")
    else:
        _require(row.get("capacity_amount") is None and row.get("capacity_evidence") == "UNKNOWN"
                 and (evidence is None or evidence.get("amount") is None), "OVERLAY_UNKNOWN_CAPACITY_SUPPLIED_AMOUNT")
    minute_dates = row.get("minute_source_observed_dates")
    _require(type(minute_dates) is list and minute_dates == sorted(set(minute_dates))
             and bool(minute_dates) == row["minute_source_observed"], "OVERLAY_MINUTE_DATES_CHANGED")
    status, returns = row.get("label_status"), [row.get(k) for k in ("net_return", "conditional_net_return", "slot_net_return")]
    _require(type(status) is str, "OVERLAY_STATUS_REQUIRED")
    if status == base_labels.SETTLED:
        numbers = [base_labels._finite(v) for v in returns]
        exit_evidence = row.get("exit_evidence")
        _require(type(row.get("proxy_fill")) is int and row["proxy_fill"] == 1 and evidence is not None
                 and evidence["status"] in {"CANONICAL_PRICE_OBSERVED", "DAILY_OPEN_PRICE_PROXY_CAPACITY_UNKNOWN"}
                 and isinstance(exit_evidence, Mapping) and len(set(numbers)) == 1
                 and math.isclose(numbers[0], base_labels._finite(exit_evidence.get("gross_return")) - .0045, abs_tol=1e-12, rel_tol=0)
                 and row.get("actual_exit_date") in minute_dates
                 and row.get("label_available_date") == row.get("actual_exit_date")
                 and row.get("label_available_at") == base_labels._at(row["actual_exit_date"], "15:00:00")
                 and row.get("label_maturity_at") == row.get("actual_exit_time") == exit_evidence.get("actual_exit_time"),
                 "OVERLAY_SETTLEMENT_CONTRACT_CHANGED")
    elif status in policy_v3.NO_FILL_STATUSES:
        _require(type(row.get("proxy_fill")) is int and row["proxy_fill"] == 0 and returns[:2] == [None, None]
                 and type(returns[2]) in (float, int) and returns[2] == 0 and not minute_dates and evidence is not None
                 and evidence["status"] in {"CANONICAL_PRICE_OBSERVED", "DAILY_OPEN_PRICE_PROXY_CAPACITY_UNKNOWN", "OBSERVED_NO_AUCTION_TRADE"}
                 and row.get("label_available_date") == row["exec_date"]
                 and row.get("label_available_at") == base_labels._at(row["exec_date"], "15:00:00")
                 and row.get("label_maturity_at") == base_labels._at(row["exec_date"], "09:25:00"), "OVERLAY_NO_FILL_CONTRACT_CHANGED")
        _require(row.get("t_daily_volume") != 0 or row.get("t_daily_ohlc_flat_at_cent") is True, "OVERLAY_ZERO_VOLUME_NONFLAT_NO_FILL")
        if status == "NO_FILL_CAPACITY":
            _require(row["capacity_proxy_verified"] and row["capacity_amount"] * .01 + 1e-9 < 100_000, "OVERLAY_UNKNOWN_CAPACITY_NO_FILL")
        elif status == "NO_FILL_CANONICAL_AUCTION_ZERO_VOLUME":
            _require(evidence["status"] == "OBSERVED_NO_AUCTION_TRADE" and row.get("auction_trade_observed") is False
                     and not row["capacity_proxy_verified"], "OVERLAY_NO_AUCTION_TRADE_UNPROVEN")
        elif status == "NO_FILL_SUSPENDED":
            _require(row.get("t_daily_volume") == 0, "OVERLAY_SUSPENSION_UNPROVEN")
        else:
            _require(row.get("t_opening_limit_up_observed") is True, "OVERLAY_OPENING_LIMIT_UNPROVEN")
    else:
        _require(status.startswith("PENDING_") and returns == [None, None, None]
                 and (row.get("proxy_fill") is None or type(row["proxy_fill"]) is int and row["proxy_fill"] == 1),
                 "OVERLAY_PENDING_CANNOT_BECOME_ZERO_OR_TERMINAL")
    return deepcopy(CONTRACT)


def build_labels(base_root, manifest, *, as_of_date, candidate_source_root, verified_scope,
                 verified_market_scope=None, market_source_root=None, prior_label_report=None):
    """Read-only full-cohort replay under a verifier-issued immutable authority."""
    _guard()
    authority = _authority(verified_scope)
    root, overlay_root = _root(base_root), _root(candidate_source_root)
    _require(root != overlay_root and overlay_root == _root(authority.root), "CANDIDATE_SOURCE_ROOT_MISMATCH")
    _require(as_of_date == authority.as_of_date, "VERIFIED_AS_OF_DATE_CHANGED")
    _require(_digest(manifest) == authority.frozen_manifest_sha256, "FROZEN_CANDIDATE_MANIFEST_CHANGED")
    market, market_root, prior, minute_files = None, None, None, {}
    optional = (verified_market_scope, market_source_root, prior_label_report)
    _require(all(v is None for v in optional) or all(v is not None for v in optional), "COMPLETE_MINUTE_ADMISSION_ARGUMENTS_REQUIRED")
    if verified_market_scope is not None:
        market, market_root, prior, minute_files = _market_admission(
            root, overlay_root, authority, manifest, as_of_date, verified_market_scope=verified_market_scope,
            market_source_root=market_source_root, prior_label_report=prior_label_report)
    inventory = [dict(b) for b in authority.base_file_bindings] + [
        {"path": path, "sha256": digest} for path, digest in sorted(minute_files.items())]
    base_files = _base_inventory(root, inventory)
    gaps, successes = set(authority.gap_pairs), set(authority.successful_pairs)
    _require(len(gaps) == len(authority.gap_pairs) and successes <= gaps, "VERIFIED_PAIR_SCOPE_CHANGED")
    candidate_bindings = {(b["path"], b["sha256"]) for b in authority.source_bindings}
    baseline = base_labels.build_labels(root, manifest, as_of_date=as_of_date)
    actual_pairs = {(r["exec_date"], r["ts_code"]) for r in baseline["rows"]}
    _require(gaps <= actual_pairs, "OVERLAY_PAIR_OUTSIDE_FROZEN_UNIVERSE")
    dates, sources, updated = base_labels.settlement._strict_open_dates(root), {}, []
    if prior is not None:
        prior_by_id = {(r["signal_date"], r["ts_code"]): r for r in prior["rows"]}
        _require(all(pair[0] in dates for pair in market.gap_pairs),
                 "REGISTERED_MINUTE_DAY_NOT_IN_STRICT_CALENDAR")
        identity_fields = ("signal_date", "ts_code", "stage_transition", "promotion_rank", "exec_date", "scheduled_exit_date",
                           "feature_as_of_date", "feature_available_at", "features", "shadow_max_price")
        for seed in baseline["rows"]:
            old = prior_by_id[(seed["signal_date"], seed["ts_code"])]
            _require(_digest({k: seed.get(k) for k in identity_fields}) == _digest({k: old.get(k) for k in identity_fields}),
                     "PRIOR_FROZEN_FEATURES_RANKS_OR_DATES_CHANGED")
    def bind(origin, item):
        binding = {key: item[key] for key in ("path", "sha256")}
        if origin == "base" and binding["path"] in minute_files:
            origin = "minute_overlay"
        if origin == "base":
            _require(base_files.get(binding["path"]) == binding["sha256"], "UNBOUND_BASE_TRUTH")
            base_labels._binding(root, binding)
        elif origin == "minute_overlay":
            _require(market is not None and minute_files.get(binding["path"]) == binding["sha256"], "UNBOUND_MINUTE_OVERLAY_TRUTH")
            base_labels._binding(root, binding)
            base_labels._binding(market_root, binding)
        else:
            _require(origin == "candidate" and (binding["path"], binding["sha256"]) in candidate_bindings,
                     "UNBOUND_CANDIDATE_TRUTH")
            base_labels._binding(overlay_root, binding)
        key = (origin, binding["path"])
        _require(key not in sources or sources[key]["sha256"] == binding["sha256"], "SOURCE_CHANGED_DURING_OVERLAY")
        sources[key] = {"origin": origin, **binding}

    for binding in baseline["source_files"]:
        bind("base", binding)
    output = []
    for seed in baseline["rows"]:
        pair = (seed["exec_date"], seed["ts_code"])
        if pair not in gaps or pair not in successes:
            output.append(deepcopy(seed))
            continue
        _require(seed["label_status"] == "PENDING_T_MISSING_CANONICAL_AUCTION", "OVERLAY_CANNOT_REPLACE_EXISTING_ENTRY_OR_OUTCOME")
        loaded = candidate_source.load(overlay_root, *pair)
        for binding in loaded.source_files:
            bind("candidate", binding)
        output.append(_resume(root, seed, loaded, dates, as_of_date, bind))
        updated.append(list(pair))
    cohorts = {}
    for day in sorted(manifest["expected_candidate_codes"]):
        rows = [r for r in output if r["signal_date"] == day]
        complete = all(r["label_status"] == base_labels.SETTLED or r["label_status"] in policy_v3.NO_FILL_STATUSES for r in rows)
        for row in rows:
            row["cohort_complete"] = complete
            validate_label_contract(row)
        cohorts[day] = {"expected_rows": len(rows), "terminal_rows": sum(r["label_status"] == base_labels.SETTLED or r["label_status"] in policy_v3.NO_FILL_STATUSES for r in rows),
                        "complete": complete, "statuses": dict(Counter(r["label_status"] for r in rows)),
                        "label_available_date": max(r["label_available_date"] for r in rows) if complete else None}
    if prior is not None:
        for row in output:
            old = prior_by_id[(row["signal_date"], row["ts_code"])]
            if old["label_status"] == base_labels.SETTLED or old["label_status"] in policy_v3.NO_FILL_STATUSES:
                _require(_digest({k: v for k, v in old.items() if k != "cohort_complete"})
                         == _digest({k: v for k, v in row.items() if k != "cohort_complete"}), "PRIOR_TERMINAL_ECONOMICS_OR_IDENTITY_CHANGED")
    for item in tuple(sources.values()):
        bind(item["origin"], item)
    _base_inventory(root, inventory)
    if market is not None:
        _require(_digest(_prior_report(prior_label_report, market.label_report_sha256)) == _digest(prior), "PRIOR_LABEL_CHANGED_DURING_REPLAY")
        market.assert_unchanged()
    authority.assert_unchanged()
    _guard()
    return {"schema_version": LABEL_SCHEMA, "source_overlay_contract": deepcopy(CONTRACT),
            "candidate_manifest_sha256": _digest(manifest), "base_archive_sha256": authority.base_archive_sha256,
            "candidate_collection_receipt_sha256": authority.receipt_sha256,
            "market_collection_receipt_sha256": market.receipt_sha256 if market is not None else None,
            "market_registered_plan_sha256": market.plan_sha256 if market is not None else None,
            "market_registered_label_report_sha256": market.label_report_sha256 if market is not None else None,
            "baseline_label_content_sha256": _digest(baseline), "as_of_date": as_of_date,
            "rows": output, "cohorts_by_date": cohorts, "source_files": [sources[k] for k in sorted(sources)],
            "overlay_consumed_pairs": sorted(updated), "unresolved_source_pairs": [list(p) for p in sorted(gaps - successes)],
            "research_only": True, "historical_counterfactual": True, "feature_evidence_kind": manifest["evidence_kind"],
            "feature_columns": manifest["feature_columns"], "round_trip_cost_rate": .0045,
            "entry_policy_id": policy_v3.ENTRY_POLICY_ID, "label_policy_id": base_labels.EXIT_POLICY_ID,
            "old_open_exit_labels_consumed": False, "natural_forward_ledger_rewritten": False,
            "source_only_metadata_rewritten": False, "training_performed": False,
            "basis": policy_v3.BASIS, "known_before_0925": False, "price_reporting_precision_confirmed": False,
            "reported_price_preserved": True, "profitability_improvement_proven": False,
            "actual_execution_claimed": False, "actual_capacity_verified": False,
            "production_activation_allowed": False, "files_written": 0}
