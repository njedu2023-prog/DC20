"""Read-only, explicitly chained minute admissions for frozen v3 economics.

The caller stages a fresh augmented base containing exactly the original base
files plus successful minute pairs. Each real collection retains its separate
private verifier authority and run identity; no merged authority is invented.
This module neither writes files nor qualifies missing daily/suspension data.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
_CHECKOUT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(_CHECKOUT), str(_CHECKOUT / "src")]
from work.profit_1000_upgrade import candidate_labels_overlay as overlay
from work.profit_1000_upgrade import candidate_scope_verify, minute_gap_verify

base_labels, policy, minute_truth = overlay.base_labels, overlay.policy_v3, overlay.minute_truth
CHAIN_SCHEMA = "dc20_verified_minute_source_chain_20260913_v1"
CHAIN_KIND = "COMPOSITE_SOURCE_CHAIN_DIGEST"
MAX_ROUNDS, EXPECTED_ROWS, EXPECTED_DATES = 8, 6753, 910
AS_OF_DATE = "20260911"
MIN_SIGNAL_DATE, MAX_SIGNAL_DATE = "20221111", "20260814"
_PINNED = {
    "work/profit_1000_upgrade/candidate_labels_overlay.py": "1c1aa1acf316e46fd0c86ed7f7310bf36474fc8bff47dcbcf8b9678ac68cc5f3",
    "work/profit_1000_upgrade/minute_gap_verify.py": "4028d78eedf343f1b261d5be562751e3464bf5c7a0ffa218223cebcb463a2b92",
    "work/profit_1000_upgrade/candidate_scope_verify.py": "fa8d8ad5956680435693cd9000954566ae8ffb1273454706450042a4e777c905",
}
_SELF_SHA = minute_gap_verify.file_sha(__file__)
_IDENTITY_FIELDS = ("signal_date", "ts_code", "stage_transition", "promotion_rank", "exec_date", "scheduled_exit_date",
                    "feature_as_of_date", "feature_available_at", "features", "shadow_max_price")
_SINGLE_FIELDS = ("market_collection_receipt_sha256", "market_registered_plan_sha256", "market_registered_label_report_sha256")
_CHAIN_FIELDS = ("market_collection_receipt_kind", "market_source_chain_document", "market_source_chain_sha256")


def require(ok, message):
    if not ok: raise ValueError(message)


def canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def equal(a, b, reason):
    require(canonical(a) == canonical(b), reason)


def _guard():
    bindings = {**_PINNED, "work/profit_1000_upgrade/candidate_market_chain.py": _SELF_SHA}
    for path, sha in bindings.items():
        require(minute_gap_verify.file_sha(_CHECKOUT / path) == sha, "MINUTE_CHAIN_IMPLEMENTATION_CHANGED")
    overlay._guard()
    return bindings


def _candidate_authority(value):
    require(type(value) is candidate_scope_verify.VerifiedCandidateCollection, "EXACT_CANDIDATE_AUTHORITY_REQUIRED")
    value.assert_unchanged()
    return value


def _minute_authority(value):
    require(type(value) is minute_gap_verify.VerifiedMinuteCollection, "EXACT_MINUTE_AUTHORITY_REQUIRED")
    value.assert_unchanged()
    return value


def _universe(manifest, candidate, as_of):
    require(as_of == AS_OF_DATE == candidate.as_of_date, "FROZEN_AS_OF_CHANGED")
    # Do not serialize/copy features until every identity is known to belong
    # to the frozen development universe. Future outcomes must not be read.
    require(type(manifest) is dict, "FROZEN_MANIFEST_CHANGED")
    codes = manifest.get("expected_candidate_codes")
    require(type(codes) is dict and len(codes) == EXPECTED_DATES
            and all(type(day) is str and MIN_SIGNAL_DATE <= day <= MAX_SIGNAL_DATE and type(names) is list
                    and names and all(type(name) is str for name in names)
                    and len(names) == len(set(names)) for day, names in codes.items()), "FROZEN_DATE_UNIVERSE_CHANGED")
    members = {(day, code) for day, names in codes.items() for code in names}
    for day, code in members: minute_truth._identity(day, code)
    require(len(members) == EXPECTED_ROWS and type(manifest.get("rows")) is list
            and len(manifest["rows"]) == EXPECTED_ROWS, "FULL_FROZEN_CANDIDATE_COUNT_REQUIRED")
    identities = []
    for row in manifest["rows"]:
        require(type(row) is dict, "FROZEN_CANDIDATE_IDENTITIES_CHANGED")
        day, code = row.get("signal_date"), row.get("ts_code")
        require(type(day) is str and type(code) is str and (day, code) in members,
                "FROZEN_CANDIDATE_IDENTITIES_CHANGED")
        identities.append((day, code))
    require(len(identities) == len(set(identities)) == EXPECTED_ROWS and set(identities) == members, "FROZEN_CANDIDATE_IDENTITIES_CHANGED")
    require(overlay._digest(manifest) == candidate.frozen_manifest_sha256, "FROZEN_MANIFEST_CHANGED")
    base_labels._source_policy(manifest)
    return members


def _full_inventory(root, bindings):
    inventory = overlay._base_inventory(root, bindings)
    allowed_dirs = {p for name in inventory for p in (root / name).parents if root in p.parents}
    folded = set()
    for path in root.rglob("*"):
        name = path.relative_to(root).as_posix().casefold()
        require(name not in folded, "AUGMENTED_CASE_ALIAS_FORBIDDEN")
        folded.add(name)
        if path.is_dir(): require(path in allowed_dirs, "AUGMENTED_EXTRA_DIRECTORY_FORBIDDEN")
    return inventory


def _round(scope, ordinal):
    for value in (scope.receipt_sha256, scope.plan_sha256, scope.label_report_sha256): minute_gap_verify.sha(value)
    require(type(scope.run_id) is str and re.fullmatch(r"[0-9]{1,20}", scope.run_id)
            and type(scope.run_commit) is str and re.fullmatch(r"[0-9a-f]{40}", scope.run_commit), "MINUTE_RUN_IDENTITY_REQUIRED")
    require(scope.source_only is True and scope.label_source_eligible is False, "MINUTE_SOURCE_ONLY_AUTHORITY_REQUIRED")
    return {"ordinal": ordinal, "run_id": scope.run_id, "run_commit": scope.run_commit,
            "collection_receipt_sha256": scope.receipt_sha256, "registered_plan_sha256": scope.plan_sha256,
            "prior_label_report_sha256": scope.label_report_sha256, "collection_status": scope.status,
            "registered_pairs": [list(p) for p in scope.gap_pairs],
            "successful_pairs": [list(p) for p in scope.successful_pairs],
            "source_bindings": [dict(b) for b in scope.source_bindings]}


def _document(candidate, manifest, rounds):
    return {"schema_version": CHAIN_SCHEMA, "digest_kind": CHAIN_KIND,
            "as_of_date": AS_OF_DATE, "base_archive_sha256": candidate.base_archive_sha256,
            "candidate_collection_receipt_sha256": candidate.receipt_sha256,
            "candidate_manifest_sha256": overlay._digest(manifest), "source_overlay_contract": deepcopy(overlay.CONTRACT),
            "rounds": deepcopy(rounds), "round_count": len(rounds),
            "single_github_collection_claimed": False, "original_sources_rewritten": False,
            "source_values_modified": False, "training_performed": False,
            "production_activation_allowed": False, "nontrading_source_admission": False}


def _prefix(prior, candidate, manifest, rounds):
    require(all(key in prior for key in _SINGLE_FIELDS), "PRIOR_MARKET_LINK_FIELDS_REQUIRED")
    if not rounds:
        require(all(prior.get(k) is None for k in _SINGLE_FIELDS) and not any(k in prior for k in _CHAIN_FIELDS), "FIRST_PRIOR_ALREADY_HAS_MINUTE_ADMISSION")
    elif len(rounds) == 1:
        previous = rounds[0]
        equal([prior.get(k) for k in _SINGLE_FIELDS], [previous["collection_receipt_sha256"], previous["registered_plan_sha256"], previous["prior_label_report_sha256"]], "SINGLE_ROUND_PRIOR_BINDING_CHANGED")
        require(not any(k in prior for k in _CHAIN_FIELDS), "SINGLE_ROUND_PRIOR_CANNOT_CLAIM_COMPOSITE")
    else:
        document = _document(candidate, manifest, rounds)
        equal(prior.get("market_source_chain_document"), document, "PRIOR_COMPOSITE_CHAIN_CHANGED")
        require(prior.get("market_collection_receipt_kind") == CHAIN_KIND
                and prior.get("market_source_chain_sha256") == prior.get("market_collection_receipt_sha256") == digest(document)
                and prior.get("market_registered_plan_sha256") is None
                and prior.get("market_registered_label_report_sha256") is None, "PRIOR_COMPOSITE_DIGEST_OR_KIND_CHANGED")


def _prior_contract(prior, manifest, candidate, members, allowed_sources):
    require("nontrading_resume" not in prior, "NONTRADING_REPORT_FORBIDDEN_IN_RAW_MINUTE_CHAIN")
    expected = {"schema_version": overlay.LABEL_SCHEMA, "source_overlay_contract": overlay.CONTRACT,
        "candidate_manifest_sha256": overlay._digest(manifest), "base_archive_sha256": candidate.base_archive_sha256,
        "candidate_collection_receipt_sha256": candidate.receipt_sha256, "as_of_date": AS_OF_DATE,
        "entry_policy_id": policy.ENTRY_POLICY_ID, "label_policy_id": base_labels.EXIT_POLICY_ID,
        "round_trip_cost_rate": .0045, "feature_evidence_kind": manifest["evidence_kind"],
        "feature_columns": manifest["feature_columns"], "research_only": True, "historical_counterfactual": True,
        "training_performed": False, "production_activation_allowed": False, "actual_execution_claimed": False,
        "source_only_metadata_rewritten": False, "files_written": 0}
    for key, expected_value in expected.items(): equal(prior.get(key), expected_value, "PRIOR_CONTRACT_OR_IDENTITY_CHANGED")
    rows = prior.get("rows")
    require(type(rows) is list and len(rows) == EXPECTED_ROWS and all(type(row) is dict for row in rows), "PRIOR_FULL_ROWS_REQUIRED")
    keys = [(r.get("signal_date"), r.get("ts_code")) for r in rows]
    require(len(set(keys)) == len(keys) and set(keys) == members, "PRIOR_CANDIDATE_UNIVERSE_CHANGED")
    missing = set()
    for row in rows:
        evidence = row.get("exit_evidence")
        require("nontrading_exit_supplement" not in row
                and not (isinstance(evidence, dict) and "nontrading_adapter_id" in evidence),
                "NONTRADING_REPORT_FORBIDDEN_IN_RAW_MINUTE_CHAIN")
        overlay.validate_label_contract(row)
        if row["label_status"] == "PENDING_EXIT_MISSING_MINUTES":
            require(row.get("missing_evidence_kind") == "research_exit_1000_1m_0931"
                    and row.get("missing_evidence_code") == row["ts_code"], "PRIOR_MISSING_KIND_OR_CODE_CHANGED")
            pair = (row.get("missing_evidence_date"), row["ts_code"])
            minute_truth._identity(*pair)
            require(row["scheduled_exit_date"] <= pair[0] <= AS_OF_DATE, "PRIOR_MISSING_DATE_OUTSIDE_HOLDING_WINDOW")
            missing.add(pair)
    sources = prior.get("source_files")
    require(type(sources) is list and sources, "PRIOR_SOURCE_BINDINGS_REQUIRED")
    seen = set()
    for item in sources:
        require(type(item) is dict and set(item) == {"origin", "path", "sha256"}, "PRIOR_SOURCE_BINDING_FIELDS_CHANGED")
        key = (item["origin"], item["path"])
        require(key not in seen and allowed_sources.get(key) == item["sha256"], "PRIOR_SOURCE_NOT_IN_PREFIX_AUTHORITIES")
        seen.add(key)
    return missing, {key: row for key, row in zip(keys, rows)}


def _admit(root, candidate_root, candidate, manifest, members, scopes, paths):
    roots = [root, candidate_root] + [overlay._root(scope.root) for scope in scopes]
    require(all(a != b and a not in b.parents and b not in a.parents for i, a in enumerate(roots) for b in roots[i + 1:]), "SOURCE_ROOTS_MUST_BE_DISJOINT")
    base_files = {b["path"]: b["sha256"] for b in candidate.base_file_bindings}
    allowed_sources = {("base", b["path"]): b["sha256"] for b in candidate.base_file_bindings}
    allowed_sources.update({("candidate", b["path"]): b["sha256"] for b in candidate.source_bindings})
    owners, rounds, priors, attempted = {}, [], [], set()
    calendar = set(base_labels.settlement._strict_open_dates(root))
    for index, (scope, path) in enumerate(zip(scopes, paths)):
        require(scope.as_of_date == AS_OF_DATE, "MINUTE_CHAIN_AS_OF_CHANGED")
        require(scope.status in {"MINUTE_GAPS_COLLECTED", "MINUTE_GAPS_PARTIAL", "PREFLIGHT_BLOCKED"}, "UNKNOWN_MINUTE_COLLECTION_STATUS")
        if index < len(scopes) - 1:
            require(scope.status == "MINUTE_GAPS_COLLECTED" and scope.successful_pairs == scope.gap_pairs, "INCOMPLETE_PREVIOUS_COLLECTION_CANNOT_START_NEXT_ROUND")
        prior = overlay._prior_report(path, scope.label_report_sha256)
        _prefix(prior, candidate, manifest, rounds)
        missing, rows = _prior_contract(prior, manifest, candidate, members, allowed_sources)
        pairs, success = tuple(scope.gap_pairs), tuple(scope.successful_pairs)
        require(pairs and pairs == tuple(sorted(set(pairs))) and set(pairs) == missing
                and success == tuple(sorted(set(success))) and set(success) <= missing, "REGISTERED_PAIRS_NOT_EXACT_PRIOR_GAPS")
        require(all(pair[0] in calendar for pair in pairs), "REGISTERED_MINUTE_NOT_IN_STRICT_CALENDAR")
        require(not attempted.intersection(pairs), "PRIOR_REGISTERED_PAIR_RETRY_FORBIDDEN")
        attempted.update(pairs)
        bindings = {(b["path"], b["sha256"]) for b in scope.source_bindings}
        require(len(bindings) == len(scope.source_bindings), "DUPLICATE_MINUTE_BINDING")
        expected = set()
        for pair in pairs:
            names = [p.relative_to(root).as_posix() for p in minute_truth.paths(root, *pair)]
            require(not any(name in base_files or name in owners for name in names), "MINUTE_PAIR_CANNOT_REPLACE_BASE_OR_PRIOR_ORPHAN")
            if pair not in success: continue
            original = overlay._minute_payload(roots[index + 2], pair)
            copied = overlay._minute_payload(root, pair)
            equal(original, copied, "AUGMENTED_MINUTE_SOURCE_DIFFERS")
            for item in original["source_files"]:
                require((item["path"], item["sha256"]) in bindings, "MINUTE_PAIR_UNBOUND")
                for source_root in (roots[index + 2], root):
                    file, _ = base_labels._binding(source_root, item)
                    require(file.stat().st_nlink == 1, "MINUTE_SOURCE_HARDLINK_FORBIDDEN")
                owners[item["path"]] = (index, item["sha256"], roots[index + 2])
                allowed_sources[("minute_overlay", item["path"])] = item["sha256"]
                expected.add((item["path"], item["sha256"]))
        require(bindings == expected, "MINUTE_BINDINGS_NOT_EXACT_SUCCESS_PAIRS")
        rounds.append(_round(scope, index + 1)); priors.append((prior, rows))
    return owners, rounds, priors


def _replay(root, candidate_root, candidate, manifest, owners, priors):
    """Mechanical source-routing counterpart of frozen overlay.build_labels."""
    inventory = [dict(b) for b in candidate.base_file_bindings] + [{"path": path, "sha256": value[1]} for path, value in sorted(owners.items())]
    base_files = _full_inventory(root, inventory)
    gaps, successes = set(candidate.gap_pairs), set(candidate.successful_pairs)
    require(len(gaps) == len(candidate.gap_pairs) and successes <= gaps, "CANDIDATE_SCOPE_CHANGED")
    candidate_bindings = {(b["path"], b["sha256"]) for b in candidate.source_bindings}
    baseline = base_labels.build_labels(root, manifest, as_of_date=AS_OF_DATE)
    require(len(baseline["rows"]) == EXPECTED_ROWS, "BASE_REPLAY_FULL_UNIVERSE_REQUIRED")
    actual_pairs = {(r["exec_date"], r["ts_code"]) for r in baseline["rows"]}
    require(gaps <= actual_pairs, "CANDIDATE_SCOPE_OUTSIDE_FROZEN_UNIVERSE")
    dates = base_labels.settlement._strict_open_dates(root)
    for prior, prior_rows in priors:
        for seed in baseline["rows"]:
            old = prior_rows[(seed["signal_date"], seed["ts_code"])]
            equal({k: seed.get(k) for k in _IDENTITY_FIELDS}, {k: old.get(k) for k in _IDENTITY_FIELDS}, "PRIOR_FROZEN_FEATURES_OR_DATES_CHANGED")
    for path in owners:
        require(path.split("/")[-2] in dates, "REGISTERED_MINUTE_NOT_IN_STRICT_CALENDAR")
    sources, output, updated = {}, [], []
    def bind(origin, item):
        binding = {k: item[k] for k in ("path", "sha256")}
        if origin == "base" and binding["path"] in owners: origin = "minute_overlay"
        if origin == "base":
            require(base_files.get(binding["path"]) == binding["sha256"], "UNBOUND_BASE_TRUTH")
            base_labels._binding(root, binding)
        elif origin == "minute_overlay":
            require(binding["path"] in owners and owners[binding["path"]][1] == binding["sha256"], "UNBOUND_CHAIN_MINUTE_TRUTH")
            base_labels._binding(root, binding); base_labels._binding(owners[binding["path"]][2], binding)
        else:
            require(origin == "candidate" and (binding["path"], binding["sha256"]) in candidate_bindings, "UNBOUND_CANDIDATE_TRUTH")
            base_labels._binding(candidate_root, binding)
        key = (origin, binding["path"])
        require(key not in sources or sources[key]["sha256"] == binding["sha256"], "SOURCE_CHANGED_DURING_REPLAY")
        sources[key] = {"origin": origin, **binding}
    for item in baseline["source_files"]: bind("base", item)
    for seed in baseline["rows"]:
        pair = (seed["exec_date"], seed["ts_code"])
        if pair not in gaps or pair not in successes:
            output.append(deepcopy(seed)); continue
        require(seed["label_status"] == "PENDING_T_MISSING_CANONICAL_AUCTION", "CANDIDATE_CANNOT_REPLACE_EXISTING_ENTRY")
        loaded = overlay.candidate_source.load(candidate_root, *pair)
        for item in loaded.source_files: bind("candidate", item)
        output.append(overlay._resume(root, seed, loaded, dates, AS_OF_DATE, bind)); updated.append(list(pair))
    cohorts = {}
    for day in sorted(manifest["expected_candidate_codes"]):
        rows = [r for r in output if r["signal_date"] == day]
        complete = all(r["label_status"] == base_labels.SETTLED or r["label_status"] in policy.NO_FILL_STATUSES for r in rows)
        for row in rows: row["cohort_complete"] = complete; overlay.validate_label_contract(row)
        cohorts[day] = {"expected_rows": len(rows), "terminal_rows": sum(r["label_status"] == base_labels.SETTLED or r["label_status"] in policy.NO_FILL_STATUSES for r in rows),
                        "complete": complete, "statuses": dict(Counter(r["label_status"] for r in rows)),
                        "label_available_date": max(r["label_available_date"] for r in rows) if complete else None}
    for prior, prior_rows in priors:
        for row in output:
            old = prior_rows[(row["signal_date"], row["ts_code"])]
            if old["label_status"] == base_labels.SETTLED or old["label_status"] in policy.NO_FILL_STATUSES:
                equal({k: v for k, v in old.items() if k != "cohort_complete"}, {k: v for k, v in row.items() if k != "cohort_complete"}, "PRIOR_TERMINAL_ECONOMICS_OR_IDENTITY_CHANGED")
    for item in tuple(sources.values()): bind(item["origin"], item)
    _full_inventory(root, inventory)
    return {"schema_version": overlay.LABEL_SCHEMA, "source_overlay_contract": deepcopy(overlay.CONTRACT),
        "candidate_manifest_sha256": overlay._digest(manifest), "base_archive_sha256": candidate.base_archive_sha256,
        "candidate_collection_receipt_sha256": candidate.receipt_sha256,
        "baseline_label_content_sha256": overlay._digest(baseline), "as_of_date": AS_OF_DATE,
        "rows": output, "cohorts_by_date": cohorts, "source_files": [sources[k] for k in sorted(sources)],
        "overlay_consumed_pairs": sorted(updated), "unresolved_source_pairs": [list(p) for p in sorted(gaps - successes)],
        "research_only": True, "historical_counterfactual": True, "feature_evidence_kind": manifest["evidence_kind"],
        "feature_columns": manifest["feature_columns"], "round_trip_cost_rate": .0045,
        "entry_policy_id": policy.ENTRY_POLICY_ID, "label_policy_id": base_labels.EXIT_POLICY_ID,
        "old_open_exit_labels_consumed": False, "natural_forward_ledger_rewritten": False,
        "source_only_metadata_rewritten": False, "training_performed": False,
        "basis": policy.BASIS, "known_before_0925": False, "price_reporting_precision_confirmed": False,
        "reported_price_preserved": True, "profitability_improvement_proven": False,
        "actual_execution_claimed": False, "actual_capacity_verified": False,
        "production_activation_allowed": False, "files_written": 0}


def build_labels(base_root, manifest, *, as_of_date, candidate_source_root, verified_candidate_scope,
                 verified_minute_scopes, prior_label_reports):
    code = _guard()
    require(type(verified_minute_scopes) in (list, tuple) and type(prior_label_reports) in (list, tuple)
            and len(verified_minute_scopes) == len(prior_label_reports) <= MAX_ROUNDS, "EXACT_BOUNDED_MINUTE_CHAIN_ARGUMENTS_REQUIRED")
    candidate = _candidate_authority(verified_candidate_scope)
    members = _universe(manifest, candidate, as_of_date)
    caller_manifest = manifest
    manifest = deepcopy(caller_manifest)
    # Replay only a private, fully bound snapshot, while checking the caller's
    # original again at both exit boundaries (including after the final guard).
    require(_universe(manifest, candidate, as_of_date) == members, "FROZEN_MANIFEST_CHANGED")
    def guard_manifests():
        for value in (caller_manifest, manifest):
            require(_universe(value, candidate, as_of_date) == members, "FROZEN_MANIFEST_CHANGED")
    root, candidate_root = overlay._root(base_root), overlay._root(candidate_source_root)
    require(candidate_root == overlay._root(candidate.root) and root != candidate_root
            and root not in candidate_root.parents and candidate_root not in root.parents, "CANDIDATE_ROOT_CHANGED")
    scopes = [_minute_authority(scope) for scope in verified_minute_scopes]
    if scopes:
        owners, rounds, priors = _admit(root, candidate_root, candidate, manifest, members, scopes, prior_label_reports)
        _full_inventory(root, [dict(b) for b in candidate.base_file_bindings] + [
            {"path": path, "sha256": value[1]} for path, value in sorted(owners.items())])
    else:
        _full_inventory(root, [dict(b) for b in candidate.base_file_bindings])
    if len(scopes) <= 1:
        arguments = {} if not scopes else {"verified_market_scope": scopes[0], "market_source_root": scopes[0].root,
                                           "prior_label_report": prior_label_reports[0]}
        result = overlay.build_labels(root, manifest, as_of_date=as_of_date, candidate_source_root=candidate_root,
                                      verified_scope=candidate, **arguments)
        guard_manifests()
        require(_guard() == code, "MINUTE_CHAIN_CODE_CHANGED_DURING_REPLAY")
        guard_manifests()
        return result
    result = _replay(root, candidate_root, candidate, manifest, owners, priors)
    guard_manifests()
    document = _document(candidate, manifest, rounds)
    result.update(market_collection_receipt_kind=CHAIN_KIND, market_source_chain_document=document,
                  market_source_chain_sha256=digest(document), market_collection_receipt_sha256=digest(document),
                  market_registered_plan_sha256=None, market_registered_label_report_sha256=None)
    for scope, path, (prior, _) in zip(scopes, prior_label_reports, priors):
        equal(overlay._prior_report(path, scope.label_report_sha256), prior, "PRIOR_CHANGED_DURING_CHAIN_REPLAY")
        scope.assert_unchanged()
    candidate.assert_unchanged()
    _full_inventory(root, [dict(b) for b in candidate.base_file_bindings] + [
        {"path": path, "sha256": value[1]} for path, value in sorted(owners.items())])
    require(_guard() == code, "MINUTE_CHAIN_CODE_CHANGED_DURING_REPLAY")
    guard_manifests()
    return result
