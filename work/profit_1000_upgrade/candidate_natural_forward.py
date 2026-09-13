"""Fixed-model research snapshots, never production or publication authority.

CLI time comes only from the host UTC clock. An injected API clock is visibly
test-only. A successful local receipt is not Git publication, independent
source admission, a broker fill, or permission to replace the formal ranking.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import stat
from zoneinfo import ZoneInfo

SCHEMA = "dc20_fixed_candidate_natural_research_snapshot_20260913_v1"
REGISTRATION_ID = "dc20_fixed_ridge_999666_forward_research_20260913_v1"
CODE_ROOT = Path(__file__).absolute().parents[2]
REGISTRATION_PATH = Path(__file__).with_name("candidate_natural_forward_registration.json")
MODEL_SHA = "999666791b147e4d120ba9b7e10d9d1fc846ba171efbba73b2488ca56ce6f589"
EVALUATION_SHA = "779baaffb008165e88a88f568497d42b410dcbec03e9f613b099464d245a4872"
PINS = {
    "work/profit_1000_upgrade/candidate_d_source_adapter.py": "bb9b32de5be84b55793d8e8c19dd6960727e90de83605c4f5a1c360dbd3d982e",
    "work/profit_1000_upgrade/candidate_forward_predict.py": "6ada7749e033b5d3e5c39c4d674d835b9113d83b4a37ef9d7e7fbad70ac95719",
    "forward/storage.py": "cc54e3b2ac1750e021a706641c592306cfcac3454c296f7c0b12b893ff13d098",
}
MAX_BYTES = 64 * 1024 * 1024
SHANGHAI = ZoneInfo("Asia/Shanghai")
BOUNDARIES = {"research_only": True, "production_activation_allowed": False,
    "source_authority_issued": False, "git_publication_verified": False,
    "point_in_time_source_availability_verified": False,
    "natural_forward_admission_issued": False, "formal_ledger_written": False,
    "model_retrained": False, "actual_execution_claimed": False,
    "profitability_improvement_proven": False, "replacement_gate": "NOT_CONFIGURED"}


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _path(value):
    require(type(value) is str or isinstance(value, Path), "EXACT_PATH_REQUIRED")
    path = Path(value)
    require(path.is_absolute() and ".." not in path.parts
        and not any(p.is_symlink() for p in (path, *path.parents)), "ABSOLUTE_UNALIASED_PATH_REQUIRED")
    return path


def _read(value, expected_identity=None):
    path = _path(value)
    before = path.stat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= MAX_BYTES,
        "REGULAR_BOUNDED_SINGLE_LINK_FILE_REQUIRED")
    identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    require(expected_identity is None or identity(before) == expected_identity,
        "SOURCE_IDENTITY_CHANGED_BEFORE_READ")
    with path.open("rb") as stream:
        body = stream.read(MAX_BYTES + 1)
    require(len(body) == before.st_size and identity(_path(path).stat()) == identity(before), "FILE_CHANGED_DURING_READ")
    return body, identity(before)


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: require(False, "NONFINITE_JSON"))


def _code_state():
    result = []
    for relative, expected in PINS.items():
        body, identity = _read(CODE_ROOT / relative)
        require(_sha(body) == expected, "FROZEN_DEPENDENCY_CHANGED:" + relative)
        result.append((relative, expected, identity))
    body, identity = _read(Path(__file__).absolute())
    require(_sha(body) == SELF_SHA, "RUNNER_CHANGED")
    return tuple(result) + ((str(Path(__file__).absolute()), SELF_SHA, identity),)


SELF_SHA = _sha(_read(Path(__file__).absolute())[0])
_code_state()
from work.profit_1000_upgrade import candidate_d_source_adapter as source
from work.profit_1000_upgrade import candidate_forward_predict as scorer
from forward import storage


def _guard():
    for module, relative in zip((source, scorer, storage), PINS):
        require(Path(module.__file__).absolute() == CODE_ROOT / relative, "IMPORTED_DEPENDENCY_ORIGIN_CHANGED")
    return _code_state(), source._code_guard()


def registration():
    raw, identity = _read(REGISTRATION_PATH)
    expected = {"schema_version": "dc20_fixed_candidate_natural_registration_v1",
        "registration_id": REGISTRATION_ID, "status": "RESEARCH_SNAPSHOT_ONLY_NOT_PRODUCTION",
        "model_canonical_sha256": MODEL_SHA, "evaluation_file_sha256": EVALUATION_SHA,
        "model_specification": scorer.MODEL_SPEC, "feature_order": list(scorer.FEATURES),
        "missing_signals_must_be_none": list(scorer.MISSING_SIGNALS),
        "start_signal_date": "20260914", "training_cutoff_date": "20251111",
        "entry_policy_id": scorer.ENTRY_POLICY_ID, "exit_policy_id": scorer.EXIT_POLICY_ID,
        "round_trip_cost_rate": .0045, "stress_round_trip_cost_rate": .009,
        "shadow_notional_cny": 100000, "maximum_auction_participation": .01,
        "prediction_window": "D_CLOSE_THROUGH_STRICTLY_BEFORE_T_0925_ASIA_SHANGHAI",
        "candidate_and_promotion_slots": [1, 2], "negative_score_skip_allowed": False,
        "missing_candidate_or_truth_is_zero": False, "same_D_replacement_allowed": False,
        "code_dependencies": PINS, "source_overlay_contract": scorer.OVERLAY_CONTRACT,
        "replacement_review_thresholds": "NOT_CONFIGURED", "production_activation_allowed": False,
        "publication_requirement": "WORKFLOW_MUST_BIND_SNAPSHOT_AND_LOCAL_RECEIPT_TO_REAL_PRE_T0925_PUBLICATION"}
    value = _json(raw)
    scorer._exact(value, expected, "FIXED_REGISTRATION_CHANGED")
    return value, _sha(raw), identity


def _plain(value):
    """Only containers created by the pinned adapter/scorer reach this helper."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if type(value) in (tuple, list):
        return [_plain(item) for item in value]
    require(value is None or type(value) in (str, int, float, bool), "NONSCALAR_ADAPTER_OUTPUT")
    return value


def _now(clock):
    value = datetime.now(timezone.utc) if clock is None else clock()
    require(type(value) is datetime and value.tzinfo is not None, "AWARE_UTC_CLOCK_REQUIRED")
    return value.astimezone(timezone.utc)


def _stamp(value):
    return value.isoformat(timespec="microseconds")


def _window(day, t):
    return (datetime.strptime(day + "150000", "%Y%m%d%H%M%S").replace(tzinfo=SHANGHAI),
            datetime.strptime(t + "092500", "%Y%m%d%H%M%S").replace(tzinfo=SHANGHAI))


def _output(root, source_root, evaluation):
    root = _path(root)
    require(root.name == "candidate_natural_forward", "ISOLATED_CANDIDATE_OUTPUT_DIRECTORY_REQUIRED")
    for parent in (source_root, CODE_ROOT):
        if parent == root or parent in root.parents:
            require(root.relative_to(parent).as_posix() == "work/profit_1000_upgrade/candidate_natural_forward",
                "OUTPUT_MUST_NOT_ENTER_PRODUCTION_OR_INPUT_DIRECTORIES")
    require(root != evaluation and root not in evaluation.parents, "MODEL_CANNOT_BE_INSIDE_OUTPUT")
    require(not root.exists() or root.is_dir(), "OUTPUT_ROOT_IS_NOT_DIRECTORY")
    return root


def _slots(rows, rank_key):
    ordered = sorted(rows, key=lambda r: r[rank_key])
    result = []
    for slot in (1, 2):
        row = ordered[slot - 1] if slot <= len(ordered) else None
        result.append({"slot": slot, "status": "PENDING_T" if row else "MISSING_CANDIDATE",
            **{key: None if row is None else row[key] for key in
               ("ts_code", "board_stage", "promotion_rank", "candidate_rank", "candidate_score")},
            "proxy_fill": None, "net_return": None, "slot_net_return": None})
    return result


def _sealed(record):
    require(type(record) is dict and "snapshot_sha256" in record, "INVALID_EXISTING_SNAPSHOT")
    core = {key: value for key, value in record.items() if key != "snapshot_sha256"}
    require(scorer.canonical_sha(core) == record["snapshot_sha256"], "EXISTING_SNAPSHOT_TAMPERED")
    return core


def freeze_natural_day(source_root, output_root, model_evaluation_path, *, signal_date,
                       expected_p0_sha256, source_path_map=None,
                       expected_existing_snapshot_sha256=None, clock=None):
    """Score and CAS-save one D; injected clocks never establish natural time.

    Existing identical snapshots are revalidated, never retimestamped. The
    returned receipt must be saved/published by the calling workflow together
    with the exact snapshot bytes. No caller argument asserts source authority.
    """
    day = scorer._date(signal_date, "signal_date")
    require(day >= "20260914", "FORWARD_D_MUST_START_AT_20260914")
    started = _now(clock)
    code_state = _guard()
    plan, plan_sha, plan_identity = registration()
    source_root, evaluation = _path(source_root), _path(model_evaluation_path)
    require(source_root.is_dir(), "SOURCE_ROOT_REQUIRED")
    output_root = _output(output_root, source_root, evaluation)
    path = output_root / ("day_" + day + ".json")
    existing_raw = _read(path)[0] if path.exists() else None
    if existing_raw is None:
        require(expected_existing_snapshot_sha256 is None, "EXPECTED_PRIOR_SNAPSHOT_MISSING")
    else:
        require(type(expected_existing_snapshot_sha256) is str
            and re.fullmatch(r"[0-9a-f]{64}", expected_existing_snapshot_sha256)
            and _sha(existing_raw) == expected_existing_snapshot_sha256,
            "EXTERNAL_EXISTING_SNAPSHOT_SHA_REQUIRED_OR_CHANGED")
    evaluation_raw, evaluation_identity = _read(evaluation)
    require(_sha(evaluation_raw) == EVALUATION_SHA, "FIXED_REAL_EVALUATION_FILE_REQUIRED")
    evaluated = _json(evaluation_raw)
    require(type(evaluated) is dict and evaluated.get("status") == "DEVELOPMENT_ONLY_FITTED", "FIXED_FITTED_MODEL_REQUIRED")
    model = evaluated["candidate_model"]
    require(scorer.canonical_sha(model) == MODEL_SHA, "FIXED_MODEL_SHA_REQUIRED")
    expected_before = source._arguments(source_root, day, expected_p0_sha256, source_path_map)
    admitted = source.project_bound_p0_d(source_root, signal_date=day,
        expected_p0_sha256=expected_p0_sha256, source_path_map=source_path_map)
    require(admitted["status"] in ("PROJECTED_UNVERIFIED_D_FEATURES", "EMPTY_P0_CANDIDATE_INPUT")
        and admitted["four_file_bytes_verified"] is True and admitted["registered_source_bytes_verified"] is True,
        "D_SOURCE_PROJECTION_BLOCKED")
    projection = admitted["projection"]
    require(projection["projection_window_complete"] is True, "COMPLETE_D_OBSERVED_WINDOWS_REQUIRED")
    close, cutoff = _window(day, projection["exec_date"])
    require(started >= close, "CANNOT_SCORE_BEFORE_D_CLOSE")
    if existing_raw is None:
        require(started < cutoff, "LATE_FIRST_FREEZE_FORBIDDEN")
    # The adapter has already checked the complete input identities. Copy only
    # this explicit scalar feature surface, not original P0 rows or outputs.
    keys = tuple(dict.fromkeys(("signal_date", "ts_code", "board_stage", "promotion_rank",
        "feature_as_of_date", "promotion_oof_train_end", *scorer.MISSING_SIGNALS, *scorer.NUMERIC_FEATURES)))
    rows = [{key: row[key] for key in keys} for row in projection["rows"]]
    evidence = _plain(admitted)
    input_state = []
    for binding in evidence["source_file_bindings"]:
        origin = _path(binding["origin_path"])
        require(output_root != origin and output_root not in origin.parents, "INPUT_CANNOT_BE_INSIDE_OUTPUT")
        body, identity = _read(origin)
        require(_sha(body) == binding["sha256"] and len(body) == binding["bytes"], "D_SOURCE_CHANGED_BEFORE_SCORING")
        input_state.append((origin, _sha(body), identity))
    predictions = scorer.predict_forward(model, rows, expected_model_sha256=MODEL_SHA, signal_date=day)
    generated = _now(clock)
    require(generated >= started, "CLOCK_MOVED_BACKWARDS")
    payload = {"schema_version": SCHEMA, "registration_id": REGISTRATION_ID,
        "registration_sha256": plan_sha, "runner_sha256": SELF_SHA, "dependency_sha256": dict(PINS),
        "signal_date": day, "exec_date": projection["exec_date"], "exit_date": projection["exit_date"],
        "model_evaluation": {"path": str(evaluation), "sha256": EVALUATION_SHA, "bytes": len(evaluation_raw)},
        "model_canonical_sha256": MODEL_SHA, "D_source_evidence": evidence,
        "input_arguments": {"source_root": expected_before[0], "signal_date": day,
            "expected_p0_sha256": expected_before[2], "source_path_map": expected_before[3]},
        "prediction": predictions, "candidate_slots": _slots(predictions["rows"], "candidate_rank"),
        "promotion_slots": _slots(predictions["rows"], "promotion_rank"),
        "entry_policy_id": scorer.ENTRY_POLICY_ID, "exit_policy_id": scorer.EXIT_POLICY_ID,
        "round_trip_cost_rate": .0045, "shadow_notional_cny": 100000,
        "clock_mode": "HOST_SYSTEM_UTC" if clock is None else "INJECTED_TEST_CLOCK_RESEARCH_ONLY",
        "timestamp_scope": "NEW_RUNNER_PREDICTION_AND_PRE_CAS_CLOCK_NOT_P0_PUBLICATION",
        **BOUNDARIES}
    if existing_raw is not None:
        existing = _json(existing_raw)
        core = _sealed(existing)
        comparable = {k: v for k, v in core.items() if k not in ("prediction_generated_at_utc", "pre_cas_freeze_at_utc")}
        require(scorer.canonical_sha(comparable) == scorer.canonical_sha(payload), "SAME_D_FROZEN_PAYLOAD_CONFLICT")
        old_generated = datetime.fromisoformat(core["prediction_generated_at_utc"])
        old_freeze = datetime.fromisoformat(core["pre_cas_freeze_at_utc"])
        require(old_generated.tzinfo is not None and old_freeze.tzinfo is not None
            and close <= old_generated <= old_freeze < cutoff, "INVALID_EXISTING_FREEZE_WINDOW")
    def end_guard():
        require(_read(evaluation, evaluation_identity) == (evaluation_raw, evaluation_identity), "MODEL_SOURCE_CHANGED")
        _read(REGISTRATION_PATH, plan_identity)
        require(registration() == (plan, plan_sha, plan_identity) and _guard() == code_state, "REGISTRATION_OR_CODE_CHANGED")
        require(source._arguments(source_root, day, expected_p0_sha256, source_path_map) == expected_before,
            "CALLER_SOURCE_ARGUMENTS_CHANGED")
        for origin, expected, original_identity in input_state:
            body, identity = _read(origin, original_identity)
            require(_sha(body) == expected and identity == original_identity, "D_SOURCE_CHANGED_BEFORE_FREEZE")
    end_guard()
    frozen = _now(clock)
    require(frozen >= generated, "CLOCK_MOVED_BACKWARDS")
    if existing_raw is None:
        require(generated < cutoff and frozen < cutoff, "LATE_FIRST_FREEZE_FORBIDDEN")
        payload.update(prediction_generated_at_utc=_stamp(generated), pre_cas_freeze_at_utc=_stamp(frozen))
        payload["snapshot_sha256"] = scorer.canonical_sha(payload)
        written_sha = storage.compare_and_swap(path, payload, None)
        expected_raw = storage.encoded(payload)
    else:
        expected_raw, written_sha = existing_raw, _sha(existing_raw)
    end_guard()
    require(_read(path)[0] == expected_raw and _sha(expected_raw) == written_sha, "PERSISTED_SNAPSHOT_CHANGED")
    completed = _now(clock)
    require(completed >= frozen, "CLOCK_MOVED_BACKWARDS_AFTER_CAS_SNAPSHOT_NOT_ADMITTED")
    fresh = existing_raw is None
    timely = completed < cutoff
    return {"schema_version": "dc20_candidate_natural_local_freeze_receipt_v1",
        "status": ("LOCAL_RESEARCH_SNAPSHOT_FROZEN" if timely else "BLOCKED_POST_CAS_DEADLINE_SNAPSHOT_NOT_ADMITTED")
                   if fresh else "EXISTING_IDENTICAL_SNAPSHOT_REVALIDATED_NO_NEW_ADMISSION",
        "signal_date": day, "snapshot_path": str(path), "snapshot_file_sha256": written_sha,
        "snapshot_sha256": _json(expected_raw)["snapshot_sha256"], "new_snapshot_written": fresh,
        "local_operation_completed_at_utc": _stamp(completed),
        "local_freeze_completed_before_cutoff": fresh and timely,
        "clock_mode": payload["clock_mode"], "independent_natural_time_verified": False,
        "publication_requirement": plan["publication_requirement"], **BOUNDARIES}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--model-evaluation", required=True, type=Path)
    parser.add_argument("--signal-date", required=True)
    parser.add_argument("--p0-hashes", required=True, type=Path)
    parser.add_argument("--source-path-map", type=Path)
    parser.add_argument("--existing-snapshot-sha256")
    args = parser.parse_args(argv)
    # No --now, --activate, --model-sha or registration override exists.
    hashes = _json(_read(args.p0_hashes)[0])
    mapping = None if args.source_path_map is None else _json(_read(args.source_path_map)[0])
    receipt = freeze_natural_day(args.source_root, args.output_root, args.model_evaluation,
        signal_date=args.signal_date, expected_p0_sha256=hashes, source_path_map=mapping,
        expected_existing_snapshot_sha256=args.existing_snapshot_sha256)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 1 if receipt["status"].startswith("BLOCKED_") else 0


if __name__ == "__main__":
    raise SystemExit(main())
