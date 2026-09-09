"""Forward-only immutable selections and additive, freeze-bound verification.

This module does no I/O. Callers persist its returned copy atomically; a failed
validation leaves the original ledger untouched. It never imports legacy stats.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

SCHEMA_VERSION = "dc20_forward_ledger_v1"
BEIJING = ZoneInfo("Asia/Shanghai")
T_FINAL = {"PROMOTED", "NOT_PROMOTED"}
T_STATES = T_FINAL | {"PENDING", "MISSING"}
T1_FINAL = {"SETTLED", "NO_FILL"}
T1_STATES = T1_FINAL | {"PENDING", "MISSING", "EXIT_BLOCKED"}


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{8}", value):
        raise ValueError("dates must be YYYYMMDD strings")
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise ValueError("invalid calendar date") from exc


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be a timezone-aware ISO string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("naive timestamp is forbidden")
    return parsed.astimezone(timezone.utc)


def _number(value, *, nullable=False):
    if value is None and nullable:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("number must be finite, not a boolean")
    try:
        finite = math.isfinite(value)
    except OverflowError as exc:
        raise ValueError("number exceeds supported finite range") from exc
    if not finite:
        raise ValueError("number must be finite, not a boolean")


def _json_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False,
                          sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("ledger inputs must be finite JSON values") from exc


def _hash(value):
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _require_hash(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("freeze SHA must be lowercase SHA256")


def freeze_day_sha256(day):
    """Digest a frozen payload, excluding storage-only hash/validation fields."""
    if not isinstance(day, dict):
        raise ValueError("day must be an object")
    return _hash({key: value for key, value in day.items()
                  if key not in {"freeze_sha256", "verifications", "admitted_at_utc"}})


def new_ledger(epoch_id, activated_at_utc=None, start_signal_date=None):
    if not isinstance(epoch_id, str) or not epoch_id.strip():
        raise ValueError("epoch_id must be a nonempty string")
    if activated_at_utc is not None:
        _timestamp(activated_at_utc)
    if start_signal_date is not None:
        _date(start_signal_date)
    return {"schema_version": SCHEMA_VERSION, "epoch_id": epoch_id,
            "activated_at_utc": activated_at_utc,
            "start_signal_date": start_signal_date, "days": []}


def _validate_day(day):
    if not isinstance(day, dict):
        raise ValueError("day must be an object")
    for key in ("signal_date", "exec_date", "exit_date"):
        _date(day.get(key))
    if not day["signal_date"] < day["exec_date"] < day["exit_date"]:
        raise ValueError("D/T/T1 must be increasing")
    _timestamp(day.get("generated_at_utc"))
    if not isinstance(day.get("generation_mode"), str) or day["generation_mode"] not in {"NATURAL", "REPLAY"}:
        raise ValueError("unrecognized generation mode")
    source = day.get("source")
    if not isinstance(source, dict) or not source:
        raise ValueError("nonempty source evidence is required")
    _json_bytes(source)
    # Provenance key names are adapter-specific, but empty hashes cannot bind it.
    hash_values = []
    def check_hashes(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if not isinstance(key, str):
                    raise ValueError("source keys must be strings")
                if "hash" in key.lower() or "sha" in key.lower():
                    if value is None or value == "" or value == {} or value == []:
                        raise ValueError("source hash evidence cannot be empty")
                    if isinstance(value, str):
                        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
                            raise ValueError("source hash evidence must be SHA1 or SHA256")
                        hash_values.append(value)
                check_hashes(value)
        elif isinstance(obj, list):
            for value in obj:
                check_hashes(value)
    check_hashes(source)
    if not hash_values:
        raise ValueError("source must bind at least one nonempty artifact or model hash")
    rows = day.get("rows")
    if not isinstance(rows, list) or len(rows) > 10:
        raise ValueError("rows must contain zero through ten real candidates")
    codes, profit_ranks = set(), set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError("candidate must be an object")
        code = row.get("ts_code")
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", code) or code in codes:
            raise ValueError("candidate codes must be valid and unique")
        codes.add(code)
        for field in ("name", "industry"):
            if not isinstance(row.get(field), str) or (field == "name" and not row[field].strip()):
                raise ValueError("candidate name and industry must be strings")
        if not isinstance(row.get("stage_transition"), str) or row["stage_transition"] not in {"2→3", "3→4"}:
            raise ValueError("candidate must be 2→3 or 3→4")
        rank = row.get("promotion_rank")
        if type(rank) is not int or rank != index:
            raise ValueError("promotion ordering must be contiguous and unchanged")
        profit_rank = row.get("profit_rank")
        if type(profit_rank) is not int or profit_rank in profit_ranks:
            raise ValueError("profit ranking must be a permutation")
        profit_ranks.add(profit_rank)
        _number(row.get("promotion_probability"))
        if not 0 <= row["promotion_probability"] <= 1:
            raise ValueError("promotion probability out of range")
        _number(row.get("profit_score"))
        if "path_change_pct" not in row or "path_label" not in row:
            raise ValueError("path fields must be explicit, including missing values")
        _number(row["path_change_pct"], nullable=True)
        if row["path_label"] is not None and not isinstance(row["path_label"], str):
            raise ValueError("path label must be a string or null")
    if profit_ranks != set(range(1, len(rows) + 1)):
        raise ValueError("profit ranking must have the exact same members")
    _json_bytes(day)


def _validate_ledger(ledger):
    if not isinstance(ledger, dict) or ledger.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported ledger schema")
    new_ledger(ledger.get("epoch_id"), ledger.get("activated_at_utc"), ledger.get("start_signal_date"))
    if not isinstance(ledger.get("days"), list):
        raise ValueError("ledger days must be a list")
    previous = None
    for day in ledger["days"]:
        _validate_day(day)
        if ledger["activated_at_utc"] is None or day["generation_mode"] != "NATURAL":
            raise ValueError("unactivated/replay ledger must never contain forward days")
        if _timestamp(day["generated_at_utc"]) < _timestamp(ledger["activated_at_utc"]):
            raise ValueError("frozen generation predates epoch activation")
        admitted = _timestamp(day.get("admitted_at_utc"))
        cutoff = datetime.combine(_date(day["exec_date"]), time(9, 25), BEIJING)
        if admitted < _timestamp(day["generated_at_utc"]) or admitted >= cutoff:
            raise ValueError("stored admission must be prospective and after generation")
        _require_hash(day.get("freeze_sha256"))
        if freeze_day_sha256(day) != day["freeze_sha256"]:
            raise ValueError("frozen selection was modified")
        if previous is not None and day["signal_date"] <= previous:
            raise ValueError("ledger days must be unique and chronological")
        if ledger["start_signal_date"] is None or day["signal_date"] < ledger["start_signal_date"]:
            raise ValueError("day precedes the epoch start")
        previous = day["signal_date"]
        if not isinstance(day.get("verifications"), list):
            raise ValueError("verifications must be an append-only list")
        prior = None
        for verification in day["verifications"]:
            _validate_verification(day, verification)
            if prior is not None:
                merged = _merge_verification(day, prior, verification)
                if merged != verification:
                    raise ValueError("historical verification erased known truth")
            prior = verification
    _json_bytes(ledger)


def _source_timestamps(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            lower = key.lower()
            if (lower.endswith(("_at", "_at_utc", "_timestamp")) or lower == "timestamp") and value is not None:
                yield _timestamp(value)
            else:
                yield from _source_timestamps(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _source_timestamps(value)


def freeze_day(ledger, day, *, open_dates, now_utc):
    _validate_ledger(ledger)
    _validate_day(day)
    if ledger["activated_at_utc"] is None:
        raise ValueError("epoch is inactive; explicit activation is required")
    if day["generation_mode"] != "NATURAL":
        raise ValueError("REPLAY cannot be admitted to forward statistics")
    if not isinstance(open_dates, (list, tuple, set, frozenset)):
        raise ValueError("open_dates must be a collection of committed SSE dates")
    dates = list(open_dates)
    for value in dates:
        _date(value)
    if len(dates) != len(set(dates)):
        raise ValueError("duplicate SSE dates")
    dates.sort()
    try:
        pos = dates.index(day["signal_date"])
    except ValueError as exc:
        raise ValueError("D is not a committed SSE open date") from exc
    if dates[pos:pos + 3] != [day["signal_date"], day["exec_date"], day["exit_date"]]:
        raise ValueError("D/T/T1 are not consecutive committed SSE sessions")
    now = _timestamp(now_utc)
    generated = _timestamp(day["generated_at_utc"])
    activated = _timestamp(ledger["activated_at_utc"])
    close = datetime.combine(_date(day["signal_date"]), time(15, 0), BEIJING)
    cutoff = datetime.combine(_date(day["exec_date"]), time(9, 25), BEIJING)
    if generated < close or generated < activated or now < generated:
        raise ValueError("generation must follow D close and activation, not future")
    if generated >= cutoff or any(stamp >= cutoff for stamp in _source_timestamps(day["source"])):
        raise ValueError("generation/source exceeds prospective T 09:25 cutoff")
    digest = freeze_day_sha256(day)
    if "freeze_sha256" in day and day["freeze_sha256"] != digest:
        raise ValueError("provided freeze SHA does not match payload")
    for existing in ledger["days"]:
        if existing["signal_date"] == day["signal_date"]:
            if existing["freeze_sha256"] != digest:
                raise ValueError("existing D is frozen and cannot be replaced")
            return copy.deepcopy(ledger)
    if "admitted_at_utc" in day:
        raise ValueError("caller cannot supply a first-admission timestamp")
    if "verifications" in day and day["verifications"]:
        raise ValueError("cannot import previous verifications")
    if now >= cutoff:
        raise ValueError("late first admission would manufacture a forward selection")
    if ledger["start_signal_date"] is not None and day["signal_date"] < ledger["start_signal_date"]:
        raise ValueError("cannot admit a D before the epoch start")
    if ledger["days"] and day["signal_date"] < ledger["days"][-1]["signal_date"]:
        raise ValueError("cannot backfill earlier D selections")
    result = copy.deepcopy(ledger)
    if result["start_signal_date"] is None:
        result["start_signal_date"] = day["signal_date"]
    frozen = {key: copy.deepcopy(value) for key, value in day.items()
              if key not in {"freeze_sha256", "verifications", "admitted_at_utc"}}
    frozen.update(freeze_sha256=digest, verifications=[], admitted_at_utc=now_utc)
    result["days"].append(frozen)
    return result


def _validate_verification(day, verification):
    if not isinstance(verification, dict):
        raise ValueError("verification must be an object")
    for field in ("signal_date", "exec_date", "exit_date", "freeze_sha256"):
        if verification.get(field) != day[field]:
            raise ValueError("verification is not bound to this frozen day")
    _date(verification.get("as_of_date"))
    if verification["as_of_date"] < day["signal_date"]:
        raise ValueError("verification as-of date predates D")
    if verification.get("members_sha256") != _hash([row["ts_code"] for row in day["rows"]]):
        raise ValueError("verification member SHA does not match frozen order")
    _number(verification.get("costs_bps"))
    if verification["costs_bps"] < 0:
        raise ValueError("costs must be nonnegative")
    if verification.get("price_basis") != "daily_open_proxy" or verification.get("research_only") is not True:
        raise ValueError("verification must identify its non-trading proxy methodology")
    rows = verification.get("rows")
    if not isinstance(rows, list) or len(rows) != len(day["rows"]):
        raise ValueError("verification must contain the exact frozen members")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("ts_code"), str) or row["ts_code"] in seen:
            raise ValueError("duplicate or malformed truth row")
        seen.add(row.get("ts_code"))
        if not isinstance(row.get("t_status"), str) or not isinstance(row.get("t1_status"), str) or row["t_status"] not in T_STATES or row["t1_status"] not in T1_STATES:
            raise ValueError("unknown verification state")
        _validate_truth_evidence(day, verification, row)
        for field in ("net_return", "slot_return", "entry_price", "exit_price"):
            if field not in row:
                raise ValueError("truth values must explicitly distinguish missing")
            _number(row[field], nullable=True)
        for field in ("entry_price", "exit_price"):
            if row[field] is not None and row[field] <= 0:
                raise ValueError("truth prices must be positive")
        if "actual_exit_date" not in row:
            raise ValueError("actual exit date must be explicit")
        if row["t_status"] in T_FINAL and verification["as_of_date"] < day["exec_date"]:
            raise ValueError("cannot verify T before T")
        if row["t_status"] == "PENDING" and verification["as_of_date"] >= day["exec_date"]:
            raise ValueError("due but unavailable T truth must be MISSING, not PENDING")
        status = row["t1_status"]
        if status == "PENDING" and verification["as_of_date"] >= day["exit_date"]:
            raise ValueError("due but unavailable T1 truth must be MISSING, not PENDING")
        if status in T1_FINAL | {"EXIT_BLOCKED"} and verification["as_of_date"] < day["exit_date"]:
            raise ValueError("cannot resolve T1 before T1")
        if status in T1_FINAL and row["t_status"] not in T_FINAL:
            raise ValueError("resolved T1 requires resolved T truth")
        if status == "NO_FILL":
            if row["net_return"] is not None or row["slot_return"] != 0 or row["entry_price"] is not None or row["exit_price"] is not None or row["actual_exit_date"] is not None:
                raise ValueError("NO_FILL has no trade return or prices; only slot zero")
        elif status == "SETTLED":
            if row["net_return"] is None or row["slot_return"] != row["net_return"] or row["entry_price"] is None or row["exit_price"] is None:
                raise ValueError("settled trade requires entry, exit and identical net/slot return")
            _date(row["actual_exit_date"])
            if not day["exit_date"] <= row["actual_exit_date"] <= verification["as_of_date"]:
                raise ValueError("actual exit must be on/after T1 and not future")
            expected = row["exit_price"] / row["entry_price"] - 1 - verification["costs_bps"] / 10000
            if not math.isclose(row["net_return"], expected, rel_tol=1e-12, abs_tol=1e-12):
                raise ValueError("settled net return does not include the declared costs")
        elif row["net_return"] is not None or row["slot_return"] is not None or row["exit_price"] is not None or row["actual_exit_date"] is not None:
            raise ValueError("unresolved returns and exits must remain null")
        _validate_corporate_evidence(day, verification, row)
    if seen != {row["ts_code"] for row in day["rows"]}:
        raise ValueError("verification has different frozen members")
    _json_bytes(verification)


def _validate_truth_evidence(day, verification, row):
    t_evidence, t1_evidence = row.get("t_evidence"), row.get("t1_evidence")
    if not isinstance(t_evidence, list) or len(t_evidence) > 1 or not isinstance(t1_evidence, list):
        raise ValueError("T and T1 evidence must be explicit lists")
    if row.get("truth_evidence") != t_evidence + t1_evidence:
        raise ValueError("truth evidence must preserve T and T1 lineage")
    for group, evidence_list in (("T", t_evidence), ("T1", t1_evidence)):
        prior = None
        for evidence in evidence_list:
            if not isinstance(evidence, dict):
                raise ValueError("truth evidence must be an object")
            date = evidence.get("trade_date")
            _date(date)
            if evidence.get("ts_code") != row["ts_code"] or date > verification["as_of_date"]:
                raise ValueError("truth evidence has wrong member or future date")
            if group == "T" and date != day["exec_date"]:
                raise ValueError("T evidence must be exact T")
            if group == "T1" and (date < day["exit_date"] or (prior is not None and date <= prior)):
                raise ValueError("T1 evidence must be chronological, on/after T1")
            prior = date
            status = evidence.get("status")
            if status == "OBSERVED":
                _require_hash(evidence.get("partition_sha256"))
                _require_hash(evidence.get("row_sha256"))
            elif status == "MISSING_ROW":
                _require_hash(evidence.get("partition_sha256"))
                if evidence.get("row_sha256") is not None:
                    raise ValueError("missing row cannot carry observed row hash")
            elif status == "MISSING_PARTITION":
                if evidence.get("partition_sha256") is not None or evidence.get("row_sha256") is not None:
                    raise ValueError("missing partition cannot carry observed hashes")
            else:
                raise ValueError("unknown truth evidence status")
    if row["t_status"] in T_FINAL and (len(t_evidence) != 1 or t_evidence[0]["status"] != "OBSERVED"):
        raise ValueError("final T requires observed exact-T hash evidence")
    if row["t1_status"] == "SETTLED":
        if not t1_evidence or any(item["status"] != "OBSERVED" for item in t1_evidence):
            raise ValueError("settled T1 requires observed exit-session hash evidence")
        if t1_evidence[-1]["trade_date"] != row.get("actual_exit_date"):
            raise ValueError("settled exit evidence does not match actual exit date")
    if row["t1_status"] == "NO_FILL" and t1_evidence:
        raise ValueError("NO_FILL must not manufacture exit-session evidence")
    if row["t1_status"] == "EXIT_BLOCKED" and (not t1_evidence or any(item["status"] != "OBSERVED" for item in t1_evidence)):
        raise ValueError("blocked exit needs observed sessions, not missing data")


def _validate_corporate_evidence(day, verification, row):
    assurance = row.get("corporate_action_evidence")
    if assurance is not None:
        required = {"ts_code", "entry_date", "through_date", "basis", "source_sha256", "evidence_sha256"}
        if not isinstance(assurance, dict) or set(assurance) != required:
            raise ValueError("corporate action evidence schema is invalid")
        _date(assurance["entry_date"])
        _date(assurance["through_date"])
        if assurance["ts_code"] != row["ts_code"] or assurance["entry_date"] != day["exec_date"] or not day["exec_date"] <= assurance["through_date"] <= verification["as_of_date"]:
            raise ValueError("corporate action evidence date/member binding mismatch")
        if assurance["basis"] != "NO_CORPORATE_ACTION_IN_WINDOW":
            raise ValueError("unsupported corporate action adjustment basis")
        _require_hash(assurance["source_sha256"])
        _require_hash(assurance["evidence_sha256"])
        body = {key: value for key, value in assurance.items() if key != "evidence_sha256"}
        if assurance["evidence_sha256"] != _hash(body):
            raise ValueError("corporate action evidence hash mismatch")
    blocker = row.get("settlement_blocker")
    if blocker is not None and (blocker != "CORPORATE_ACTION_REVIEW_REQUIRED" or row["t1_status"] != "MISSING"):
        raise ValueError("corporate review blocker must leave settlement MISSING")
    if row["t1_status"] == "SETTLED" and (assurance is None or assurance["through_date"] < row["actual_exit_date"] or blocker is not None):
        raise ValueError("settlement requires corporate action evidence covering actual exit")


def _merge_verification(day, previous, incoming):
    if incoming["as_of_date"] < previous["as_of_date"]:
        raise ValueError("verification as-of date cannot move backwards")
    for key in ("costs_bps", "price_basis", "research_only", "members_sha256"):
        if key in previous and incoming.get(key) != previous[key]:
            raise ValueError("verification methodology or member binding cannot change")
    result = copy.deepcopy(incoming)
    old_by_code = {row["ts_code"]: row for row in previous["rows"]}
    new_by_code = {row["ts_code"]: row for row in incoming["rows"]}
    merged_rows = []
    for candidate in day["rows"]:
        old, new = old_by_code[candidate["ts_code"]], copy.deepcopy(new_by_code[candidate["ts_code"]])
        if old["t_status"] in T_FINAL:
            if new["t_status"] in T_FINAL and old["t_status"] != new["t_status"]:
                raise ValueError("contradictory finalized T promotion truth")
            if new["t_status"] in T_FINAL and old["t_evidence"][0]["row_sha256"] != new["t_evidence"][0]["row_sha256"]:
                raise ValueError("contradictory finalized T source row")
            new["t_status"] = old["t_status"]
            for key, value in old.items():
                if key.startswith("t_") and key != "t_status":
                    new[key] = copy.deepcopy(value)
        if old["t1_status"] in T1_FINAL:
            if new["t1_status"] in T1_FINAL:
                fields = ("t1_status", "net_return", "slot_return", "entry_price", "exit_price", "actual_exit_date")
                if any(new.get(key) != old.get(key) for key in fields):
                    raise ValueError("contradictory settled T1 truth")
            # Preserve all resolved trade evidence, including adapter-specific hashes.
            preserved = copy.deepcopy(old)
            for key, value in new.items():
                if key.startswith("t_"):
                    preserved[key] = value
            new = preserved
        if "t_evidence" in new and "t1_evidence" in new:
            new["truth_evidence"] = copy.deepcopy(new["t_evidence"] + new["t1_evidence"])
        merged_rows.append(new)
    result["rows"] = merged_rows
    return result


def record_verification(ledger, signal_date, verification):
    _validate_ledger(ledger)
    _date(signal_date)
    matches = [day for day in ledger["days"] if day["signal_date"] == signal_date]
    if len(matches) != 1:
        raise ValueError("verification cannot create an unfrozen D")
    day = matches[0]
    _validate_verification(day, verification)
    if day["verifications"]:
        merged = _merge_verification(day, day["verifications"][-1], verification)
        if merged == day["verifications"][-1]:
            return copy.deepcopy(ledger)
    else:
        merged = copy.deepcopy(verification)
        by_code = {row["ts_code"]: row for row in merged["rows"]}
        merged["rows"] = [by_code[row["ts_code"]] for row in day["rows"]]
    _validate_verification(day, merged)
    result = copy.deepcopy(ledger)
    target = next(item for item in result["days"] if item["signal_date"] == signal_date)
    target["verifications"].append(merged)
    return result
