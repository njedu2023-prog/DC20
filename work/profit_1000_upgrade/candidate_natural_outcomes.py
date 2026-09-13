"""Append-only research outcome observations using the unchanged v3 label kernel.

No HTTP calls, fitting, natural-source admission, production writes or economic
reimplementation. Only externally bound files enter per-stock temporary roots.
Native singleton counterfactual reports remain unmodified and clearly labelled.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).absolute().parents[2]
SCHEMA = "dc20_candidate_natural_outcomes_append_only_v1"
REPORT_SCHEMA = "dc20_candidate_natural_posthoc_price_observation_v1"
REGISTRATION_SHA = "2b24d549c16b5ad6bb3c682eb491a56907ccd7cd986c4c2c9e494a76f4d79de2"
PINS = {
    "work/profit_1000_upgrade/candidate_natural_forward.py": "5a3967c88829be0e6b9a0d7c384b6d2cf12ac7c257bac4dff40d1a15ce2d5c15",
    "work/profit_1000_upgrade/labels_v3.py": "cfe592fd7d90101514204d45d4e7f3a27e12f13bd8d3905fc77d89c9dee2fe9b",
    "work/profit_1000_upgrade/policy_v3.py": "a384365e7200643738c4f03e6f8236cd4ac8984eb879ff69752f5a4de0ad729b",
    "work/profit_1000_upgrade/auction_truth_v3.py": "889765e435f2e44c6bedc21ef74789c5155081160da54624a147fdd3bca43665",
    "work/profit_1000_upgrade/auction_truth.py": "b71c2ae223bc07ef110b206a45b778c3ed4aad76273ff32be3984520b8b996af",
    "work/profit_1000_upgrade/auction_http_v3.py": "289fbcca8eb1642af7ea4a4c51bd6a136faf3ce96c5fc4c4e3a9091a369fa89c",
    "work/profit_1000_upgrade/minute_truth.py": "69f2eb9fe2ceb1579a59d5247a3f1ddc72b80d251abbed9fe69e06e0d83df268",
    "src/top10decision/decision/shadow_exit_1000.py": "23d1e693bbf4e38771b107f434aab7cd40f82855e0f480e391d819778e05c5af",
    "src/top10decision/decision/shadow_exit_minute_truth.py": "0cdd36ed69e734ab8c59bb3b44a5bf27cc702a9ce67879a225f4a94d1d14ee65",
    "src/top10decision/decision/executable_profit_shadow_settlement.py": "acb2cf0a74d5fe7849a1b480b512834916954d3c43ce4f71534c1cccdf92ea12",
    "src/top10decision/decision/executable_profit_shadow.py": "872a761235c52be0126f15e0e75b1d1ab855881a5657f7ebe82083fc0182d16c",
}


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def _sha(body):
    return hashlib.sha256(body).hexdigest()


# Check the file before importing executable helpers; the caller additionally
# pins this new wrapper itself at deployment/publication time.
for _relative, _expected in PINS.items():
    _file = ROOT / _relative
    require(not any(p.is_symlink() for p in (_file, *_file.parents))
        and _sha(_file.read_bytes()) == _expected, "FROZEN_OUTCOME_DEPENDENCY_CHANGED:" + _relative)

from work.profit_1000_upgrade import candidate_natural_forward as natural
from work.profit_1000_upgrade import labels_v3 as labels, auction_http_v3
from top10decision.decision import shadow_exit_1000, executable_profit_shadow

SELF_SHA = _sha(natural._read(Path(__file__).absolute())[0])
FLAGS = {"research_only": True, "production_activation_allowed": False,
    "source_authority_issued": False, "natural_forward_admission_issued": False,
    "git_publication_verified": False, "formal_ledger_written": False,
    "actual_execution_claimed": False, "actual_capacity_verified": False,
    "provider_timestamp_semantics_confirmed": False, "profitability_improvement_proven": False,
    "model_training_performed": False, "prediction_scores_recalculated": False,
    "native_economic_kernel_modified": False, "replacement_gate": "NOT_CONFIGURED"}
TERMINAL = labels.policy_v3.NO_FILL_STATUSES | {labels.SETTLED}


def _guard():
    modules = (natural, labels, labels.policy_v3, labels.auction_truth, labels.auction_truth._old(),
        auction_http_v3, labels.minute_truth, shadow_exit_1000, labels.minute_truth._minute,
        labels.settlement, executable_profit_shadow)
    state = []
    for (relative, expected), module in zip(PINS.items(), modules):
        require(Path(module.__file__).absolute() == ROOT / relative, "OUTCOME_DEPENDENCY_IMPORT_ORIGIN_CHANGED")
        body, identity = natural._read(ROOT / relative)
        require(_sha(body) == expected, "FROZEN_OUTCOME_DEPENDENCY_CHANGED:" + relative)
        state.append((relative, expected, identity))
    body, identity = natural._read(Path(__file__).absolute())
    require(_sha(body) == SELF_SHA, "OUTCOME_WRAPPER_CHANGED")
    return tuple(state), identity, natural._guard()


def _snapshot(raw, expected):
    natural.scorer._sha(expected)
    require(_sha(raw) == expected, "EXTERNAL_FROZEN_SNAPSHOT_SHA_MISMATCH")
    value = natural._json(raw)
    natural._sealed(value)
    for key, wanted in {"schema_version": natural.SCHEMA, "registration_id": natural.REGISTRATION_ID,
            "registration_sha256": REGISTRATION_SHA, "runner_sha256": PINS[next(iter(PINS))],
            "dependency_sha256": natural.PINS, "model_canonical_sha256": natural.MODEL_SHA,
            "entry_policy_id": natural.scorer.ENTRY_POLICY_ID, "exit_policy_id": natural.scorer.EXIT_POLICY_ID,
            "round_trip_cost_rate": .0045, "shadow_notional_cny": 100000, **natural.BOUNDARIES}.items():
        natural.scorer._exact(value.get(key), wanted, "FROZEN_SNAPSHOT_CONTRACT_CHANGED:" + key)
    require(value["model_evaluation"]["sha256"] == natural.EVALUATION_SHA, "FIXED_EVALUATION_BINDING_REQUIRED")
    day = natural.scorer._date(value["signal_date"], "signal_date")
    require(day >= "20260914", "NATURAL_D_BEFORE_REGISTERED_START")
    for field in ("exec_date", "exit_date"):
        natural.scorer._date(value[field], field)
    close, cutoff = natural._window(day, value["exec_date"])
    generated = labels._timestamp(value["prediction_generated_at_utc"])
    frozen = labels._timestamp(value["pre_cas_freeze_at_utc"])
    require(close <= generated <= frozen < cutoff, "FROZEN_PREDICTION_WINDOW_INVALID")
    require(value["clock_mode"] in {"HOST_SYSTEM_UTC", "INJECTED_TEST_CLOCK_RESEARCH_ONLY"}, "FROZEN_CLOCK_MODE_INVALID")
    evidence = value["D_source_evidence"]
    for field in ("source_authority_issued", "git_membership_verified", "point_in_time_availability_verified",
            "natural_freeze_verified", "production_activation_allowed", "future_outcomes_read"):
        require(evidence.get(field) is False, "FROZEN_D_SOURCE_QUALIFICATION_CHANGED")
    projection = evidence["projection"]
    require(evidence["signal_date"] == projection["signal_date"] == day
        and projection["exec_date"] == value["exec_date"] and projection["exit_date"] == value["exit_date"]
        and projection["projection_window_complete"] is True
        and evidence["four_file_bytes_verified"] is True and evidence["registered_source_bytes_verified"] is True,
        "FROZEN_D_PROJECTION_OR_CALENDAR_CHANGED")
    prediction = value["prediction"]
    require(prediction["schema_version"] == natural.scorer.SCHEMA and prediction["signal_date"] == day
        and prediction["model_sha256"] == natural.MODEL_SHA, "FROZEN_PREDICTION_MODEL_CHANGED")
    for key, flag in natural.scorer.FLAGS.items():
        natural.scorer._exact(prediction.get(key), flag, "FROZEN_PREDICTION_QUALIFICATION_CHANGED:" + key)
    rows = prediction["rows"]
    # Validate every identity before reading score or feature values.
    require(type(rows) is list and len(rows) <= 10, "FULL_ZERO_TO_TEN_FROZEN_ROWS_REQUIRED")
    identities = set()
    for row in rows:
        require(type(row) is dict and row.get("signal_date") == day
            and type(row.get("ts_code")) is str and re.fullmatch(r"[0-9]{6}\.(SH|SZ)", row["ts_code"])
            and row["ts_code"] not in identities, "FROZEN_FULL_COHORT_IDENTITY_CHANGED")
        identities.add(row["ts_code"])
    require(prediction["candidate_count"] == len(rows) and type(prediction["candidate_count"]) is int,
        "FROZEN_CANDIDATE_COUNT_CHANGED")
    for index, row in enumerate(rows, 1):
        require(type(row.get("candidate_rank")) is int and row["candidate_rank"] == index,
            "FROZEN_CANDIDATE_RANK_CHANGED")
        natural.scorer._number(row["candidate_score"], "candidate_score")
    require(rows == sorted(rows, key=lambda r: (-r["candidate_score"], r["ts_code"])), "FROZEN_SCORE_ORDER_CHANGED")
    ordered = sorted(rows, key=lambda r: r["promotion_rank"])
    selected = natural.scorer._project(ordered, day)
    projected = natural.scorer._project(projection["rows"], day)
    require(natural.scorer.canonical_sha(selected) == natural.scorer.canonical_sha(projected)
        == prediction["whitelisted_D_rows_sha256"], "FROZEN_WHITELIST_FEATURES_CHANGED")
    for name, rank in (("candidate_slots", "candidate_rank"), ("promotion_slots", "promotion_rank")):
        natural.scorer._exact(value[name], natural._slots(rows, rank), "FROZEN_FOUR_SLOT_MEMBERSHIP_CHANGED")
    return value


def _logical(relative, *, t, t1, asof, code, dates):
    require(type(relative) is str and "\\" not in relative, "EXACT_SCOPED_SOURCE_PATH_REQUIRED")
    daily = re.fullmatch(r"data/market/raw/(20[0-9]{2})/(20[0-9]{6})/(daily|stk_limit)\.csv", relative)
    auction = re.fullmatch(r"data/research/canonical_auction_price_v3/(20[0-9]{2})/(20[0-9]{6})/stk_auction\.(data|meta)\.json", relative)
    minute = re.fullmatch(r"research_inputs/minute_truth_0931/(20[0-9]{2})/(20[0-9]{6})/([0-9]{6}_(?:SH|SZ))\.(data|meta)\.json", relative)
    match = daily or auction or minute
    require(match is not None, "UNREGISTERED_OUTCOME_SOURCE_PATH")
    day = natural.scorer._date(match[2], "source_date")
    require(match[1] == day[:4] and day in dates and t <= day <= asof, "FUTURE_OR_OUT_OF_SCOPE_OUTCOME_SOURCE")
    require(not auction or day == t, "AUCTION_MUST_BE_EXACT_T")
    require(not minute or (day >= t1 and match[3] == code.replace(".", "_")), "MINUTE_MUST_BE_EXACT_STOCK_AND_EXIT_SESSION")


def _bundle(bundle, codes, *, t, t1, asof, dates):
    require(type(bundle) is dict and set(bundle) == {"calendar", "by_code"}, "EXACT_SOURCE_BUNDLE_REQUIRED")
    require(type(bundle["calendar"]) is dict and set(bundle["calendar"]) == {"origin_path", "sha256"}, "EXACT_CALENDAR_BINDING_REQUIRED")
    require(type(bundle["by_code"]) is dict and set(bundle["by_code"]) == set(codes), "EXACT_FOUR_SLOT_UNION_SOURCE_ROOTS_REQUIRED")
    planned, roots = [], set()
    # Whitelist all identities/paths/dates before opening any outcome file.
    for code in codes:
        item = bundle["by_code"][code]
        require(type(item) is dict and set(item) == {"source_root", "bindings"}, "EXACT_STOCK_SOURCE_SPEC_REQUIRED")
        root = natural._path(item["source_root"])
        require(root.is_dir() and str(root) not in roots, "DISTINCT_EXISTING_STOCK_SOURCE_ROOT_REQUIRED")
        roots.add(str(root))
        require(type(item["bindings"]) is list and len(item["bindings"]) <= 4096, "BOUNDED_SOURCE_BINDINGS_REQUIRED")
        seen = set()
        for binding in item["bindings"]:
            require(type(binding) is dict and set(binding) == {"path", "sha256"}, "EXACT_SOURCE_BINDING_REQUIRED")
            relative = binding["path"]
            _logical(relative, t=t, t1=t1, asof=asof, code=code, dates=dates)
            require(relative not in seen, "DUPLICATE_OUTCOME_SOURCE_BINDING")
            seen.add(relative)
            natural.scorer._sha(binding["sha256"])
            planned.append((code, relative, root / relative, binding["sha256"]))
    return planned, tuple(sorted(roots)), natural.scorer.canonical_sha(bundle)


def _manifest(snapshot, row, sources):
    day, code = snapshot["signal_date"], row["ts_code"]
    relative = "research_inputs/candidate_natural_forward/day_" + day + ".json"
    binding = {"path": relative, "sha256": sources[relative]}
    return {"schema_version": labels.SCHEMA, "plan_version": "v3",
        "evidence_kind": "RETROSPECTIVE_D_ONLY_RECONSTRUCTION", "feature_columns": ["board_stage"],
        "source_bindings": [binding], "expected_candidate_codes": {day: [code]},
        **labels.policy_v3.source_policy_contract(),
        "rows": [{"signal_date": day, "ts_code": code, "stage": row["board_stage"],
            "promotion_rank": row["promotion_rank"], "exec_date": snapshot["exec_date"],
            "scheduled_exit_date": snapshot["exit_date"], "feature_as_of_date": day,
            "feature_available_at": snapshot["prediction_generated_at_utc"],
            "features": {"board_stage": row["board_stage"]}, "shadow_max_price": None}],
        "truth_sources": [{"path": key, "sha256": sha} for key, sha in sorted(sources.items())]}


def _slot_results(frozen, native):
    result = []
    for slot in frozen:
        row = None if slot["ts_code"] is None else native[slot["ts_code"]]["rows"][0]
        result.append({"frozen_slot": dict(slot), "status": "MISSING_CANDIDATE" if row is None else row["label_status"],
            **{key: None if row is None else row[key] for key in
               ("proxy_fill", "net_return", "conditional_net_return", "slot_net_return", "label_available_date")}})
    return result


def _seal(value, field):
    core = {key: item for key, item in value.items() if key != field}
    require(value.get(field) == natural.scorer.canonical_sha(core), "OUTCOME_RECORD_SEAL_CHANGED")


def _output(value, snapshot_path, roots):
    output = natural._path(value)
    require(output.name == "candidate_natural_outcomes", "ISOLATED_OUTCOME_DIRECTORY_REQUIRED")
    if ROOT in output.parents:
        require(output.relative_to(ROOT).as_posix() == "work/profit_1000_upgrade/candidate_natural_outcomes",
            "OUTCOME_OUTPUT_CANNOT_ENTER_PRODUCTION")
    require(output != snapshot_path and output not in snapshot_path.parents, "SNAPSHOT_CANNOT_BE_INSIDE_OUTCOME_OUTPUT")
    for root in roots:
        root = Path(root)
        require(root != output and root not in output.parents and output not in root.parents,
            "OUTCOME_OUTPUT_AND_SOURCE_ROOTS_MUST_BE_DISJOINT")
    return output


def evaluate_natural_outcomes(snapshot_path, output_root, *, expected_snapshot_sha256, as_of_date,
                              source_bundle, expected_existing_ledger_sha256=None, clock=None):
    """Append one completed-asof observation, never modify a previous version.

    Sources are separate per selected stock. Only exact externally SHA-bound
    bytes are copied into disposable roots. An API test clock is marked, not
    an independently verified natural clock or a source-publication receipt.
    """
    code_state = _guard()
    plan, plan_sha, plan_identity = natural.registration()
    require(plan_sha == REGISTRATION_SHA, "FIXED_NATURAL_REGISTRATION_REQUIRED")
    asof = natural.scorer._date(as_of_date, "as_of_date")
    now = natural._now(clock)
    require(now >= labels._timestamp(labels._at(asof, "15:00:00")), "ASOF_MUST_BE_COMPLETED_SESSION")
    snapshot_path = natural._path(snapshot_path)
    raw, identity = natural._read(snapshot_path)
    frozen = _snapshot(raw, expected_snapshot_sha256)
    require(labels._timestamp(frozen["pre_cas_freeze_at_utc"]) <= now, "FROZEN_PREDICTION_IS_IN_CLOCK_FUTURE")
    day, t, t1 = (frozen[key] for key in ("signal_date", "exec_date", "exit_date"))
    require(day <= asof, "ASOF_CANNOT_PRECEDE_FROZEN_D")
    require(type(source_bundle) is dict and set(source_bundle) == {"calendar", "by_code"}
        and type(source_bundle["calendar"]) is dict
        and set(source_bundle["calendar"]) == {"origin_path", "sha256"}, "EXACT_SOURCE_BUNDLE_REQUIRED")
    calendar = source_bundle["calendar"]
    natural.scorer._sha(calendar["sha256"])
    require(calendar["sha256"] == labels.settlement.CALENDAR_SHA256, "PINNED_SETTLEMENT_CALENDAR_REQUIRED")
    original_calendar = [b for b in frozen["D_source_evidence"]["source_file_bindings"]
        if b["receipt_path"] == str(labels.settlement.CALENDAR_PATH)]
    require(len(original_calendar) == 1 and original_calendar[0]["sha256"] == calendar["sha256"],
        "OUTCOME_CALENDAR_MUST_MATCH_FROZEN_D")
    calendar_path = natural._path(calendar["origin_path"])
    calendar_raw, calendar_identity = natural._read(calendar_path)
    require(_sha(calendar_raw) == calendar["sha256"], "CALENDAR_BYTES_CHANGED")
    codes = sorted({slot["ts_code"] for key in ("candidate_slots", "promotion_slots")
        for slot in frozen[key] if slot["ts_code"] is not None})
    native, manifests, copied, bindings, origins, original_states = {}, {}, {}, [], [], []
    snapshot_relative = "research_inputs/candidate_natural_forward/day_" + day + ".json"
    with tempfile.TemporaryDirectory(prefix="dc20-natural-outcomes-") as temporary:
        staging = Path(temporary).resolve()
        cal = staging / labels.settlement.CALENDAR_PATH
        cal.parent.mkdir(parents=True); cal.write_bytes(calendar_raw)
        dates = labels.settlement._strict_open_dates(staging)
        require(day in dates and asof in dates and dates[dates.index(day) + 1:dates.index(day) + 3] == [t, t1],
            "FROZEN_D_T_T1_OR_COMPLETED_ASOF_CALENDAR_CHANGED")
        planned, source_roots, bundle_sha = _bundle(source_bundle, codes, t=t, t1=t1, asof=asof, dates=dates)
        output = _output(output_root, snapshot_path, source_roots)
        require(output != calendar_path and output not in calendar_path.parents, "CALENDAR_CANNOT_BE_INSIDE_OUTPUT")
        total_bytes = 0
        for code, relative, origin, expected in planned:
            body, original_identity = natural._read(origin)
            require(_sha(body) == expected, "EXTERNAL_OUTCOME_SOURCE_SHA_MISMATCH")
            total_bytes += len(body)
            require(total_bytes <= 128 * 1024 * 1024, "BOUNDED_TOTAL_OUTCOME_SOURCE_BYTES_REQUIRED")
            if relative.endswith(".meta.json"):
                try:
                    meta = natural._json(body)
                    fetched = labels._timestamp(meta["fetched_at_utc"])
                except (ValueError, TypeError, KeyError):
                    pass  # The native loader returns pending for malformed metadata.
                else:
                    require(fetched <= now, "SOURCE_FETCH_TIMESTAMP_IS_IN_CLOCK_FUTURE")
            original_states.append((origin, expected, original_identity))
            copied.setdefault(code, {})[relative] = body
            bindings.append({"ts_code": code, "path": relative, "sha256": expected, "bytes": len(body)})
            origins.append({"ts_code": code, "path": relative, "origin_path": str(origin), "sha256": expected})
        by_code = {row["ts_code"]: row for row in frozen["prediction"]["rows"]}
        for code in codes:
            root = staging / code.replace(".", "_")
            data = {str(labels.settlement.CALENDAR_PATH): calendar_raw, snapshot_relative: raw,
                **copied.get(code, {})}
            for relative, body in data.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(body)
            # Valid sources must be the exact per-code request, not an altered
            # candidate-scope schema or a full-market request with extra rows.
            try:
                loaded = labels.auction_truth.load(root, t) if t <= asof else None
            except labels.auction_truth.AuctionSourceError:
                loaded = None  # Native kernel preserves malformed/missing as pending.
            if loaded is not None:
                require(loaded.requested_code == code, "EXACT_SINGLE_STOCK_NATIVE_AUCTION_REQUEST_REQUIRED")
            manifest = _manifest(frozen, by_code[code], {key: _sha(body) for key, body in data.items()})
            report = labels.build_labels(root, manifest, as_of_date=asof)
            require(report["historical_counterfactual"] is True and len(report["rows"]) == 1
                and report["rows"][0]["ts_code"] == code, "NATIVE_SINGLETON_REPORT_CHANGED")
            native[code], manifests[code] = report, manifest
        for code, report in native.items():
            labels.policy_v3.validate_label_contract(report["rows"][0])
    payload = {"schema_version": REPORT_SCHEMA, "signal_date": day, "exec_date": t, "exit_date": t1,
        "as_of_date": asof, "snapshot_file_sha256": expected_snapshot_sha256,
        "snapshot_sha256": frozen["snapshot_sha256"], "registration_sha256": REGISTRATION_SHA,
        "model_canonical_sha256": natural.MODEL_SHA, "model_evaluation_sha256": natural.EVALUATION_SHA,
        "model_feature_order": list(natural.scorer.FEATURES),
        "full_frozen_prediction": frozen["prediction"], "full_frozen_candidate_count": len(frozen["prediction"]["rows"]),
        "evaluated_slot_union_codes": codes, "native_singleton_manifests": manifests,
        "native_singleton_reports": native, "native_report_scope": "ONE_STOCK_NOT_FULL_N_TRAINING_COHORT",
        "observation_kind": "POSTHOC_PRICE_PROXY_FOR_BOUND_PREBUY_RESEARCH_SELECTION_NOT_NATURAL_ADMISSION",
        "native_feature_projection_kind": "BOUND_PREBUY_FROZEN_SELECTION_STAGE_ONLY_PROJECTION",
        "candidate_slots": _slot_results(frozen["candidate_slots"], native),
        "promotion_slots": _slot_results(frozen["promotion_slots"], native),
        "calendar_sha256": calendar["sha256"], "input_bindings": sorted(bindings, key=lambda b: (b["ts_code"], b["path"])),
        "origin_bindings": sorted(origins, key=lambda b: (b["ts_code"], b["path"])),
        "observed_at_utc": natural._stamp(now), "clock_mode": "HOST_SYSTEM_UTC" if clock is None else "INJECTED_TEST_CLOCK_RESEARCH_ONLY",
        "frozen_clock_mode": frozen["clock_mode"], "writer_sha256": SELF_SHA, "dependencies": PINS,
        "original_snapshot_bytes_preserved": True, "round_trip_cost_rate": .0045,
        "shadow_notional_cny": 100000, "fees_subtracted_again": False, **FLAGS}
    payload["report_sha256"] = natural.scorer.canonical_sha(payload)
    target = output / ("day_" + day + ".json")
    existing_raw = natural._read(target)[0] if target.exists() else None
    if existing_raw is None:
        require(expected_existing_ledger_sha256 is None, "EXPECTED_PRIOR_OUTCOME_LEDGER_MISSING")
        ledger = {"schema_version": SCHEMA, "signal_date": day, "snapshot_file_sha256": expected_snapshot_sha256,
            "versions": [], **FLAGS}
    else:
        require(type(expected_existing_ledger_sha256) is str and _sha(existing_raw) == expected_existing_ledger_sha256,
            "EXTERNAL_PRIOR_OUTCOME_LEDGER_SHA_REQUIRED_OR_CHANGED")
        ledger = natural._json(existing_raw); _seal(ledger, "ledger_sha256")
        require(ledger["schema_version"] == SCHEMA and ledger["signal_date"] == day
            and ledger["snapshot_file_sha256"] == expected_snapshot_sha256, "PRIOR_LEDGER_IDENTITY_CHANGED")
        for key, wanted in FLAGS.items():
            natural.scorer._exact(ledger.get(key), wanted, "PRIOR_LEDGER_QUALIFICATION_CHANGED")
        versions = ledger["versions"]
        require(type(versions) is list and 0 < len(versions) <= 4096, "INVALID_PRIOR_VERSION_HISTORY")
        previous_asof = ""
        for version in versions:
            _seal(version, "report_sha256")
            require(previous_asof < version["as_of_date"] <= asof
                and version["snapshot_file_sha256"] == expected_snapshot_sha256, "OUTCOME_ASOF_MUST_APPEND_NOT_REDATE")
            previous_asof = version["as_of_date"]
        for code, old in versions[-1]["native_singleton_reports"].items():
            if old["rows"][0]["label_status"] in TERMINAL:
                require(old["rows"] == native[code]["rows"] and old["source_files"] == native[code]["source_files"],
                    "PRIOR_TERMINAL_OUTCOME_OR_SOURCE_CANNOT_CHANGE")
    same_asof = bool(ledger["versions"] and ledger["versions"][-1]["as_of_date"] == asof)
    if same_asof:
        comparable = lambda r: {k: v for k, v in r.items() if k not in ("observed_at_utc", "origin_bindings", "report_sha256")}
        require(comparable(ledger["versions"][-1]) == comparable(payload), "SAME_ASOF_OUTCOME_CONFLICT")
    else:
        ledger["versions"].append(payload)
        ledger.pop("ledger_sha256", None)
        ledger["ledger_sha256"] = natural.scorer.canonical_sha(ledger)
        require(len(natural.storage.encoded(ledger)) <= natural.MAX_BYTES, "BOUNDED_OUTCOME_LEDGER_BYTES_REQUIRED")

    def guard():
        require(natural._read(snapshot_path, identity)[0] == raw, "FROZEN_SNAPSHOT_CHANGED_DURING_OUTCOME")
        require(natural._read(calendar_path, calendar_identity)[0] == calendar_raw, "CALENDAR_CHANGED_DURING_OUTCOME")
        natural._read(natural.REGISTRATION_PATH, plan_identity)
        require(natural.registration() == (plan, plan_sha, plan_identity) and _guard() == code_state,
            "OUTCOME_CODE_OR_REGISTRATION_CHANGED")
        require(_bundle(source_bundle, codes, t=t, t1=t1, asof=asof, dates=dates)[2] == bundle_sha,
            "CALLER_SOURCE_BUNDLE_CHANGED")
        for origin, expected, original_identity in original_states:
            require(_sha(natural._read(origin, original_identity)[0]) == expected, "OUTCOME_SOURCE_CHANGED")
    guard()
    if same_asof:
        stored, saved_sha = existing_raw, _sha(existing_raw)
    else:
        saved_sha = natural.storage.compare_and_swap(target, ledger, expected_existing_ledger_sha256)
        stored = natural.storage.encoded(ledger)
    guard()
    require(natural._read(target)[0] == stored and _sha(stored) == saved_sha, "PERSISTED_OUTCOME_LEDGER_CHANGED")
    completed = natural._now(clock)
    require(completed >= now, "CLOCK_MOVED_BACKWARDS_OUTCOME_NOT_ADMITTED")
    return {"schema_version": "dc20_candidate_natural_outcomes_local_receipt_v1",
        "status": "EXISTING_IDENTICAL_ASOF_REVALIDATED" if same_asof else "RESEARCH_OUTCOME_OBSERVATION_APPENDED",
        "signal_date": day, "as_of_date": asof, "ledger_path": str(target), "ledger_file_sha256": saved_sha,
        "version_count": len(ledger["versions"]), "new_version_written": not same_asof,
        "local_operation_completed_at_utc": natural._stamp(completed), **FLAGS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--snapshot-sha256", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--source-bundle", required=True, type=Path)
    parser.add_argument("--existing-ledger-sha256")
    args = parser.parse_args(argv)
    result = evaluate_natural_outcomes(args.snapshot, args.output_root,
        expected_snapshot_sha256=args.snapshot_sha256, as_of_date=args.as_of_date,
        source_bundle=natural._json(natural._read(args.source_bundle)[0]),
        expected_existing_ledger_sha256=args.existing_ledger_sha256)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
