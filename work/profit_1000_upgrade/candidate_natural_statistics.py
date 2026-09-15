"""Read-only aggregation of bound natural research selections and append ledgers.

The publication proof admits the original selection, not its price outcomes.
This module validates ledger bytes/contracts; the caller must independently
verify their acquisition/publication bindings. It issues no source authority.
"""
from __future__ import annotations

from collections import Counter
import csv
import hashlib
import importlib
import io
import math
from pathlib import Path

ROOT = Path(__file__).absolute().parents[2]
SCHEMA = "dc20_candidate_natural_bound_statistics_v1"
OUTCOMES_SHA = "5949be11309eebba1a3d5f45be9b56d4469b1d6e51f2c416960c9920699d7c18"
PUBLICATION_MODULE = "work.profit_1000_upgrade.candidate_natural_evidence_publication"
PUBLICATION_SHA = "ddfcd8932032d9e64c2be3577d60b74bc6d67f070d77727b79e83bced8c20bbb"
GROUPS = ("candidate_top1", "candidate_top2", "promotion_top1", "promotion_top2")
DAY_FIELDS = frozenset({"signal_date", "snapshot_raw", "expected_snapshot_sha256",
    "ledger_raw", "expected_ledger_sha256", "ledger_as_of_date", "publication_proof",
    "source_collection_receipt_sha256"})
FLAGS = {"research_only": True, "production_activation_allowed": False,
    "source_authority_issued": False, "natural_forward_admission_issued": False,
    "outcome_sources_independently_verified": False, "actual_execution_claimed": False,
    "actual_capacity_verified": False, "capital_nav_claimed": False,
    "drawdown_verified": False, "fees_subtracted_again": False,
    "model_training_performed": False, "prediction_scores_recalculated": False,
    "formal_ledger_written": False, "front_end_replaced": False,
    "profitability_improvement_proven": False, "replacement_gate": "NOT_CONFIGURED"}


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


_outcome_file = ROOT / "work/profit_1000_upgrade/candidate_natural_outcomes.py"
require(not any(p.is_symlink() for p in (_outcome_file, *_outcome_file.parents))
    and sha(_outcome_file.read_bytes()) == OUTCOMES_SHA, "FROZEN_OUTCOME_WRITER_CHANGED")
from work.profit_1000_upgrade import candidate_natural_outcomes as outcomes

natural, labels = outcomes.natural, outcomes.labels
SELF_SHA = sha(natural._read(Path(__file__).absolute())[0])


def _guard():
    require(Path(outcomes.__file__).absolute() == _outcome_file
        and sha(natural._read(_outcome_file)[0]) == OUTCOMES_SHA, "OUTCOME_IMPORT_OR_BYTES_CHANGED")
    body, identity = natural._read(Path(__file__).absolute())
    require(sha(body) == SELF_SHA, "STATISTICS_CODE_CHANGED")
    return identity, outcomes._guard()


def _publication_type():
    path = ROOT / (PUBLICATION_MODULE.replace(".", "/") + ".py")
    require(PUBLICATION_SHA != "0" * 64 and sha(natural._read(path)[0]) == PUBLICATION_SHA,
        "INDEPENDENT_PUBLICATION_ISSUER_NOT_REGISTERED_OR_CHANGED")
    module = importlib.import_module(PUBLICATION_MODULE)
    require(Path(module.__file__).absolute() == path, "PUBLICATION_IMPORT_ORIGIN_CHANGED")
    return module.VerifiedResearchPublication


def _proof(value, day, snapshot_sha):
    require(type(value) is _publication_type(), "EXACT_PRIVATE_ISSUED_PUBLICATION_PROOF_REQUIRED")
    value.assert_unchanged()
    require(value.signal_date == day and value.snapshot_file_sha256 == snapshot_sha,
        "PUBLICATION_PROOF_SNAPSHOT_IDENTITY_CHANGED")
    for field in ("publication_observation_sha256", "evidence_manifest_sha256"):
        natural.scorer._sha(getattr(value, field))
    require(type(value.observer_run_id) is int and value.observer_run_id > 0, "EXACT_OBSERVER_RUN_ID_REQUIRED")
    require(type(value.evidence_commit) is str and len(value.evidence_commit) == 40
        and all(c in "0123456789abcdef" for c in value.evidence_commit), "EXACT_EVIDENCE_COMMIT_REQUIRED")
    return {field: getattr(value, field) for field in ("signal_date", "snapshot_file_sha256",
        "publication_observation_sha256", "evidence_manifest_sha256", "observer_run_id", "evidence_commit")}


def _calendar(raw, expected):
    require(type(raw) is bytes and len(raw) <= 2 * 1024 * 1024, "BOUNDED_CALENDAR_BYTES_REQUIRED")
    require(expected == labels.settlement.CALENDAR_SHA256 and sha(raw) == expected,
        "PINNED_CALENDAR_BYTES_REQUIRED")
    rows = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    dates = sorted({natural.scorer._date(row["cal_date"], "calendar_date") for row in rows
        if str(row.get("exchange") or "").strip().upper() == "SSE"
        and str(row.get("is_open") or "").strip() == "1"})
    require(bool(dates), "EMPTY_CALENDAR")
    return dates


def _identities(day_inputs, asof, dates):
    """All external D/asof metadata precedes access to every raw outcome body."""
    require(type(day_inputs) in (list, tuple) and len(day_inputs) <= 4096, "BOUNDED_DAY_INPUTS_REQUIRED")
    days = []
    for item in day_inputs:
        require(type(item) is dict and set(item) == DAY_FIELDS, "EXACT_DAY_INPUT_FIELDS_REQUIRED")
        day = natural.scorer._date(item["signal_date"], "signal_date")
        require("20260914" <= day <= asof and day in dates, "FUTURE_OR_UNREGISTERED_SIGNAL_D")
        ledger_asof = item["ledger_as_of_date"]
        if ledger_asof is not None:
            natural.scorer._date(ledger_asof, "ledger_as_of_date")
            require(day <= ledger_asof <= asof and ledger_asof in dates, "FUTURE_OR_INVALID_LEDGER_ASOF_METADATA")
        days.append(day)
    require(days == sorted(set(days)), "EXACT_SORTED_UNIQUE_SIGNAL_DAYS_REQUIRED")
    return tuple(days)


def _same(value, expected, reason):
    natural.scorer._exact(value, expected, reason)


def _byte_budget(day_inputs):
    total = 0
    for item in day_inputs:
        for field in ("snapshot_raw", "ledger_raw"):
            raw = item[field]
            if field == "ledger_raw" and raw is None:
                continue
            require(type(raw) is bytes and len(raw) <= natural.MAX_BYTES, "BOUNDED_IMMUTABLE_INPUT_BYTES_REQUIRED")
            total += len(raw)
    require(total <= 128 * 1024 * 1024, "BOUNDED_TOTAL_STATISTICS_INPUTS_REQUIRED")


def _version_identity(version, frozen, dates, limit):
    require(type(version) is dict, "EXACT_OUTCOME_VERSION_REQUIRED")
    day = frozen["signal_date"]
    current = natural.scorer._date(version.get("as_of_date"), "version_as_of_date")
    require(day <= current <= limit and current in dates, "FUTURE_OR_INVALID_OUTCOME_VERSION")
    for field in ("signal_date", "exec_date", "exit_date"):
        require(version.get(field) == frozen[field], "OUTCOME_VERSION_IDENTITY_CHANGED")
    codes = sorted({s["ts_code"] for name in ("candidate_slots", "promotion_slots")
        for s in frozen[name] if s["ts_code"] is not None})
    require(type(version.get("native_singleton_reports")) is dict
        and set(version["native_singleton_reports"]) == set(codes), "EXACT_FOUR_SLOT_UNION_REPORTS_REQUIRED")
    require(type(version.get("native_singleton_manifests")) is dict
        and set(version["native_singleton_manifests"]) == set(codes), "EXACT_FOUR_SLOT_UNION_MANIFESTS_REQUIRED")
    for code, report in version["native_singleton_reports"].items():
        require(type(report) is dict and report.get("as_of_date") == current
            and type(report.get("rows")) is list and len(report["rows"]) == 1, "SINGLETON_REPORT_SHAPE_CHANGED")
        row = report["rows"][0]
        require(type(row) is dict and row.get("signal_date") == day and row.get("ts_code") == code
            and row.get("exec_date") == frozen["exec_date"]
            and row.get("scheduled_exit_date") == frozen["exit_date"], "NATIVE_ROW_IDENTITY_CHANGED")
        maturity = row.get("label_available_date")
        if maturity is not None:
            natural.scorer._date(maturity, "label_available_date")
            require(maturity in dates and frozen["exec_date"] <= maturity <= current,
                "FUTURE_OR_INVALID_LABEL_MATURITY")
    return current, codes


def _version(version, frozen, expected_snapshot, dates, now):
    outcomes._seal(version, "report_sha256")
    for field, expected in {"schema_version": outcomes.REPORT_SCHEMA,
        "snapshot_file_sha256": expected_snapshot, "snapshot_sha256": frozen["snapshot_sha256"],
        "registration_sha256": outcomes.REGISTRATION_SHA, "model_canonical_sha256": natural.MODEL_SHA,
        "model_evaluation_sha256": natural.EVALUATION_SHA, "model_feature_order": list(natural.scorer.FEATURES),
        "full_frozen_prediction": frozen["prediction"], "full_frozen_candidate_count": len(frozen["prediction"]["rows"]),
        "calendar_sha256": labels.settlement.CALENDAR_SHA256, "writer_sha256": OUTCOMES_SHA,
        "dependencies": outcomes.PINS, "clock_mode": "HOST_SYSTEM_UTC", "frozen_clock_mode": "HOST_SYSTEM_UTC",
        "original_snapshot_bytes_preserved": True, "round_trip_cost_rate": .0045,
        "shadow_notional_cny": 100000, "fees_subtracted_again": False,
        "native_report_scope": "ONE_STOCK_NOT_FULL_N_TRAINING_COHORT",
        "observation_kind": "POSTHOC_PRICE_PROXY_FOR_BOUND_PREBUY_RESEARCH_SELECTION_NOT_NATURAL_ADMISSION",
        "native_feature_projection_kind": "BOUND_PREBUY_FROZEN_SELECTION_STAGE_ONLY_PROJECTION", **outcomes.FLAGS}.items():
        _same(version.get(field), expected, "OUTCOME_CONTRACT_CHANGED:" + field)
    current = version["as_of_date"]
    observed = labels._timestamp(version["observed_at_utc"])
    require(labels._timestamp(labels._at(current, "15:00:00")) <= observed <= now,
        "OUTCOME_OBSERVATION_CLOCK_INVALID")
    codes = sorted(version["native_singleton_reports"])
    _same(version.get("evaluated_slot_union_codes"), codes, "OUTCOME_UNION_CODES_CHANGED")
    inputs, origins = version["input_bindings"], version["origin_bindings"]
    require(type(inputs) is list and type(origins) is list and len(inputs) <= 16384,
        "BOUNDED_SOURCE_BINDINGS_REQUIRED")
    source_map = {}
    for binding in inputs:
        require(type(binding) is dict and set(binding) == {"ts_code", "path", "sha256", "bytes"}, "EXACT_INPUT_BINDING_REQUIRED")
        code, path = binding["ts_code"], binding["path"]
        require(code in codes and (code, path) not in source_map, "DUPLICATE_OR_WRONG_STOCK_SOURCE_BINDING")
        outcomes._logical(path, t=frozen["exec_date"], t1=frozen["exit_date"], asof=current, code=code, dates=dates)
        natural.scorer._sha(binding["sha256"])
        require(type(binding["bytes"]) is int and 0 <= binding["bytes"] <= natural.MAX_BYTES, "INVALID_SOURCE_SIZE")
        source_map[code, path] = binding["sha256"]
    require(inputs == sorted(inputs, key=lambda b: (b["ts_code"], b["path"])), "SOURCE_BINDINGS_NOT_CANONICAL")
    require(len(origins) == len(inputs), "ORIGIN_BINDING_COUNT_CHANGED")
    for origin, binding in zip(origins, inputs):
        require(type(origin) is dict and set(origin) == {"ts_code", "path", "origin_path", "sha256"}
            and all(origin[k] == binding[k] for k in ("ts_code", "path", "sha256"))
            and type(origin["origin_path"]) is str and Path(origin["origin_path"]).is_absolute()
            and ".." not in Path(origin["origin_path"]).parts, "SOURCE_ORIGIN_BINDING_CHANGED")
    snapshot_path = "research_inputs/candidate_natural_forward/day_" + frozen["signal_date"] + ".json"
    by_code = {r["ts_code"]: r for r in frozen["prediction"]["rows"]}
    for code in codes:
        supplied = {str(labels.settlement.CALENDAR_PATH): labels.settlement.CALENDAR_SHA256,
            snapshot_path: expected_snapshot,
            **{path: digest for (stock, path), digest in source_map.items() if stock == code}}
        manifest = outcomes._manifest(frozen, by_code[code], supplied)
        _same(version["native_singleton_manifests"][code], manifest, "NATIVE_MANIFEST_CHANGED")
        report = version["native_singleton_reports"][code]
        for key, expected in {"schema_version": labels.LABEL_SCHEMA, "label_policy_id": labels.EXIT_POLICY_ID,
            "candidate_manifest_sha256": natural.scorer.canonical_sha(manifest), "research_only": True,
            "historical_counterfactual": True, "natural_forward_ledger_rewritten": False,
            "old_open_exit_labels_consumed": False, "actual_execution_claimed": False,
            "actual_capacity_verified": False, "production_activation_allowed": False,
            "round_trip_cost_rate": .0045, "feature_columns": ["board_stage"],
            "feature_evidence_kind": manifest["evidence_kind"]}.items():
            _same(report.get(key), expected, "NATIVE_REPORT_CONTRACT_CHANGED:" + key)
        labels.policy_v3.validate_contract(report)
        row = report["rows"][0]
        labels.policy_v3.validate_label_contract(row)
        require(row.get("stage_transition") == ("2→3" if by_code[code]["board_stage"] == 2 else "3→4")
            and type(row["promotion_rank"]) is int and row["promotion_rank"] == by_code[code]["promotion_rank"],
            "NATIVE_STAGE_OR_PROMOTION_RANK_CHANGED")
        _same(row.get("features"), {"board_stage": float(by_code[code]["board_stage"])},
            "NATIVE_D_FEATURES_CHANGED")
        require(row.get("feature_as_of_date") == frozen["signal_date"]
            and row.get("feature_available_at") == labels._timestamp(frozen["prediction_generated_at_utc"]).isoformat(),
            "NATIVE_FEATURE_TIME_CHANGED")
        for field in ("net_return", "conditional_net_return", "slot_net_return"):
            if row[field] is not None:
                natural.scorer._number(row[field], field)
        bound = report["source_files"]
        require(type(bound) is list and len(bound) >= 2, "NATIVE_SOURCE_BINDINGS_REQUIRED")
        seen = set()
        for binding in bound:
            require(type(binding) is dict and set(binding) == {"path", "sha256"}
                and binding["path"] not in seen and supplied.get(binding["path"]) == binding["sha256"],
                "NATIVE_SOURCE_BINDING_NOT_IN_INPUTS")
            seen.add(binding["path"])
        require({snapshot_path, str(labels.settlement.CALENDAR_PATH)} <= seen, "NATIVE_FROZEN_SOURCE_BINDING_MISSING")
    for name in ("candidate_slots", "promotion_slots"):
        _same(version[name], outcomes._slot_results(frozen[name], version["native_singleton_reports"]),
            "FOUR_SLOT_RESULT_CHANGED")


def _ledger(raw, expected, frozen, snapshot_sha, external_asof, dates, now):
    require(type(raw) is bytes and len(raw) <= natural.MAX_BYTES, "BOUNDED_LEDGER_BYTES_REQUIRED")
    value = natural._json(raw)
    require(type(value) is dict and set(value) == {"schema_version", "signal_date", "snapshot_file_sha256",
        "versions", "ledger_sha256", *outcomes.FLAGS}, "EXACT_APPEND_LEDGER_FIELDS_REQUIRED")
    require(value.get("schema_version") == outcomes.SCHEMA and value.get("signal_date") == frozen["signal_date"]
        and value.get("snapshot_file_sha256") == snapshot_sha, "LEGACY_OR_WRONG_DAY_LEDGER")
    versions = value["versions"]
    require(type(versions) is list and 0 < len(versions) <= 4096, "BOUNDED_NONEMPTY_VERSION_HISTORY_REQUIRED")
    # All version/row identities and maturity dates precede seals or return access.
    seen = [_version_identity(v, frozen, dates, external_asof)[0] for v in versions]
    require(seen == sorted(set(seen)) and seen[-1] == external_asof, "LEDGER_ASOF_OR_APPEND_ORDER_CHANGED")
    require(sha(raw) == expected, "EXTERNAL_LEDGER_SHA_CHANGED")
    outcomes._seal(value, "ledger_sha256")
    for key, flag in outcomes.FLAGS.items():
        _same(value[key], flag, "LEDGER_QUALIFICATION_CHANGED")
    previous = None
    for version in versions:
        _version(version, frozen, snapshot_sha, dates, now)
        if previous is not None:
            for code, report in previous["native_singleton_reports"].items():
                if report["rows"][0]["label_status"] in outcomes.TERMINAL:
                    current = version["native_singleton_reports"][code]
                    _same(current["rows"], report["rows"], "TERMINAL_ECONOMIC_HISTORY_CHANGED")
                    _same(current["source_files"], report["source_files"], "TERMINAL_SOURCE_HISTORY_CHANGED")
        previous = version
    return versions[-1], len(versions)


def _metrics(sequence):
    terminal = [r for r in sequence if r["status"] in outcomes.TERMINAL]
    filled = [r for r in terminal if r["status"] == labels.SETTLED]
    values = [r["slot_net_return"] for r in terminal]
    filled_values = [r["slot_net_return"] for r in filled]
    product, completed = 1.0, []
    for row in sequence:
        if row["status"] in outcomes.TERMINAL:
            product *= 1 + row["slot_net_return"]
            require(math.isfinite(product), "SYNTHETIC_CUMULATIVE_OVERFLOW")
            completed.append({"signal_date": row["signal_date"], "slot_net_return": row["slot_net_return"],
                "synthetic_cumulative_net_return": product - 1})
    counts = dict(sorted(Counter(r["status"] for r in sequence).items()))
    return {"verified_selection_days": len(sequence), "terminal_slots": len(terminal),
        "filled_settled_slots": len(filled), "known_no_fill_slots": len(terminal) - len(filled),
        "pending_slots": sum(n for status, n in counts.items() if status.startswith("PENDING_")),
        "missing_ledger_slots": counts.get("MISSING_OUTCOME_LEDGER", 0),
        "missing_candidate_slots": counts.get("MISSING_CANDIDATE", 0), "status_counts": counts,
        "positive_filled_slots": sum(v > 0 for v in filled_values),
        "negative_filled_slots": sum(v < 0 for v in filled_values),
        "zero_filled_slots": sum(v == 0 for v in filled_values),
        "filled_proxy_win_rate": sum(v > 0 for v in filled_values) / len(filled) if filled else None,
        "mean_net_slot_return": math.fsum(values) / len(values) if values else None,
        "mean_net_filled_proxy_return": math.fsum(filled_values) / len(filled) if filled else None,
        "minimum_net_slot_return": min(values, default=None),
        "complete_daily_sequence": completed,
        "synthetic_cumulative_net_return": product - 1 if completed else None,
        "all_supplied_days_terminal": bool(sequence) and len(terminal) == len(sequence),
        "daily_sequence": sequence}


def summarize_natural_statistics(day_inputs, *, as_of_date, calendar_raw, expected_calendar_sha256, clock=None):
    """Aggregate a caller-declared day universe; no implicit latest-file search.

    Missing ledger uses three None fields (raw/hash/asof); it never yields zero.
    The source receipt SHA is traceability only, not outcome-source authority.
    A supplied test clock visibly marks the result SYNTHETIC_CLOCK_ONLY.
    """
    code_state = _guard()
    asof = natural.scorer._date(as_of_date, "as_of_date")
    dates = _calendar(calendar_raw, expected_calendar_sha256)
    now = natural._now(clock)
    require(asof in dates and now >= labels._timestamp(labels._at(asof, "15:00:00")), "ASOF_SESSION_NOT_COMPLETED")
    days = _identities(day_inputs, asof, dates)
    _byte_budget(day_inputs)
    sequences, audit, states = {name: [] for name in GROUPS}, [], []
    total = 0
    for item in day_inputs:
        day = item["signal_date"]
        expected = item["expected_snapshot_sha256"]
        natural.scorer._sha(expected)
        proof_state = _proof(item["publication_proof"], day, expected)
        require(_identities(day_inputs, asof, dates) == days, "DAY_UNIVERSE_CHANGED_DURING_ADMISSION")
        _byte_budget(day_inputs)
        raw = item["snapshot_raw"]
        require(type(raw) is bytes and len(raw) <= natural.MAX_BYTES, "BOUNDED_SNAPSHOT_BYTES_REQUIRED")
        frozen = outcomes._snapshot(raw, expected)
        require(frozen["signal_date"] == day and frozen["clock_mode"] == "HOST_SYSTEM_UTC", "EXACT_HOST_CLOCK_NATURAL_SNAPSHOT_REQUIRED")
        require(dates[dates.index(day) + 1:dates.index(day) + 3] == [frozen["exec_date"], frozen["exit_date"]],
            "FROZEN_D_T_T1_CALENDAR_CHANGED")
        require(labels._timestamp(frozen["pre_cas_freeze_at_utc"]) <= now, "FROZEN_CLOCK_IS_IN_FUTURE")
        ledger_raw, ledger_sha, ledger_asof = item["ledger_raw"], item["expected_ledger_sha256"], item["ledger_as_of_date"]
        receipt_sha = item["source_collection_receipt_sha256"]
        if receipt_sha is not None:
            natural.scorer._sha(receipt_sha)
        if ledger_raw is None:
            require(ledger_sha is ledger_asof is receipt_sha is None, "MISSING_LEDGER_CANNOT_CLAIM_HASH_OR_OBSERVATION")
            latest, count = None, 0
        else:
            natural.scorer._sha(ledger_sha)
            require(ledger_asof is not None, "EXTERNAL_LEDGER_ASOF_REQUIRED")
            latest, count = _ledger(ledger_raw, ledger_sha, frozen, expected, ledger_asof, dates, now)
        total += len(raw) + (len(ledger_raw) if ledger_raw is not None else 0)
        require(total <= 128 * 1024 * 1024, "BOUNDED_TOTAL_STATISTICS_INPUTS_REQUIRED")
        for ranking in ("candidate", "promotion"):
            for index, slot in enumerate(frozen[ranking + "_slots"], 1):
                result = latest[ranking + "_slots"][index - 1] if latest is not None else {
                    "status": "MISSING_CANDIDATE" if slot["ts_code"] is None else "MISSING_OUTCOME_LEDGER",
                    "slot_net_return": None, "proxy_fill": None, "label_available_date": None}
                sequences[f"{ranking}_top{index}"].append({"signal_date": day, "ts_code": slot["ts_code"],
                    "ledger_as_of_date": ledger_asof, **{k: result[k] for k in
                    ("status", "proxy_fill", "slot_net_return", "label_available_date")}})
        audit.append({**proof_state, "ledger_file_sha256": ledger_sha, "ledger_as_of_date": ledger_asof,
            "ledger_version_count": count, "source_collection_receipt_sha256": receipt_sha})
        states.append((item, raw, ledger_raw, proof_state, expected, ledger_sha, ledger_asof, receipt_sha,
            item["publication_proof"]))
    common_days = [day for index, day in enumerate(days)
        if all(sequences[group][index]["status"] in outcomes.TERMINAL for group in GROUPS)]
    result = {"schema_version": SCHEMA, "status": "SYNTHETIC_CLOCK_ONLY" if clock is not None else
        ("BOUND_NATURAL_RESEARCH_STATISTICS" if days else "EMPTY_NATURAL_SAMPLE"),
        "as_of_date": asof, "signal_dates": list(days), "supplied_day_count": len(days),
        "universe_scope": "ALL_EXPLICITLY_SUPPLIED_BOUND_DAYS_NOT_PROOF_OF_NO_OMITTED_PUBLICATIONS",
        "whole_natural_history_completeness_verified": False,
        "publication_proof_verified_days": len(days), "groups": {k: _metrics(v) for k, v in sequences.items()},
        "common_complete_signal_dates": common_days,
        "common_complete_groups": {k: _metrics([r for r in v if r["signal_date"] in common_days]) for k, v in sequences.items()},
        "cumulative_interpretation": "SIGNED_PRODUCT_ONE_PLUS_TERMINAL_SLOT_RETURNS_IN_SIGNAL_D_ORDER_NOT_CAPITAL_NAV_OVERLAPPING_HOLDS_IGNORED",
        "win_rate_denominator": "FILLED_SETTLED_SLOTS_ONLY_ZERO_NOT_WIN",
        "mean_slot_denominator": "SETTLED_PLUS_VERIFIED_NATIVE_NO_FILL_ZERO_EXCLUDES_PENDING_AND_MISSING",
        "pairwise_comparison_scope": "COMMON_COMPLETE_FOUR_SLOT_DAYS_ONLY_NO_MODEL_SELECTION_OR_SIGNIFICANCE_CLAIM",
        "round_trip_cost_rate": .0045, "shadow_notional_cny": 100000,
        "calendar_sha256": expected_calendar_sha256, "input_bindings": audit,
        "statistics_code_sha256": SELF_SHA, "outcomes_writer_sha256": OUTCOMES_SHA,
        "publication_issuer_sha256": PUBLICATION_SHA, **FLAGS}
    # Publication guards may be long: repeat cheap caller/code checks afterwards.
    for item, raw, ledger_raw, proof_state, expected, ledger_sha, ledger_asof, receipt_sha, proof in states:
        require(_proof(item["publication_proof"], item["signal_date"], expected) == proof_state, "PUBLICATION_PROOF_CHANGED")
    def cheap_inputs():
        require(_identities(day_inputs, asof, dates) == days, "DAY_UNIVERSE_CHANGED")
        require(len(day_inputs) == len(states) and all(item is state[0] for item, state in zip(day_inputs, states)),
            "CALLER_DAY_OBJECTS_CHANGED")
        for item, raw, ledger_raw, proof_state, expected, ledger_sha, ledger_asof, receipt_sha, proof in states:
            require(item["snapshot_raw"] is raw and item["ledger_raw"] is ledger_raw
                and item["expected_snapshot_sha256"] == expected and item["expected_ledger_sha256"] == ledger_sha
                and item["ledger_as_of_date"] == ledger_asof and item["source_collection_receipt_sha256"] == receipt_sha
                and item["publication_proof"] is proof
                and all(getattr(proof, field) == value for field, value in proof_state.items()),
                "CALLER_BINDINGS_CHANGED_DURING_STATISTICS")
    cheap_inputs()
    require(_guard() == code_state, "STATISTICS_CODE_OR_DEPENDENCY_CHANGED")
    cheap_inputs()
    require(sha(natural._read(Path(__file__).absolute())[0]) == SELF_SHA
        and sha(natural._read(_outcome_file)[0]) == OUTCOMES_SHA, "FINAL_STATISTICS_CODE_CHANGED")
    if states:
        require(type(states[0][-1]) is _publication_type(), "FINAL_PUBLICATION_CODE_CHANGED")
    cheap_inputs()
    result["report_sha256"] = natural.scorer.canonical_sha(result)
    return result
