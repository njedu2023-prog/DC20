"""Strict v1/v2 snapshot reader. No publication, trade or migration authority.

The schema selects an exact reviewed profile, never a permissive fallback.
Original bytes and original promotion universe are retained.
"""
from datetime import datetime
from pathlib import Path
import hashlib

ROOT = Path(__file__).absolute().parents[2]
PINS = {
    "candidate_natural_forward.py": "5a3967c88829be0e6b9a0d7c384b6d2cf12ac7c257bac4dff40d1a15ce2d5c15",
    "candidate_eligible_forward.py": "9177b183813626de51affaf6f9f17a0aed6c557b50a56018023fe8a62f28ea2c",
}

def require(ok, reason):
    if not ok: raise ValueError(reason)

def _sha(raw): return hashlib.sha256(raw).hexdigest()

def guard():
    for name, sha in PINS.items():
        path = ROOT / "work/profit_1000_upgrade" / name
        require(not any(p.is_symlink() for p in (path,*path.parents))
            and path.stat().st_nlink == 1 and _sha(path.read_bytes()) == sha,
            "SNAPSHOT_PROFILE_SOURCE_CHANGED:"+name)

guard()
from work.profit_1000_upgrade import candidate_natural_forward as natural
from work.profit_1000_upgrade import candidate_eligible_forward as eligible
import re
REGISTRATION_SHA = "2b24d549c16b5ad6bb3c682eb491a56907ccd7cd986c4c2c9e494a76f4d79de2"
LEGACY_RUNNER_SHA = PINS["candidate_natural_forward.py"]

def timestamp(value):
    require(type(value) is str, "AWARE_TIMESTAMP_REQUIRED")
    result = datetime.fromisoformat(value.replace("Z","+00:00"))
    require(result.tzinfo is not None, "AWARE_TIMESTAMP_REQUIRED")
    return result

def _snapshot(raw, expected):
    natural.scorer._sha(expected)
    require(_sha(raw) == expected, "EXTERNAL_FROZEN_SNAPSHOT_SHA_MISMATCH")
    value = natural._json(raw)
    natural._sealed(value)
    for key, wanted in {"schema_version": natural.SCHEMA, "registration_id": natural.REGISTRATION_ID,
            "registration_sha256": REGISTRATION_SHA, "runner_sha256": LEGACY_RUNNER_SHA,
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
    generated = timestamp(value["prediction_generated_at_utc"])
    frozen = timestamp(value["pre_cas_freeze_at_utc"])
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


def validate_snapshot(raw, expected_sha256):
    guard()
    value = natural._json(raw)
    schema = value.get("schema_version")
    if schema == natural.SCHEMA:
        natural._guard()
        result = _snapshot(raw, expected_sha256)
    elif schema == eligible.SCHEMA:
        result = eligible.validate_snapshot(raw, expected_sha256)
    else:
        raise ValueError("UNREGISTERED_SNAPSHOT_SCHEMA")
    guard()
    return result


def outcome_rows(snapshot):
    """Original promotion comparator may be ineligible for profit scoring."""
    if snapshot["schema_version"] == eligible.SCHEMA:
        return snapshot["prediction"]["promotion_rows"]
    require(snapshot["schema_version"] == natural.SCHEMA, "UNREGISTERED_SNAPSHOT_SCHEMA")
    return snapshot["prediction"]["rows"]

