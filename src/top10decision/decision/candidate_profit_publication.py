"""Explicit formal presentation of independently frozen fixed-model rankings.

The unchanged research registration does not authorize a production switch.
The separate user-approved activation document authorizes this adapter only.
It never scores, fits, modifies promotion ranks, changes historical snapshots,
settles outcomes, or interprets absence as a zero return. A private, live-issuer
publication proof is required to create each new formal day. Cached public JSON
validation is deliberately a structural integrity check, not a proof issuer.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[3]
ACTIVATION_PATH = ROOT / "models/decision_candidate_profit_activation_v1.json"
ACTIVATION_SCHEMA = "dc20_candidate_formal_profit_activation_v1"
DAY_SCHEMA = "dc20_candidate_formal_profit_day_v1"
INDEX_SCHEMA = "dc20_candidate_formal_profit_index_v1"
ACTIVATION_ID = "dc20_profit_ridge_999666_auction_exit1000_v1"
MODEL_SHA = "999666791b147e4d120ba9b7e10d9d1fc846ba171efbba73b2488ca56ce6f589"
EFFECTIVE_DATE = "20260914"
PUBLIC_PREFIX = "outputs/decision/candidate_profit_v1/"
SNAPSHOT_PREFIX = "work/profit_1000_upgrade/candidate_natural_forward/"
SHANGHAI = timezone(timedelta(hours=8))
MAX_JSON_BYTES = 4_000_000
IDENTITY_KEYS = ("enabled", "activation_id", "model_canonical_sha256", "effective_from_signal_date")
EXPECTED_ACTIVATION = {
    "schema_version": ACTIVATION_SCHEMA,
    "enabled": True,
    "activation_id": ACTIVATION_ID,
    "model_canonical_sha256": MODEL_SHA,
    "effective_from_signal_date": EFFECTIVE_DATE,
    "authorization_scope": "FORMAL_PROFIT_RANKING_AND_VERSIONED_SHADOW_FORWARD_VALIDATION",
    "authorization_date": "20260914",
    "entry_policy_id": "research_canonical_price_capacity_split_no_cap_v3",
    "exit_policy_id": "dc20_exit_1000_limit_hold_20260912_v1",
    "round_trip_cost_rate": .0045,
    "shadow_notional_cny": 100000,
    "promotion_model_replacement_allowed": False,
    "historical_reranking_allowed": False,
    "negative_score_skip_allowed": False,
    "missing_or_no_fill_is_profit": False,
    "profitability_improvement_proven": False,
    "real_order_execution_allowed": False,
    "legacy_statistics_merge_allowed": False,
    "rollback_mode": "EXPLICIT_DISABLE_NEW_PUBLICATION_RETAIN_EXISTING_FROZEN_DAYS",
}


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode()


def _json(raw):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_JSON_BYTES, "BOUNDED_JSON_BYTES_REQUIRED")
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    def invalid(_):
        raise ValueError("NONFINITE_JSON_NUMBER")
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    require(type(value) is dict, "JSON_OBJECT_REQUIRED")
    return value


def _digest(value, length=64):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{" + str(length) + r"}", value),
            "EXACT_SHA_REQUIRED")
    return value


def _date(value):
    require(type(value) is str and re.fullmatch(r"20[0-9]{6}", value), "EXACT_SIGNAL_DATE_REQUIRED")
    require(datetime.strptime(value, "%Y%m%d").strftime("%Y%m%d") == value, "INVALID_SIGNAL_DATE")
    return value


def _time(value):
    require(type(value) is str, "AWARE_TIMESTAMP_REQUIRED")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "AWARE_TIMESTAMP_REQUIRED")
    return parsed.astimezone(timezone.utc)


def _now():
    return datetime.now(timezone.utc)


def validate_activation(value):
    require(type(value) is dict and set(value) == set(EXPECTED_ACTIVATION), "EXACT_ACTIVATION_DOCUMENT_REQUIRED")
    for key, expected in EXPECTED_ACTIVATION.items():
        if key == "enabled":
            require(type(value[key]) is bool, "EXPLICIT_ENABLE_OR_DISABLE_REQUIRED")
        else:
            require(type(value[key]) is type(expected) and value[key] == expected,
                    "ACTIVATION_CONTRACT_CHANGED:" + key)
    return deepcopy(value)


def load_activation(path=None):
    target = ACTIVATION_PATH if path is None else Path(path)
    require(target.is_file() and not any(p.is_symlink() for p in (target, *target.parents)),
            "REGULAR_ACTIVATION_DOCUMENT_REQUIRED")
    return validate_activation(_json(target.read_bytes()))


def activation_identity(activation):
    checked = validate_activation(activation)
    return {key: checked[key] for key in IDENTITY_KEYS}


def _dependencies():
    # These original modules remain research-only and frozen. Their own guards
    # check the complete unchanged source/model/label chain.
    pins = {
        "candidate_natural_outcomes": "5949be11309eebba1a3d5f45be9b56d4469b1d6e51f2c416960c9920699d7c18",
        "candidate_natural_evidence_publication": "ddfcd8932032d9e64c2be3577d60b74bc6d67f070d77727b79e83bced8c20bbb",
    }
    for name, expected in pins.items():
        path = ROOT / "work/profit_1000_upgrade" / (name + ".py")
        require(path.is_file() and path.stat().st_nlink == 1
                and not any(p.is_symlink() for p in (path, *path.parents))
                and sha(path.read_bytes()) == expected, "FROZEN_PUBLICATION_DEPENDENCY_REQUIRED:" + name)
    from work.profit_1000_upgrade import candidate_natural_outcomes as outcomes
    from work.profit_1000_upgrade import candidate_natural_evidence_publication as issuer
    for name, module in zip(pins, (outcomes, issuer)):
        require(Path(module.__file__).absolute() == ROOT / "work/profit_1000_upgrade" / (name + ".py"),
                "EXACT_PUBLICATION_DEPENDENCY_ORIGIN_REQUIRED")
    return outcomes, issuer


def _p0(raw, snapshot):
    day = snapshot["signal_date"]
    wanted_sha = snapshot["input_arguments"]["expected_p0_sha256"]["three_rank_json"]
    require(sha(raw) == _digest(wanted_sha), "SAME_D_ORIGINAL_P0_BYTES_REQUIRED")
    path = f"outputs/decision/three_rank_top10_{day}.json"
    bindings = [b for b in snapshot["D_source_evidence"]["source_file_bindings"]
                if b.get("receipt_path") == path]
    require(len(bindings) == 1 and bindings[0]["sha256"] == wanted_sha
            and type(bindings[0]["bytes"]) is int and bindings[0]["bytes"] == len(raw),
            "EXACT_P0_SOURCE_BINDING_REQUIRED")
    p0 = _json(raw)
    require(p0.get("schema_version") == "decision_three_rank_top10_v1"
            and p0.get("signal_date") == day and p0.get("exec_date") == snapshot["exec_date"]
            and p0.get("exit_date") == snapshot["exit_date"]
            and p0.get("membership_authority") == "promotion_probability_engine_only"
            and p0.get("downstream_scope") == "exact_frozen_promotion_top10",
            "EXACT_FROZEN_PROMOTION_P0_REQUIRED")
    rows = p0.get("rows")
    require(type(rows) is list and len(rows) == len(snapshot["prediction"]["rows"])
            and type(p0.get("top10_count")) is int and p0["top10_count"] == len(rows),
            "EXACT_P0_COHORT_SIZE_REQUIRED")
    mapping = {}
    for row in rows:
        require(type(row) is dict and type(row.get("ts_code")) is str
                and re.fullmatch(r"[0-9]{6}\.(SH|SZ)", row["ts_code"])
                and row["ts_code"] not in mapping, "UNIQUE_P0_IDENTITIES_REQUIRED")
        require(type(row.get("promotion_rank")) is int, "EXACT_P0_PROMOTION_RANK_REQUIRED")
        mapping[row["ts_code"]] = row
    predicted = snapshot["prediction"]["rows"]
    require(set(mapping) == {r["ts_code"] for r in predicted}, "P0_PREDICTION_MEMBER_MISMATCH")
    for row in predicted:
        require(mapping[row["ts_code"]]["promotion_rank"] == row["promotion_rank"],
                "FROZEN_PROMOTION_RANK_CHANGED")
    return p0, mapping


def project_verified_day(*, snapshot_raw, publication_proof, current_p0_raw, activation, source_main_sha):
    """Build a new immutable day; only the actual issuer can supply admission.

    This generates bytes, not a Git publication acknowledgement. The caller
    must recheck the same T09:20 safety boundary immediately before Git CAS.
    Existing formal days must be read and validated, never reprojected late.
    """
    config = validate_activation(activation)
    require(config["enabled"], "ACTIVATION_DISABLED")
    _digest(source_main_sha, 40)
    outcomes, issuer = _dependencies()
    require(type(publication_proof) is issuer.VerifiedResearchPublication,
            "REAL_PRIVATE_PUBLICATION_PROOF_REQUIRED")
    publication_proof.assert_unchanged()
    proof_report = publication_proof.report
    require(proof_report.get("status") == "INDEPENDENT_OBSERVER_RESEARCH_PUBLICATION_OBSERVED"
            and proof_report.get("research_prospective_publication_observed") is True
            and proof_report.get("injected_client_for_test") is False,
            "ACTUAL_PROSPECTIVE_PUBLICATION_REQUIRED")
    snapshot_sha = _digest(publication_proof.snapshot_file_sha256)
    require(type(snapshot_raw) is bytes and sha(snapshot_raw) == snapshot_sha,
            "ORIGINAL_OBSERVED_SNAPSHOT_BYTES_REQUIRED")
    snapshot = outcomes._snapshot(snapshot_raw, snapshot_sha)
    require(snapshot["clock_mode"] == "HOST_SYSTEM_UTC", "SYNTHETIC_CLOCK_CANNOT_ACTIVATE")
    day = _date(snapshot["signal_date"])
    require(day >= config["effective_from_signal_date"] and publication_proof.signal_date == day,
            "ACTIVATION_SAME_D_REQUIRED")
    require(snapshot["model_canonical_sha256"] == config["model_canonical_sha256"], "ACTIVATED_MODEL_MISMATCH")
    p0, mapping = _p0(current_p0_raw, snapshot)
    close, cutoff = outcomes.natural._window(day, snapshot["exec_date"])
    generated = _now()
    require(type(generated) is datetime and generated.tzinfo is not None
            and close <= generated < cutoff - timedelta(minutes=5),
            "NEW_FORMAL_DAY_REQUIRES_PRE_T0920_PUBLICATION_WINDOW")
    rows = []
    for row in snapshot["prediction"]["rows"]:
        stock = mapping[row["ts_code"]]
        rows.append({key: row[key] for key in ("ts_code", "candidate_rank", "candidate_score", "promotion_rank")}
                    | {"name": stock.get("name", ""), "industry": stock.get("industry", "")})
    result = {
        "schema_version": DAY_SCHEMA, "status": "FROZEN_FORMAL_PROFIT_FORWARD_VALIDATION",
        **{k: config[k] for k in IDENTITY_KEYS if k != "enabled"},
        "signal_date": day, "exec_date": snapshot["exec_date"], "exit_date": snapshot["exit_date"],
        "source_main_sha": source_main_sha, "snapshot_file_sha256": snapshot_sha,
        "snapshot_source": {"path": SNAPSHOT_PREFIX + "day_" + day + ".json", "sha256": snapshot_sha},
        "p0_file_sha256": sha(current_p0_raw),
        "p0_source": {"path": f"outputs/decision/three_rank_top10_{day}.json", "sha256": sha(current_p0_raw)},
        "prediction_generated_at_utc": snapshot["prediction_generated_at_utc"],
        "pre_cas_freeze_at_utc": snapshot["pre_cas_freeze_at_utc"],
        "projection_generated_at_utc": generated.astimezone(timezone.utc).isoformat(),
        "formal_publication_deadline_utc": (cutoff - timedelta(minutes=5)).isoformat(),
        "rows": rows, "candidate_count": len(rows),
        "candidate_slots": deepcopy(snapshot["candidate_slots"]),
        "promotion_slots": deepcopy(snapshot["promotion_slots"]),
        "entry_policy_id": config["entry_policy_id"], "exit_policy_id": config["exit_policy_id"],
        "round_trip_cost_rate": config["round_trip_cost_rate"], "shadow_notional_cny": config["shadow_notional_cny"],
        "provenance": {key: getattr(publication_proof, key) for key in
                       ("observer_run_id", "evidence_commit", "evidence_manifest_sha256", "publication_observation_sha256")},
        "promotion_ranking_unchanged": True, "historical_reranking_performed": False,
        "model_retrained": False, "score_is_probability": False, "profitability_improvement_proven": False,
        "actual_execution_claimed": False, "actual_capacity_verified": False,
        "source_price_observation_only": True,
    }
    publication_proof.assert_unchanged()
    require(proof_report == publication_proof.report and config == activation, "PROOF_OR_ACTIVATION_CHANGED")
    validate_public_day(encoded(result), expected_sha256=sha(encoded(result)), activation=config)
    return result


def validate_public_day(raw, *, expected_sha256, activation):
    """Validate externally SHA-bound public bytes, without issuing authority."""
    config = validate_activation(activation)
    require(type(raw) is bytes and sha(raw) == _digest(expected_sha256), "PUBLIC_DAY_EXTERNAL_SHA_MISMATCH")
    day = _json(raw)
    require(day.get("schema_version") == DAY_SCHEMA
            and day.get("status") == "FROZEN_FORMAL_PROFIT_FORWARD_VALIDATION", "PUBLIC_DAY_SCHEMA_REQUIRED")
    for key in IDENTITY_KEYS[1:]:
        require(day.get(key) == config[key], "PUBLIC_DAY_ACTIVATION_IDENTITY_CHANGED")
    signal = _date(day.get("signal_date")); execution = _date(day.get("exec_date")); exit_day = _date(day.get("exit_date"))
    require(config["effective_from_signal_date"] <= signal < execution < exit_day, "PUBLIC_DAY_DATE_ORDER_CHANGED")
    _digest(day.get("source_main_sha"), 40)
    for key, prefix in (("snapshot", SNAPSHOT_PREFIX + "day_"), ("p0", "outputs/decision/three_rank_top10_")):
        digest = _digest(day.get(key + "_file_sha256"))
        require(day.get(key + "_source") == {"path": prefix + signal + ".json", "sha256": digest},
                "PUBLIC_DAY_SOURCE_BINDING_CHANGED")
    rows = day.get("rows")
    require(type(rows) is list and 0 <= len(rows) <= 10 and type(day.get("candidate_count")) is int
            and day["candidate_count"] == len(rows), "PUBLIC_DAY_COHORT_SIZE_CHANGED")
    codes, ranks = set(), set()
    for rank, row in enumerate(rows, 1):
        require(type(row) is dict and set(row) == {"ts_code", "candidate_rank", "candidate_score", "promotion_rank", "name", "industry"},
                "EXACT_PUBLIC_ROW_REQUIRED")
        require(type(row["ts_code"]) is str and re.fullmatch(r"[0-9]{6}\.(SH|SZ)", row["ts_code"])
                and row["ts_code"] not in codes, "UNIQUE_PUBLIC_STOCK_REQUIRED")
        codes.add(row["ts_code"])
        require(type(row["candidate_rank"]) is int and row["candidate_rank"] == rank
                and type(row["promotion_rank"]) is int, "EXACT_PUBLIC_FROZEN_RANK_REQUIRED")
        ranks.add(row["promotion_rank"])
        require(type(row["candidate_score"]) in (int, float) and math.isfinite(row["candidate_score"]), "FINITE_PUBLIC_SCORE_REQUIRED")
        require(type(row["name"]) is str and len(row["name"]) <= 100
                and type(row["industry"]) is str and len(row["industry"]) <= 100, "BOUNDED_PUBLIC_LABEL_REQUIRED")
    require(ranks == set(range(1, len(rows) + 1)) and rows == sorted(rows, key=lambda r: (-r["candidate_score"], r["ts_code"])),
            "PUBLIC_RANK_ORDER_CHANGED")
    for field, rank_key in (("candidate_slots", "candidate_rank"), ("promotion_slots", "promotion_rank")):
        slots = day.get(field)
        require(type(slots) is list and len(slots) == 2, "EXACT_TWO_PUBLIC_SLOTS_REQUIRED")
        ordered = sorted(rows, key=lambda r: r[rank_key])
        for i, slot in enumerate(slots, 1):
            require(type(slot) is dict and type(slot.get("slot")) is int and slot["slot"] == i, "EXACT_PUBLIC_SLOT_REQUIRED")
            if i <= len(ordered):
                expected = ordered[i - 1]
                require(slot.get("status") == "PENDING_T" and all(slot.get(k) == expected[k]
                        and type(slot.get(k)) is type(expected[k]) for k in ("ts_code", "candidate_rank", "candidate_score", "promotion_rank")),
                        "PUBLIC_TOP_SLOT_MEMBERSHIP_CHANGED")
                require(type(slot.get("board_stage")) is int and slot["board_stage"] in (2, 3), "PUBLIC_SLOT_STAGE_CHANGED")
            else:
                require(slot.get("status") == "MISSING_CANDIDATE" and all(slot.get(k) is None for k in
                        ("ts_code", "board_stage", "candidate_rank", "candidate_score", "promotion_rank")),
                        "MISSING_PUBLIC_SLOT_MUST_REMAIN_MISSING")
            require(all(slot.get(k) is None for k in ("proxy_fill", "net_return", "slot_net_return")),
                    "FROZEN_PUBLIC_DAY_CANNOT_CONTAIN_OUTCOMES")
    for key in ("entry_policy_id", "exit_policy_id", "round_trip_cost_rate", "shadow_notional_cny"):
        require(type(day.get(key)) is type(config[key]) and day[key] == config[key], "PUBLIC_POLICY_CHANGED")
    for key, wanted in {"promotion_ranking_unchanged": True, "historical_reranking_performed": False,
                        "model_retrained": False, "score_is_probability": False, "profitability_improvement_proven": False,
                        "actual_execution_claimed": False, "actual_capacity_verified": False, "source_price_observation_only": True}.items():
        require(day.get(key) is wanted, "PUBLIC_DAY_QUALIFICATION_CHANGED:" + key)
    provenance = day.get("provenance")
    require(type(provenance) is dict and type(provenance.get("observer_run_id")) is int
            and provenance["observer_run_id"] > 0, "OBSERVER_PROVENANCE_REQUIRED")
    _digest(provenance.get("evidence_commit"), 40)
    _digest(provenance.get("evidence_manifest_sha256")); _digest(provenance.get("publication_observation_sha256"))
    close = datetime.strptime(signal + "150000", "%Y%m%d%H%M%S").replace(tzinfo=SHANGHAI)
    cutoff = datetime.strptime(execution + "092500", "%Y%m%d%H%M%S").replace(tzinfo=SHANGHAI)
    require(close <= _time(day.get("prediction_generated_at_utc")) <= _time(day.get("pre_cas_freeze_at_utc"))
            <= _time(day.get("projection_generated_at_utc")) < cutoff - timedelta(minutes=5)
            and _time(day.get("formal_publication_deadline_utc")) == cutoff - timedelta(minutes=5),
            "PUBLIC_DAY_PROSPECTIVE_WINDOW_CHANGED")
    return day


def validate_persisted_day(raw, *, expected_sha256, snapshot_raw, current_p0_raw, activation):
    """Check persisted day/snapshot/P0 consistency; issue NO source admission.

    The publisher must independently require the exact day bytes in its current
    Git parent tree. Reusing already settled accounting additionally requires
    the original SHA-bound public summary's two immutable terminal (or genuinely
    absent candidate) slots. This function alone authorizes neither new rankings
    nor new prices/outcomes. It avoids expiring an old completed record merely
    because the observer's temporary ACK artifact was later garbage-collected.
    """
    config = validate_activation(activation)
    day = validate_public_day(raw, expected_sha256=expected_sha256, activation=config)
    outcomes, _ = _dependencies()
    require(type(snapshot_raw) is bytes and sha(snapshot_raw) == day["snapshot_file_sha256"],
            "CACHED_DAY_ORIGINAL_SNAPSHOT_MISMATCH")
    snapshot = outcomes._snapshot(snapshot_raw, day["snapshot_file_sha256"])
    require(snapshot["clock_mode"] == "HOST_SYSTEM_UTC"
            and snapshot["signal_date"] == day["signal_date"]
            and snapshot["exec_date"] == day["exec_date"] and snapshot["exit_date"] == day["exit_date"],
            "CACHED_DAY_FROZEN_DATE_OR_CLOCK_CHANGED")
    _, mapping = _p0(current_p0_raw, snapshot)
    require(sha(current_p0_raw) == day["p0_file_sha256"], "CACHED_DAY_P0_MISMATCH")
    wanted_rows = [{k: row[k] for k in ("ts_code", "candidate_rank", "candidate_score", "promotion_rank")}
                   | {"name": mapping[row["ts_code"]].get("name", ""), "industry": mapping[row["ts_code"]].get("industry", "")}
                   for row in snapshot["prediction"]["rows"]]
    require(encoded({"rows": day["rows"]}) == encoded({"rows": wanted_rows}), "CACHED_DAY_FROZEN_RANK_OR_LABEL_CHANGED")
    for field in ("candidate_slots", "promotion_slots", "prediction_generated_at_utc", "pre_cas_freeze_at_utc"):
        require(encoded({field: day[field]}) == encoded({field: snapshot[field]}), "CACHED_DAY_FROZEN_FIELD_CHANGED:" + field)
    require(config == activation, "ACTIVATION_CHANGED")
    return day


def validate_cached_day(raw, *, expected_sha256, snapshot_raw, current_p0_raw, publication_proof, activation):
    """Rebind existing immutable bytes to the still-live original observer proof.

    Unsettled days use this path. It never assigns new prediction/publication
    times or excuses late first publication. Existing Git membership remains
    the publisher's responsibility; a caller dict cannot grant admission.
    """
    config = validate_activation(activation)
    _, issuer = _dependencies()
    require(type(publication_proof) is issuer.VerifiedResearchPublication,
            "REAL_PRIVATE_PUBLICATION_PROOF_REQUIRED")
    publication_proof.assert_unchanged()
    report = publication_proof.report
    require(report.get("status") == "INDEPENDENT_OBSERVER_RESEARCH_PUBLICATION_OBSERVED"
            and report.get("research_prospective_publication_observed") is True
            and report.get("injected_client_for_test") is False, "ACTUAL_PROSPECTIVE_PUBLICATION_REQUIRED")
    day = validate_persisted_day(raw, expected_sha256=expected_sha256, snapshot_raw=snapshot_raw,
                                 current_p0_raw=current_p0_raw, activation=config)
    require(day["snapshot_file_sha256"] == publication_proof.snapshot_file_sha256
            and publication_proof.signal_date == day["signal_date"], "CACHED_DAY_ORIGINAL_SNAPSHOT_MISMATCH")
    wanted_provenance = {key: getattr(publication_proof, key) for key in
                         ("observer_run_id", "evidence_commit", "evidence_manifest_sha256", "publication_observation_sha256")}
    require(day["provenance"] == wanted_provenance, "CACHED_DAY_ORIGINAL_PROVENANCE_CHANGED")
    publication_proof.assert_unchanged()
    require(report == publication_proof.report and config == activation, "PROOF_OR_ACTIVATION_CHANGED")
    return day


def build_public_index(days, *, activation, source_main_sha, generated_at_utc):
    config = validate_activation(activation)
    _digest(source_main_sha, 40); generated = _time(generated_at_utc)
    require(type(days) is list and len(days) <= 4096, "BOUNDED_PUBLIC_DAY_LIST_REQUIRED")
    checked, seen = [], set()
    for value in days:
        raw = encoded(value)
        day = validate_public_day(raw, expected_sha256=sha(raw), activation=config)
        signal = day["signal_date"]
        require(signal not in seen and _time(day["projection_generated_at_utc"]) <= generated,
                "DUPLICATE_OR_FUTURE_PUBLIC_DAY")
        seen.add(signal)
        checked.append({"signal_date": signal, "path": PUBLIC_PREFIX + "day_" + signal + ".json", "sha256": sha(raw),
                        "snapshot_file_sha256": day["snapshot_file_sha256"], "p0_file_sha256": day["p0_file_sha256"]})
    checked.sort(key=lambda item: item["signal_date"])
    identity = activation_identity(config)
    return {"schema_version": INDEX_SCHEMA, **identity, "activation": identity,
            "status": "DISABLED" if not config["enabled"] else "ACTIVE_FORWARD_VALIDATION" if checked else "ACTIVE_WAITING_FIRST_NATURAL_D",
            "source_main_sha": source_main_sha, "generated_at_utc": generated_at_utc,
            "days": checked, "latest_signal_date": checked[-1]["signal_date"] if checked else None,
            "profitability_improvement_proven": False, "old_model_statistics_merged": False,
            "actual_execution_claimed": False}


def validate_public_index(raw, *, expected_sha256, activation):
    require(type(raw) is bytes and sha(raw) == _digest(expected_sha256), "PUBLIC_INDEX_EXTERNAL_SHA_MISMATCH")
    value = _json(raw); identity = activation_identity(activation)
    require(value.get("schema_version") == INDEX_SCHEMA and value.get("activation") == identity
            and all(value.get(k) == v and type(value.get(k)) is type(v) for k, v in identity.items()),
            "PUBLIC_INDEX_ACTIVATION_CHANGED")
    _digest(value.get("source_main_sha"), 40); _time(value.get("generated_at_utc"))
    days = value.get("days")
    require(type(days) is list and len(days) <= 4096, "BOUNDED_PUBLIC_INDEX_REQUIRED")
    seen = []
    for day in days:
        require(type(day) is dict and set(day) == {"signal_date", "path", "sha256", "snapshot_file_sha256", "p0_file_sha256"},
                "EXACT_PUBLIC_INDEX_ENTRY_REQUIRED")
        signal = _date(day["signal_date"])
        require(signal >= identity["effective_from_signal_date"] and day["path"] == PUBLIC_PREFIX + "day_" + signal + ".json",
                "PUBLIC_INDEX_PATH_IDENTITY_CHANGED")
        for key in ("sha256", "snapshot_file_sha256", "p0_file_sha256"):
            _digest(day[key])
        seen.append(signal)
    require(seen == sorted(set(seen)) and value.get("latest_signal_date") == (seen[-1] if seen else None),
            "PUBLIC_INDEX_DAY_ORDER_CHANGED")
    wanted = "DISABLED" if not identity["enabled"] else "ACTIVE_FORWARD_VALIDATION" if days else "ACTIVE_WAITING_FIRST_NATURAL_D"
    require(value.get("status") == wanted, "PUBLIC_INDEX_STATUS_CHANGED")
    require(all(value.get(k) is False for k in ("profitability_improvement_proven", "old_model_statistics_merged", "actual_execution_claimed")),
            "PUBLIC_INDEX_QUALIFICATION_CHANGED")
    return value
