"""Independent per-D contracts and local CAS persistence for the new epoch.

Promotion persistence reads only the immutable epoch manifest and that D's
promotion file. Profit/truth failures, old stats and global latest pointers are
not P0 dependencies. This local store is NOT a Git/Pages publication receipt;
remote main CAS, revision acceptance and a latest pointer remain separate gates.
No legacy ledger is imported, and this module performs no network operations.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, time
from pathlib import Path

from . import storage
from .ledger import (BEIJING, _date, _hash, _json_bytes, _merge_verification,
                     _number, _require_hash, _source_timestamps, _timestamp,
                     _validate_verification)

PROMOTION_FIELDS = {"ts_code", "name", "industry", "stage_transition", "promotion_rank",
                    "promotion_probability", "path_label", "path_change_pct"}
DATE_FIELDS = ("signal_date", "exec_date", "exit_date")


def new_epoch(epoch_id, activated_at_utc=None, start_signal_date=None):
    if not isinstance(epoch_id, str) or not epoch_id.strip():
        raise ValueError("epoch_id must be nonempty")
    if activated_at_utc is not None:
        _timestamp(activated_at_utc)
    if start_signal_date is not None:
        _date(start_signal_date)
    result = {"schema_version": "dc20_forward_epoch_v2", "epoch_id": epoch_id,
              "activated_at_utc": activated_at_utc, "start_signal_date": start_signal_date,
              "legacy_statistics_import_allowed": False, "formal_trade_actions_allowed": False}
    result["epoch_sha256"] = _hash(result)
    return result


def _validate_epoch(epoch, *, active=False):
    if not isinstance(epoch, dict):
        raise ValueError("epoch must be an object")
    expected = new_epoch(epoch.get("epoch_id"), epoch.get("activated_at_utc"), epoch.get("start_signal_date"))
    if epoch != expected:
        raise ValueError("epoch identity or immutable activation was altered")
    if active and epoch["activated_at_utc"] is None:
        raise ValueError("epoch is inactive; no forward day may be admitted")


def _source(source, *, promotion=False):
    if not isinstance(source, dict) or not source:
        raise ValueError("source must contain nonempty immutable evidence")
    hashes = []
    def walk(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if not isinstance(key, str):
                    raise ValueError("source keys must be strings")
                lower = key.lower()
                if promotion and ("profit" in lower or "shadow" in lower or re.search(r"(^|_)p1($|_)", lower)):
                    raise ValueError("promotion source cannot depend on profit/P1/Shadow")
                if "hash" in lower or "sha" in lower:
                    if value is None or value == "" or value == {} or value == []:
                        raise ValueError("empty source hash")
                    if isinstance(value, str):
                        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
                            raise ValueError("source hash must be SHA1/SHA256")
                        hashes.append(value)
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)
    walk(source)
    if not hashes:
        raise ValueError("source has no artifact/model hash binding")
    _json_bytes(source)


def _promotion_rows(rows):
    if not isinstance(rows, list) or len(rows) > 10:
        raise ValueError("promotion must contain zero through ten real candidates")
    codes = set()
    for rank, row in enumerate(rows, 1):
        if not isinstance(row, dict) or set(row) != PROMOTION_FIELDS:
            raise ValueError("promotion rows require only the exact promotion/path fields")
        code = row["ts_code"]
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", code) or code in codes:
            raise ValueError("invalid or duplicate promotion member")
        codes.add(code)
        if not isinstance(row["name"], str) or not row["name"].strip() or not isinstance(row["industry"], str):
            raise ValueError("company name/industry must be strings")
        if not isinstance(row["stage_transition"], str) or row["stage_transition"] not in {"2→3", "3→4"}:
            raise ValueError("promotion candidate must be 2→3 or 3→4")
        if type(row["promotion_rank"]) is not int or row["promotion_rank"] != rank:
            raise ValueError("promotion rank must be ordered and contiguous")
        _number(row["promotion_probability"])
        if not 0 <= row["promotion_probability"] <= 1:
            raise ValueError("promotion probability out of range")
        if row["path_label"] is not None and not isinstance(row["path_label"], str):
            raise ValueError("path label must be string or null")
        _number(row["path_change_pct"], nullable=True)


def _dates(day):
    for field in DATE_FIELDS:
        _date(day.get(field))
    if not day["signal_date"] < day["exec_date"] < day["exit_date"]:
        raise ValueError("D/T/T1 must be strictly increasing")


def _prospective(epoch, payload, now_utc, *, existing=False):
    generated = _timestamp(payload.get("generated_at_utc"))
    now = _timestamp(now_utc)
    if payload.get("generation_mode") != "NATURAL":
        raise ValueError("only NATURAL may enter the forward daybook")
    activated = _timestamp(epoch["activated_at_utc"])
    first = epoch["start_signal_date"] or activated.astimezone(BEIJING).strftime("%Y%m%d")
    if payload["signal_date"] < first:
        raise ValueError("D predates the new epoch start")
    close = datetime.combine(_date(payload["signal_date"]), time(15), BEIJING)
    cutoff = datetime.combine(_date(payload["exec_date"]), time(9, 25), BEIJING)
    if generated < close or generated < activated or now < generated or generated >= cutoff:
        raise ValueError("generation is not prospective D-close to T09:25")
    if any(value >= cutoff or value > generated for value in _source_timestamps(payload["source"])):
        raise ValueError("source evidence is future or beyond the prospective cutoff")
    if not existing and now >= cutoff:
        raise ValueError("late first admission would manufacture a forward record")


def _slots(day, rows, rank_field, limit):
    return [{**{key: day[key] for key in DATE_FIELDS}, "slot": f"Top{row[rank_field]}",
             "ts_code": row["ts_code"], "name": row["name"], rank_field: row[rank_field]}
            for row in rows if row[rank_field] <= limit]


def _sealed(value, sha_field):
    _require_hash(value.get(sha_field))
    excluded = {sha_field}
    if sha_field in {"freeze_sha256", "profit_sha256"}:
        excluded |= {"admitted_at_utc", "record_sha256"}
    payload = {key: item for key, item in value.items() if key not in excluded}
    if _hash(payload) != value[sha_field]:
        raise ValueError("immutable daybook record was modified")


def _storage_seal(value):
    value["record_sha256"] = _hash(value)
    return value


def _validate_storage_seal(value):
    _require_hash(value.get("record_sha256"))
    if _hash({key: item for key, item in value.items() if key != "record_sha256"}) != value["record_sha256"]:
        raise ValueError("stored daybook admission or record was modified")


def _validate_promotion(promotion, epoch=None):
    if not isinstance(promotion, dict) or promotion.get("schema_version") != "dc20_forward_promotion_v2":
        raise ValueError("invalid promotion record")
    allowed = {*DATE_FIELDS, "schema_version", "epoch_id", "epoch_sha256", "generated_at_utc",
               "generation_mode", "source", "rows", "promotion_top3", "freeze_sha256",
               "admitted_at_utc", "record_sha256"}
    if set(promotion) != allowed:
        raise ValueError("promotion record contains unexpected or missing fields")
    _dates(promotion)
    _promotion_rows(promotion.get("rows"))
    _source(promotion.get("source"), promotion=True)
    _require_hash(promotion.get("epoch_sha256"))
    if not isinstance(promotion.get("epoch_id"), str) or not promotion["epoch_id"].strip():
        raise ValueError("promotion epoch identity is missing")
    _sealed(promotion, "freeze_sha256")
    _validate_storage_seal(promotion)
    if promotion.get("promotion_top3") != _slots(promotion, promotion["rows"], "promotion_rank", 3):
        raise ValueError("promotion Top1/2/3 must be frozen in the promotion record")
    admitted = _timestamp(promotion.get("admitted_at_utc"))
    generated = _timestamp(promotion.get("generated_at_utc"))
    cutoff = datetime.combine(_date(promotion["exec_date"]), time(9, 25), BEIJING)
    close = datetime.combine(_date(promotion["signal_date"]), time(15), BEIJING)
    if not close <= generated <= admitted < cutoff or promotion.get("generation_mode") != "NATURAL":
        raise ValueError("promotion admission is not prospective")
    if any(stamp > generated for stamp in _source_timestamps(promotion["source"])):
        raise ValueError("promotion source evidence is from the future")
    if epoch is not None:
        _validate_epoch(epoch, active=True)
        if promotion.get("epoch_id") != epoch["epoch_id"] or promotion["epoch_sha256"] != epoch["epoch_sha256"]:
            raise ValueError("promotion belongs to a different epoch")
        _prospective(epoch, promotion, promotion["admitted_at_utc"])


def freeze_promotion_day(epoch, day, *, open_dates, now_utc, existing=None):
    """Freeze P0 and its Top1/2/3 without reading any profit or truth state."""
    _validate_epoch(epoch, active=True)
    required = {*DATE_FIELDS, "generated_at_utc", "generation_mode", "source", "rows"}
    if not isinstance(day, dict) or set(day) != required:
        raise ValueError("promotion input must contain only the independent day schema")
    _dates(day)
    _promotion_rows(day["rows"])
    _source(day["source"], promotion=True)
    if not isinstance(open_dates, (list, tuple)) or not open_dates:
        raise ValueError("committed SSE dates must be an ordered list")
    for date in open_dates:
        _date(date)
    if list(open_dates) != sorted(set(open_dates)) or day["signal_date"] not in open_dates:
        raise ValueError("SSE dates must be unique ordered sessions and include D")
    offset = open_dates.index(day["signal_date"])
    if list(open_dates[offset:offset + 3]) != [day[key] for key in DATE_FIELDS]:
        raise ValueError("D/T/T1 must be adjacent committed SSE sessions")
    _prospective(epoch, day, now_utc, existing=existing is not None)
    result = {"schema_version": "dc20_forward_promotion_v2", "epoch_id": epoch["epoch_id"],
              "epoch_sha256": epoch["epoch_sha256"], **copy.deepcopy(day)}
    result["promotion_top3"] = _slots(result, result["rows"], "promotion_rank", 3)
    result["freeze_sha256"] = _hash(result)
    if existing is not None:
        _validate_promotion(existing, epoch)
        if existing["signal_date"] != result["signal_date"] or existing["freeze_sha256"] != result["freeze_sha256"]:
            raise ValueError("frozen promotion conflict; never overwrite")
        return copy.deepcopy(existing)
    result["admitted_at_utc"] = now_utc
    return _storage_seal(result)


def _profit_payload(epoch, promotion, profit):
    required = {*DATE_FIELDS, "promotion_freeze_sha256", "generated_at_utc", "generation_mode", "source", "rows"}
    if not isinstance(profit, dict) or set(profit) != required:
        raise ValueError("profit input must have the independent attachment schema")
    if any(profit[key] != promotion[key] for key in DATE_FIELDS) or profit["promotion_freeze_sha256"] != promotion["freeze_sha256"]:
        raise ValueError("profit must bind exact D/T/T1 and promotion SHA")
    _source(profit["source"])
    supplied = profit["rows"]
    members = {row["ts_code"]: row for row in promotion["rows"]}
    if not isinstance(supplied, list) or len(supplied) != len(members):
        raise ValueError("profit must include every real frozen member, not pad/filter")
    seen, ranks, normalized = set(), set(), []
    for row in supplied:
        allowed = {"ts_code", "profit_rank", "profit_score", "name", "promotion_rank"}
        if not isinstance(row, dict) or not {"ts_code", "profit_rank", "profit_score"}.issubset(row) or set(row) - allowed:
            raise ValueError("invalid profit row schema")
        code, rank = row["ts_code"], row["profit_rank"]
        if not isinstance(code, str) or code not in members or code in seen:
            raise ValueError("profit member mismatch")
        if type(rank) is not int or rank in ranks:
            raise ValueError("profit ranks must be an exact permutation")
        seen.add(code)
        ranks.add(rank)
        _number(row["profit_score"])
        if "name" in row and row["name"] != members[code]["name"]:
            raise ValueError("profit cannot rename a frozen company")
        if "promotion_rank" in row and (type(row["promotion_rank"]) is not int or row["promotion_rank"] != members[code]["promotion_rank"]):
            raise ValueError("profit cannot alter promotion rank")
        normalized.append({"ts_code": code, "name": members[code]["name"],
                           "promotion_rank": members[code]["promotion_rank"],
                           "profit_rank": rank, "profit_score": row["profit_score"]})
    if ranks != set(range(1, len(members) + 1)):
        raise ValueError("profit ranks are incomplete")
    normalized.sort(key=lambda row: row["profit_rank"])
    # Equal joint scores retain the hash-bound inference adapter's frozen ranks.
    # The trained engine breaks ties using conditional score, fill score, then
    # stock code. The ledger must not introduce a second, incompatible tie rule.
    if any(left["profit_score"] < right["profit_score"]
           for left, right in zip(normalized, normalized[1:])):
        raise ValueError("profit ranks must descend by score; equal scores retain adapter rank")
    result = {"schema_version": "dc20_forward_profit_v2", "epoch_id": epoch["epoch_id"],
              "epoch_sha256": epoch["epoch_sha256"], **copy.deepcopy(profit), "status": "READY"}
    result["rows"] = normalized
    result["profit_top2"] = _slots(result, normalized, "profit_rank", 2)
    return result


def attach_profit_day(epoch, promotion, profit, *, now_utc, existing=None):
    """Append immutable P1/Top1/2 without changing P0 or adapter tie ranks.

Scores must be nonincreasing. Exact equal-score tie ordering is guaranteed by
the reviewed, hash-bound inference adapter, not recomputed by this ledger.
"""
    _validate_promotion(promotion, epoch)
    result = _profit_payload(epoch, promotion, profit)
    _prospective(epoch, result, now_utc, existing=existing is not None)
    if _timestamp(result["generated_at_utc"]) < _timestamp(promotion["generated_at_utc"]) or _timestamp(now_utc) < _timestamp(promotion["admitted_at_utc"]):
        raise ValueError("profit cannot precede its frozen promotion")
    result["profit_sha256"] = _hash(result)
    if existing is not None:
        _validate_profit(existing, promotion, epoch)
        if existing["profit_sha256"] != result["profit_sha256"]:
            raise ValueError("frozen profit conflict; never overwrite")
        return copy.deepcopy(existing)
    result["admitted_at_utc"] = now_utc
    return _storage_seal(result)


def _validate_profit(profit, promotion, epoch):
    if not isinstance(profit, dict) or profit.get("schema_version") != "dc20_forward_profit_v2":
        raise ValueError("invalid profit record")
    _sealed(profit, "profit_sha256")
    _validate_storage_seal(profit)
    payload = {key: copy.deepcopy(profit[key]) for key in (*DATE_FIELDS, "promotion_freeze_sha256", "generated_at_utc", "generation_mode", "source", "rows") if key in profit}
    expected = _profit_payload(epoch, promotion, payload)
    expected["profit_sha256"] = _hash(expected)
    expected["admitted_at_utc"] = profit.get("admitted_at_utc")
    _storage_seal(expected)
    if expected != profit:
        raise ValueError("profit record or frozen Top1/2 was changed")
    _prospective(epoch, profit, profit["admitted_at_utc"])
    if _timestamp(profit["generated_at_utc"]) < _timestamp(promotion["generated_at_utc"]) or _timestamp(profit["admitted_at_utc"]) < _timestamp(promotion["admitted_at_utc"]):
        raise ValueError("stored profit precedes its promotion")


def _validate_sidecar(promotion, existing):
    if not isinstance(existing, dict) or existing.get("schema_version") != "dc20_forward_truth_v2":
        raise ValueError("invalid truth sidecar")
    if set(existing) != {*DATE_FIELDS, "schema_version", "epoch_id", "epoch_sha256",
                         "freeze_sha256", "verifications", "sidecar_sha256"}:
        raise ValueError("truth sidecar contains unexpected or missing fields")
    _sealed(existing, "sidecar_sha256")
    for key in (*DATE_FIELDS, "epoch_id", "epoch_sha256", "freeze_sha256"):
        if existing.get(key) != promotion[key]:
            raise ValueError("truth sidecar binding mismatch")
    if not isinstance(existing.get("verifications"), list) or not existing["verifications"]:
        raise ValueError("truth sidecar requires at least one verification")
    prior = None
    for current in existing["verifications"]:
        _validate_verification(promotion, current)
        if prior is not None and _merge_verification(promotion, prior, current) != current:
            raise ValueError("truth history erased finalized values")
        prior = current


def record_day_verification(promotion, verification, *, existing=None):
    """Append freeze-bound truth independently of profit and all other D days."""
    _validate_promotion(promotion)
    _validate_verification(promotion, verification)
    if existing is None:
        merged = copy.deepcopy(verification)
        by_code = {row["ts_code"]: row for row in merged["rows"]}
        merged["rows"] = [by_code[row["ts_code"]] for row in promotion["rows"]]
        result = {"schema_version": "dc20_forward_truth_v2",
                  **{key: promotion[key] for key in (*DATE_FIELDS, "epoch_id", "epoch_sha256", "freeze_sha256")},
                  "verifications": [merged]}
    else:
        _validate_sidecar(promotion, existing)
        merged = _merge_verification(promotion, existing["verifications"][-1], verification)
        _validate_verification(promotion, merged)
        if merged == existing["verifications"][-1]:
            return copy.deepcopy(existing)
        result = {key: copy.deepcopy(value) for key, value in existing.items() if key != "sidecar_sha256"}
        result["verifications"].append(merged)
    result["sidecar_sha256"] = _hash(result)
    return result


class Daybook:
    """Local dated-file CAS store, with no global latest or history dependency.

Construct with a reviewed epoch. Existing epoch identity cannot be mutated;
staging/production activations must use separately approved epoch directories.
Only local persistence is guaranteed here, never remote publication readiness.
"""
    def __init__(self, root, epoch):
        _validate_epoch(epoch)
        self.root = Path(root)
        self.epoch = copy.deepcopy(epoch)
        path = self.root / "epoch.json"
        existing, sha = storage.read_state(path)
        if existing is None:
            storage.compare_and_swap(path, self.epoch, sha)
        elif existing != self.epoch:
            raise ValueError("daybook epoch differs; do not relabel or reactivate old data")

    def _check_epoch(self):
        existing, _ = storage.read_state(self.root / "epoch.json")
        if existing != self.epoch:
            raise ValueError("persisted epoch changed or disappeared")
        _validate_epoch(self.epoch, active=True)

    def _path(self, kind, signal_date):
        _date(signal_date)
        return self.root / kind / (signal_date + ".json")

    def freeze_promotion(self, day, *, open_dates, now_utc):
        self._check_epoch()
        if not isinstance(day, dict):
            raise ValueError("promotion day must be an object")
        path = self._path("promotion", day.get("signal_date"))
        existing, sha = storage.read_state(path)
        result = freeze_promotion_day(self.epoch, day, open_dates=open_dates, now_utc=now_utc, existing=existing)
        storage.compare_and_swap(path, result, sha)
        return result

    def _promotion(self, signal_date):
        self._check_epoch()
        promotion, _ = storage.read_state(self._path("promotion", signal_date))
        if promotion is None:
            raise ValueError("profit/truth cannot create an unfrozen D")
        _validate_promotion(promotion, self.epoch)
        if promotion["signal_date"] != signal_date:
            raise ValueError("dated promotion file has a wrong D")
        return promotion

    def attach_profit(self, profit, *, now_utc):
        if not isinstance(profit, dict):
            raise ValueError("profit must be an object")
        signal_date = profit.get("signal_date")
        promotion = self._promotion(signal_date)
        path = self._path("profit", signal_date)
        existing, sha = storage.read_state(path)
        result = attach_profit_day(self.epoch, promotion, profit, now_utc=now_utc, existing=existing)
        storage.compare_and_swap(path, result, sha)
        return result

    def record_verification(self, signal_date, verification):
        promotion = self._promotion(signal_date)
        path = self._path("truth", signal_date)
        existing, sha = storage.read_state(path)
        result = record_day_verification(promotion, verification, existing=existing)
        storage.compare_and_swap(path, result, sha)
        return result

    def read_profit(self, signal_date):
        """None means absent, never a fabricated READY record with zero slots."""
        promotion = self._promotion(signal_date)
        profit, _ = storage.read_state(self._path("profit", signal_date))
        if profit is not None:
            _validate_profit(profit, promotion, self.epoch)
        return profit
